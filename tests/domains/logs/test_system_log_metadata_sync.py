from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from tests.support.v6_records import native_record

from datacoolie_studio.db.models import EnvironmentSource, LogFileManifest
from datacoolie_studio.db.session import create_session
from datacoolie_studio.domains.logs import ingestion, staging
from datacoolie_studio.domains.storage.adapters import (
    LocalStorageAdapter,
    StorageObject,
    StorageRevision,
)
from datacoolie_studio.domains.storage.inventory import StorageInventory


class ListingAdapter:
    provider = "s3"

    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects
        self.version = 1
        self.downloads: list[str] = []
        self.listings = 0

    def inventory(self, request):
        self.listings += 1
        prefix = request.uri.rstrip("/") + "/"
        files = tuple(
            StorageObject(
                canonical_uri=uri, name=uri.rsplit("/", 1)[-1],
                object_type="file", size=len(content),
                last_modified=datetime(2026, 10, 10, tzinfo=timezone.utc),
                provider_revision=str(self.version),
            )
            for uri, content in self.objects.items()
            if uri.startswith(prefix)
            and (request.recursive or "/" not in uri[len(prefix):])
            and (not request.name_prefix or uri.rsplit("/", 1)[-1].startswith(request.name_prefix))
            and (not request.suffixes or any(uri.endswith(suffix) for suffix in request.suffixes))
        )
        return StorageInventory(
            objects=files, completeness="complete", requests=1, pages=1,
            directories_visited=1, objects_inspected=len(files),
            matching_objects=len(files), retries=0, throttles=0,
            bytes_read=0, duration_ms=0,
        )

    def stat(self, uri):
        return StorageRevision(
            canonical_uri=uri, size=len(self.objects[uri]),
            last_modified=datetime(2026, 10, 10, tzinfo=timezone.utc),
            provider_revision=str(self.version),
        )

    def canonical_uri(self, uri):
        return uri

    def open_read(self, uri):
        raise AssertionError(f"Sync opened remote contents: {uri}")

    def materialize(self, uri, target, expected_revision=None):
        assert "/system_logs/" not in uri, "Sync downloaded system contents"
        self.downloads.append(uri)
        target.write_bytes(self.objects[uri])
        return self.stat(uri)


@pytest.mark.parametrize("provider", ["local", "s3"])
@pytest.mark.parametrize("mixed", [False, True])
def test_sync_lists_system_files_without_reading_or_staging_contents(
    studio_project_factory, tmp_path, monkeypatch, provider, mixed,
) -> None:
    project = studio_project_factory()
    root = tmp_path / "logs"
    base = root.as_posix() if provider == "local" else "s3://bucket/logs"
    system_uri = f"{base}/system_logs/system_20261010_010203_1_0_job-1.json"
    part_uri = f"{base}/system_logs/system_20261010_010203_1_0_job-1_part_00000001.json"
    opaque_uri = f"{base}/system_logs/system_opaque.json"
    job_uri = f"{base}/execution_logs/job_run_log/jobs.json"
    # Invalid contents are intentional: sync must collect these filenames
    # without admission, irrespective of schema or file shape.
    objects = {system_uri: b"invalid JSON", part_uri: b"", opaque_uri: b"not a log"}
    if mixed:
        objects[job_uri] = (json.dumps(native_record({
            "_type": "job_run_log", "job_id": "job-1",
        })) + "\n").encode()
    adapter = ListingAdapter(objects)
    if provider == "local":
        for uri, content in objects.items():
            path = Path(uri)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        original_open = Path.open

        def checked_open(path, *args, **kwargs):
            assert "system_logs" not in Path(path).parts, "Sync opened local system contents"
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(Path, "open", checked_open)
        monkeypatch.setattr(LocalStorageAdapter, "open_read", adapter.open_read)
    else:
        monkeypatch.setattr(ingestion, "create_storage_adapter", lambda *args, **kwargs: adapter)
    if not mixed:
        def fail_staging(*args, **kwargs):
            raise AssertionError("System-only sync created staging")

        monkeypatch.setattr(staging, "create_staging_dir", fail_staging)

    with create_session() as session:
        source = EnvironmentSource(
            environment_id=project.environment_id, source_kind="logs",
            uri=base, storage_provider=provider,
            storage_auth_mode="none" if provider == "local" else "ambient",
        )
        session.add(source)
        session.commit()
        options = {"database_path_override": tmp_path / "analytics.duckdb"}
        first = ingestion.refresh_log_source_cache(session, source, **options)
        assert first["sync_execution"]["state"] == "succeeded", first
        rows = list(session.scalars(select(LogFileManifest).where(
            LogFileManifest.source_id == source.id,
            LogFileManifest.file_kind == "system_json",
        ).order_by(LogFileManifest.id)))
        assert len(rows) == 3
        assert {row.filename_job_id for row in rows} == {"job-1", "job-1_part_00000001", None}
        assert all(row.row_count == 0 and row.admission_summary_json is None for row in rows)
        assert all(row.job_id is None and row.log_session_id is None for row in rows)
        assert all(row.event_min_timestamp is None and row.event_max_timestamp is None for row in rows)
        before = [(row.id, row.last_seen_at, row.revision_json) for row in rows]
        second = ingestion.refresh_log_source_cache(session, source, **options)
        assert second["sync_execution"]["state"] == "succeeded", second
        session.expire_all()
        assert [(row.id, row.last_seen_at, row.revision_json) for row in rows] == before
        if mixed:
            assert session.scalar(select(LogFileManifest.row_count).where(
                LogFileManifest.source_id == source.id,
                LogFileManifest.file_kind == "job_json",
            )) == 1
        if provider == "s3":
            assert adapter.listings > 0
            assert adapter.downloads == ([job_uri] if mixed else [])
            measured = next(row for row in rows if row.filename_job_id == "job-1")
            measured.row_count = 99
            measured.job_id = "old-content-job"
            measured.log_session_id = "old-session"
            measured.admission_summary_json = '{"accepted": 99}'
            session.commit()
            adapter.version += 1
            adapter.objects.pop(opaque_uri)
            adapter.objects[f"{base}/system_logs/system_20261010_020304_1_0_new-job.json"] = b"invalid new contents"
            changed = ingestion.refresh_log_source_cache(session, source, **options)
            assert changed["sync_execution"]["state"] == "succeeded", changed
            session.expire_all()
            current = list(session.scalars(select(LogFileManifest).where(
                LogFileManifest.source_id == source.id,
                LogFileManifest.file_kind == "system_json",
            )))
            assert {row.filename_job_id for row in current} == {"job-1", "job-1_part_00000001", "new-job"}
            assert all(row.row_count == 0 and row.job_id is None for row in current)
            assert all(row.log_session_id is None and row.admission_summary_json is None for row in current)
            assert adapter.downloads == ([job_uri, job_uri] if mixed else [])
