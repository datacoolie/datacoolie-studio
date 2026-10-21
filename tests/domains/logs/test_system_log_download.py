from __future__ import annotations

import asyncio
import json
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path

import pytest
from starlette.requests import ClientDisconnect

from datacoolie_studio.db.models import EnvironmentSource, LogFileManifest
from datacoolie_studio.db.session import create_session, refresh_workspace_reads
from datacoolie_studio.api.v1.system_log_download import (
    PreparedSystemLogStreamingResponse,
)
from datacoolie_studio.domains.logs.file_state import revision_json
from datacoolie_studio.domains.logs.system_log_download import (
    DownloadBusyError,
    DownloadAdmissionError,
    DownloadRevisionConflictError,
    DownloadStorageError,
    DownloadTooLargeError,
    prepare_system_log_download,
)
from datacoolie_studio.domains.storage.adapters import (
    LocalStorageAdapter,
    StorageRevision,
)
from tests.support.v6_records import native_record


def _row(
    *,
    job_id: str,
    session_id: str | None,
    message: str,
    dataflow_id: str | None = None,
) -> dict[str, object]:
    values: dict[str, object] = {
        "log_schema_version": 6,
        "_type": "system_log",
        "job_id": job_id,
        "log_session_id": session_id,
        "msg": message,
        "ts": "2026-10-04T12:00:00+00:00",
    }
    if dataflow_id is not None:
        values["dataflow_id"] = dataflow_id
    return native_record(values)


def _write(path: Path, lines: list[bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(lines))


def _line(
    row: dict[str, object], *, newline: bytes = b"\n", ensure_ascii: bool = True
) -> bytes:
    return (
        json.dumps(row, ensure_ascii=ensure_ascii, separators=(",", ":")).encode()
        + newline
    )


def _manifest(
    session, source: EnvironmentSource, path: Path, *, event_min: int
) -> None:
    revision = LocalStorageAdapter().stat(str(path))
    session.add(
        LogFileManifest(
            source_id=source.id,
            file_uri=revision.canonical_uri,
            file_kind="system_json",
            revision_json=revision_json(revision),
            row_count=len(path.read_bytes().splitlines()),
            status="ok",
            event_min_timestamp=datetime(
                2026, 10, 4, 12, event_min, tzinfo=timezone.utc
            ),
        )
    )


def test_download_preserves_original_bytes_mixed_jobs_children_duplicates_and_order(
    studio_paths,
    studio_project_factory,
    monkeypatch,
):
    project = studio_project_factory()
    first = studio_paths.data_dir / "source-a" / "first-job-1.json"
    second = studio_paths.data_dir / "source-b" / "second-job-1.json"
    target_one = _row(job_id="job-1", session_id="session-a", message="one")
    target_child = _row(
        job_id="job-1",
        session_id="session-a",
        message="child",
        dataflow_id="child-flow",
    )
    target_duplicate = _row(job_id="job-1", session_id="session-a", message="one")
    unrelated = _row(job_id="job-2", session_id="session-a", message="other")
    eof = _row(job_id="job-1", session_id="session-a", message="unicode-é")
    first_bytes = (
        _line(target_one, newline=b"\r\n")
        + _line(unrelated)
        + _line(target_child, newline=b"\r\n")
    )
    second_bytes = _line(target_duplicate, newline=b"\n") + _line(
        eof, newline=b"", ensure_ascii=False
    )
    _write(first, [first_bytes])
    _write(second, [second_bytes])

    with create_session() as session:
        source_a = EnvironmentSource(
            environment_id=project.environment_id,
            source_kind="logs",
            uri=str(first.parent),
            storage_provider="local",
        )
        source_b = EnvironmentSource(
            environment_id=project.environment_id,
            source_kind="logs",
            uri=str(second.parent),
            storage_provider="local",
        )
        session.add_all([source_a, source_b])
        session.flush()
        _manifest(session, source_a, first, event_min=1)
        _manifest(session, source_b, second, event_min=2)
        session.commit()

        prepared = prepare_system_log_download(
            session,
            [source_a, source_b],
            job_id="job-1",
            log_session_id="session-a",
            log_session_scope="exact",
        )
        try:
            payload = b"".join(prepared.iter_chunks(7))
        finally:
            prepared.close()

    assert payload == (
        _line(target_one, newline=b"\r\n")
        + _line(target_child, newline=b"\r\n")
        + _line(target_duplicate)
        + _line(eof, newline=b"", ensure_ascii=False)
        + b"\n"
    )
    assert payload.count(_line(target_one, newline=b"\r\n")) == 1
    assert prepared.filename.startswith("job-job-1-")
    monkeypatch.setattr(
        "datacoolie_studio.domains.logs.system_log_download.MANIFEST_PAGE_SIZE",
        1,
    )


def test_download_exact_empty_and_null_sessions_are_distinct(
    studio_paths,
    studio_project_factory,
):
    project = studio_project_factory()
    path = studio_paths.data_dir / "system-job-1.json"
    empty = _row(job_id="job-1", session_id="", message="empty")
    null = _row(job_id="job-1", session_id=None, message="null")
    _write(path, [_line(empty), _line(null)])
    with create_session() as session:
        source = EnvironmentSource(
            environment_id=project.environment_id,
            source_kind="logs",
            uri=str(path.parent),
            storage_provider="local",
        )
        session.add(source)
        session.flush()
        _manifest(session, source, path, event_min=1)
        session.commit()
        exact = prepare_system_log_download(
            session,
            [source],
            job_id="job-1",
            log_session_id="",
            log_session_scope="exact",
        )
        null_scope = prepare_system_log_download(
            session,
            [source],
            job_id="job-1",
            log_session_scope="null",
        )
        try:
            assert b"empty" in b"".join(exact.iter_chunks())
            assert b"null" in b"".join(null_scope.iter_chunks())
        finally:
            exact.close()
            null_scope.close()


def test_download_validates_unrelated_records_and_is_atomic_on_failure(
    studio_paths,
    studio_project_factory,
):
    project = studio_project_factory()
    path = studio_paths.data_dir / "system-job-1.json"
    valid = _row(job_id="job-1", session_id="session-a", message="selected")
    unsupported = _row(job_id="unrelated", session_id="session-b", message="old")
    unsupported["log_schema_version"] = 5
    _write(path, [_line(valid), _line(unsupported)])
    with create_session() as session:
        source = EnvironmentSource(
            environment_id=project.environment_id,
            source_kind="logs",
            uri=str(path.parent),
            storage_provider="local",
        )
        session.add(source)
        session.flush()
        _manifest(session, source, path, event_min=1)
        session.commit()
        with pytest.raises(DownloadAdmissionError, match="Unsupported schema"):
            prepare_system_log_download(
                session,
                [source],
                job_id="job-1",
                log_session_scope="all",
            )


def test_download_rejects_non_utf8_json_before_exporting_bytes(
    studio_paths,
    studio_project_factory,
):
    project = studio_project_factory()
    path = studio_paths.data_dir / "utf16-job-utf16.json"
    row = _row(job_id="job-utf16", session_id=None, message="bad encoding")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(json.dumps(row, separators=(",", ":")).encode("utf-16"))
    with create_session() as session:
        source = EnvironmentSource(
            environment_id=project.environment_id,
            source_kind="logs",
            uri=str(path.parent),
            storage_provider="local",
        )
        session.add(source)
        session.flush()
        _manifest(session, source, path, event_min=1)
        session.commit()
        with pytest.raises(DownloadAdmissionError, match="Invalid JSON"):
            prepare_system_log_download(session, [source], job_id="job-utf16")


def test_download_line_and_output_bounds_are_enforced(
    studio_paths,
    studio_project_factory,
    monkeypatch,
):
    project = studio_project_factory()
    path = studio_paths.data_dir / "system-job-1.json"
    row = _row(job_id="job-1", session_id="session-a", message="x" * 100)
    _write(path, [_line(row)])
    with create_session() as session:
        source = EnvironmentSource(
            environment_id=project.environment_id,
            source_kind="logs",
            uri=str(path.parent),
            storage_provider="local",
        )
        session.add(source)
        session.flush()
        _manifest(session, source, path, event_min=1)
        session.commit()
        monkeypatch.setattr(
            "datacoolie_studio.domains.logs.system_log_download.MAX_LINE_BYTES",
            32,
        )
        with pytest.raises(DownloadTooLargeError, match="exceeds"):
            prepare_system_log_download(session, [source], job_id="job-1")


def test_download_output_bound_fails_before_spool_write(
    studio_paths,
    studio_project_factory,
    monkeypatch,
):
    project = studio_project_factory()
    path = studio_paths.data_dir / "system-job-1.json"
    raw = _line(_row(job_id="job-1", session_id=None, message="output"))
    _write(path, [raw])
    with create_session() as session:
        source = EnvironmentSource(
            environment_id=project.environment_id,
            source_kind="logs",
            uri=str(path.parent),
            storage_provider="local",
        )
        session.add(source)
        session.flush()
        _manifest(session, source, path, event_min=1)
        session.commit()
        monkeypatch.setattr(
            "datacoolie_studio.domains.logs.system_log_download.MAX_OUTPUT_BYTES",
            len(raw) - 1,
        )
        with pytest.raises(DownloadTooLargeError, match="download limit"):
            prepare_system_log_download(session, [source], job_id="job-1")


def test_download_spool_write_failure_releases_lease(
    studio_paths,
    studio_project_factory,
    monkeypatch,
):
    project = studio_project_factory()
    path = studio_paths.data_dir / "system-job-1.json"
    _write(path, [_line(_row(job_id="job-1", session_id=None, message="write"))])
    with create_session() as session:
        source = EnvironmentSource(
            environment_id=project.environment_id,
            source_kind="logs",
            uri=str(path.parent),
            storage_provider="local",
        )
        session.add(source)
        session.flush()
        _manifest(session, source, path, event_min=1)
        session.commit()

        def fail_write(*_args, **_kwargs):
            raise OSError("spool write unavailable")

        with monkeypatch.context() as scoped:
            scoped.setattr(
                "datacoolie_studio.domains.logs.system_log_download._spool_write",
                fail_write,
            )
            with pytest.raises(DownloadStorageError, match="spool write unavailable"):
                prepare_system_log_download(session, [source], job_id="job-1")

        prepared = prepare_system_log_download(session, [source], job_id="retry")
        prepared.close()


def test_download_has_no_preview_page_limit_for_large_duplicate_stream(
    studio_paths,
    studio_project_factory,
):
    project = studio_project_factory()
    path = studio_paths.data_dir / "large-job-1.json"
    row = _row(job_id="job-1", session_id="session-a", message="duplicate")
    raw = _line(row)
    _write(path, [raw] * 5001)
    with create_session() as session:
        source = EnvironmentSource(
            environment_id=project.environment_id,
            source_kind="logs",
            uri=str(path.parent),
            storage_provider="local",
        )
        session.add(source)
        session.flush()
        _manifest(session, source, path, event_min=1)
        session.commit()
        prepared = prepare_system_log_download(
            session,
            [source],
            job_id="job-1",
            log_session_scope="exact",
            log_session_id="session-a",
        )
        try:
            payload = b"".join(prepared.iter_chunks())
        finally:
            prepared.close()
    assert payload == raw * 5001


def test_download_endpoint_uses_attachment_and_structured_error_contract(
    studio_client,
    studio_paths,
    studio_project_factory,
):
    project = studio_project_factory()
    path = studio_paths.data_dir / "system-job%2Funsafe.json"
    _write(path, [_line(_row(job_id="job/unsafe", session_id=None, message="ready"))])
    with create_session() as session:
        source = EnvironmentSource(
            environment_id=project.environment_id,
            source_kind="logs",
            uri=str(path.parent),
            storage_provider="local",
        )
        session.add(source)
        session.flush()
        source_id = source.id
        _manifest(session, source, path, event_min=1)
        session.commit()

    endpoint = (
        f"/api/v1/environments/{project.environment_id}/monitoring/system-logs/download"
    )
    response = studio_client.get(
        endpoint,
        params={
            "job_id": "job/unsafe",
            "source_id": source_id,
            "log_session_scope": "null",
        },
        headers={"accept-encoding": "identity"},
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert "attachment" in response.headers["content-disposition"]
    assert "job-job-unsafe-system-log.jsonl" in response.headers["content-disposition"]
    assert response.headers["content-length"] == str(len(path.read_bytes()))
    assert response.headers["cache-control"] == "no-store"
    assert response.content == path.read_bytes()

    missing_session = studio_client.get(
        endpoint,
        params={"job_id": "job/unsafe", "log_session_scope": "exact"},
    )
    assert missing_session.status_code == 422
    assert missing_session.json()["detail"] == {
        "code": "exact_session_required",
        "message": "Exact session scope requires log_session_id",
    }


def test_download_lease_covers_preparation_and_streaming_lifetime(
    studio_paths,
    studio_project_factory,
):
    project = studio_project_factory()
    with create_session() as session:
        source = EnvironmentSource(
            environment_id=project.environment_id,
            source_kind="logs",
            uri=str(studio_paths.data_dir),
            storage_provider="local",
        )
        session.add(source)
        session.flush()
        first = prepare_system_log_download(session, [source], job_id="first")
        second = prepare_system_log_download(session, [source], job_id="second")
        try:
            with pytest.raises(DownloadBusyError):
                prepare_system_log_download(session, [source], job_id="third")
        finally:
            first.close()
            second.close()
        released = prepare_system_log_download(session, [source], job_id="released")
        released.close()


def test_download_spools_to_disk_before_streaming_and_closes_spill(
    studio_paths,
    studio_project_factory,
    monkeypatch,
):
    project = studio_project_factory()
    path = studio_paths.data_dir / "system-job-1.json"
    _write(path, [_line(_row(job_id="job-1", session_id=None, message="spill"))])
    with create_session() as session:
        source = EnvironmentSource(
            environment_id=project.environment_id,
            source_kind="logs",
            uri=str(path.parent),
            storage_provider="local",
        )
        session.add(source)
        session.flush()
        _manifest(session, source, path, event_min=1)
        session.commit()
        monkeypatch.setattr(
            "datacoolie_studio.domains.logs.system_log_download.RAM_SPOOL_THRESHOLD",
            1,
        )
        prepared = prepare_system_log_download(session, [source], job_id="job-1")
        spill = prepared.spool
        try:
            assert getattr(spill, "_rolled", False)
            assert b"spill" in b"".join(prepared.iter_chunks())
        finally:
            prepared.close()
        assert spill.closed


def test_download_spool_construction_failure_releases_lease(
    studio_paths,
    studio_project_factory,
    monkeypatch,
):
    project = studio_project_factory()
    with create_session() as session:
        source = EnvironmentSource(
            environment_id=project.environment_id,
            source_kind="logs",
            uri=str(studio_paths.data_dir),
            storage_provider="local",
        )
        session.add(source)
        session.flush()

        def fail_spool(*_args, **_kwargs):
            raise OSError("spool unavailable")

        with monkeypatch.context() as scoped:
            scoped.setattr(
                "datacoolie_studio.domains.logs.system_log_download.tempfile.SpooledTemporaryFile",
                fail_spool,
            )
            with pytest.raises(OSError, match="spool unavailable"):
                prepare_system_log_download(session, [source], job_id="failed")

        # The patch is restored before this second call, proving the failed
        # constructor did not consume one of the process-wide two leases.
        prepared = prepare_system_log_download(session, [source], job_id="retry")
        prepared.close()


def test_remote_download_caps_copy_and_verifies_revision_before_parsing(
    studio_paths,
    studio_project_factory,
    monkeypatch,
):
    project = studio_project_factory()
    raw = _line(_row(job_id="job-remote", session_id=None, message="remote"))
    remote_uri = "s3://bucket/system-job-remote.json"
    revision = StorageRevision(
        canonical_uri=remote_uri,
        size=len(raw),
        last_modified=datetime(2026, 10, 4, tzinfo=timezone.utc),
        provider_revision="etag-1",
    )

    class FakeRemoteAdapter:
        def __init__(self):
            self.opened = 0

        def stat(self, uri: str) -> StorageRevision:
            assert uri == remote_uri
            return revision

        def open_read(self, uri: str):
            assert uri == remote_uri
            self.opened += 1
            return BytesIO(raw)

    adapter = FakeRemoteAdapter()
    monkeypatch.setattr(
        "datacoolie_studio.domains.logs.system_log_download.create_storage_adapter",
        lambda *_args, **_kwargs: adapter,
    )
    with create_session() as session:
        source = EnvironmentSource(
            environment_id=project.environment_id,
            source_kind="logs",
            uri="s3://bucket",
            storage_provider="s3",
            storage_auth_mode="ambient",
        )
        session.add(source)
        session.flush()
        session.add(
            LogFileManifest(
                source_id=source.id,
                file_uri=remote_uri,
                file_kind="system_json",
                revision_json=revision_json(revision),
                row_count=1,
                status="ok",
                event_min_timestamp=datetime(2026, 10, 4, tzinfo=timezone.utc),
            )
        )
        session.commit()
        prepared = prepare_system_log_download(session, [source], job_id="job-remote")
        try:
            assert b"remote" in b"".join(prepared.iter_chunks())
        finally:
            prepared.close()
        monkeypatch.setattr(
            "datacoolie_studio.domains.logs.system_log_download.MAX_REMOTE_OBJECT_BYTES",
            1,
        )
        with pytest.raises(DownloadTooLargeError, match="staging limit"):
            prepare_system_log_download(session, [source], job_id="job-remote")
    assert adapter.opened == 1


def test_remote_revision_aware_open_rejects_aba_and_closes_stream(
    studio_paths,
    studio_project_factory,
    monkeypatch,
):
    project = studio_project_factory()
    raw = _line(_row(job_id="job-remote", session_id=None, message="remote"))
    remote_uri = "s3://bucket/system-job-remote.json"
    expected = StorageRevision(
        canonical_uri=remote_uri,
        size=len(raw),
        last_modified=datetime(2026, 10, 4, tzinfo=timezone.utc),
        provider_revision="etag-1",
    )
    changed = StorageRevision(
        canonical_uri=remote_uri,
        size=len(raw),
        last_modified=expected.last_modified,
        provider_revision="etag-2",
    )

    class FakeRevisionAwareAdapter:
        def __init__(self):
            self.handle = None

        def stat(self, uri: str) -> StorageRevision:
            assert uri == remote_uri
            return expected

        def open_read_with_revision(self, uri: str):
            assert uri == remote_uri
            self.handle = BytesIO(raw)
            return self.handle, changed

    adapter = FakeRevisionAwareAdapter()
    monkeypatch.setattr(
        "datacoolie_studio.domains.logs.system_log_download.create_storage_adapter",
        lambda *_args, **_kwargs: adapter,
    )
    with create_session() as session:
        source = EnvironmentSource(
            environment_id=project.environment_id,
            source_kind="logs",
            uri="s3://bucket",
            storage_provider="s3",
            storage_auth_mode="ambient",
        )
        session.add(source)
        session.flush()
        session.add(
            LogFileManifest(
                source_id=source.id,
                file_uri=remote_uri,
                file_kind="system_json",
                revision_json=revision_json(expected),
                row_count=1,
                status="ok",
                event_min_timestamp=datetime(2026, 10, 4, tzinfo=timezone.utc),
            )
        )
        session.commit()
        with pytest.raises(DownloadRevisionConflictError, match="changed"):
            prepare_system_log_download(session, [source], job_id="job-remote")
    assert adapter.handle is not None and adapter.handle.closed


def test_remote_download_rejects_growth_and_post_read_revision_change(
    studio_paths,
    studio_project_factory,
    monkeypatch,
):
    project = studio_project_factory()
    raw = _line(_row(job_id="job-remote", session_id=None, message="remote"))
    remote_uri = "s3://bucket/system-job-remote.json"
    expected = StorageRevision(
        canonical_uri=remote_uri,
        size=len(raw),
        last_modified=datetime(2026, 10, 4, tzinfo=timezone.utc),
        provider_revision="etag-1",
    )
    changed = StorageRevision(
        canonical_uri=remote_uri,
        size=len(raw),
        last_modified=expected.last_modified,
        provider_revision="etag-2",
    )

    class GrowingAdapter:
        def stat(self, uri: str) -> StorageRevision:
            assert uri == remote_uri
            return expected

        def open_read(self, uri: str):
            assert uri == remote_uri
            return BytesIO(raw + b"extra")

    growing = GrowingAdapter()
    monkeypatch.setattr(
        "datacoolie_studio.domains.logs.system_log_download.create_storage_adapter",
        lambda *_args, **_kwargs: growing,
    )
    with create_session() as session:
        source = EnvironmentSource(
            environment_id=project.environment_id,
            source_kind="logs",
            uri="s3://bucket",
            storage_provider="s3",
            storage_auth_mode="ambient",
        )
        session.add(source)
        session.flush()
        session.add(
            LogFileManifest(
                source_id=source.id,
                file_uri=remote_uri,
                file_kind="system_json",
                revision_json=revision_json(expected),
                row_count=1,
                status="ok",
                event_min_timestamp=datetime(2026, 10, 4, tzinfo=timezone.utc),
            )
        )
        session.commit()
        with pytest.raises(DownloadRevisionConflictError, match="size"):
            prepare_system_log_download(session, [source], job_id="job-remote")

        class PostReadChangedAdapter:
            def __init__(self):
                self.calls = 0

            def stat(self, uri: str) -> StorageRevision:
                assert uri == remote_uri
                self.calls += 1
                return expected if self.calls == 1 else changed

            def open_read(self, uri: str):
                assert uri == remote_uri
                return BytesIO(raw)

        post_read = PostReadChangedAdapter()
        monkeypatch.setattr(
            "datacoolie_studio.domains.logs.system_log_download.create_storage_adapter",
            lambda *_args, **_kwargs: post_read,
        )
        with pytest.raises(DownloadRevisionConflictError, match="changed"):
            prepare_system_log_download(session, [source], job_id="job-remote")


def test_remote_unknown_size_fails_before_adapter_transfer(
    studio_paths,
    studio_project_factory,
    monkeypatch,
):
    project = studio_project_factory()
    remote_uri = "s3://bucket/system-job-remote.json"
    opened = False

    def fail_factory(*_args, **_kwargs):
        nonlocal opened
        opened = True
        raise AssertionError("unknown-size object must fail before adapter creation")

    monkeypatch.setattr(
        "datacoolie_studio.domains.logs.system_log_download.create_storage_adapter",
        fail_factory,
    )
    with create_session() as session:
        source = EnvironmentSource(
            environment_id=project.environment_id,
            source_kind="logs",
            uri="s3://bucket",
            storage_provider="s3",
            storage_auth_mode="ambient",
        )
        session.add(source)
        session.flush()
        session.add(
            LogFileManifest(
                source_id=source.id,
                file_uri=remote_uri,
                file_kind="system_json",
                revision_json=json.dumps(
                    {
                        "last_modified": "2026-10-04T00:00:00+00:00",
                        "provider_revision": "etag-1",
                    }
                ),
                row_count=1,
                status="ok",
                event_min_timestamp=datetime(2026, 10, 4, tzinfo=timezone.utc),
            )
        )
        session.commit()
        with pytest.raises(DownloadTooLargeError, match="size is unknown"):
            prepare_system_log_download(session, [source], job_id="job-remote")
    assert not opened


def test_streaming_response_releases_lease_when_asgi_send_aborts(
    studio_paths,
    studio_project_factory,
):
    project = studio_project_factory()
    path = studio_paths.data_dir / "system-abort.json"
    _write(path, [_line(_row(job_id="abort", session_id=None, message="body"))])
    with create_session() as session:
        source = EnvironmentSource(
            environment_id=project.environment_id,
            source_kind="logs",
            uri=str(studio_paths.data_dir),
            storage_provider="local",
        )
        session.add(source)
        session.flush()
        _manifest(session, source, path, event_min=1)
        session.commit()
        prepared = prepare_system_log_download(session, [source], job_id="abort")
        response = PreparedSystemLogStreamingResponse(prepared)

        async def send(message):
            if message["type"] == "http.response.body" and message["body"]:
                raise OSError("client disconnected")

        async def receive():
            return {"type": "http.disconnect"}

        with pytest.raises(ClientDisconnect):
            asyncio.run(
                response(
                    {"type": "http", "asgi": {"spec_version": "2.4"}},
                    receive,
                    send,
                )
            )
        assert prepared._closed


def test_manifest_pages_keep_one_snapshot_during_publication(
    studio_paths, studio_project_factory, monkeypatch,
):
    from datacoolie_studio.domains.logs import system_log_download as download

    project = studio_project_factory()
    paths = [studio_paths.data_dir / f"part-{index}-job-1.jsonl" for index in range(3)]
    original = [_line(_row(job_id="job-1", session_id=None, message=str(index))) for index in range(3)]
    for path, payload in zip(paths, original):
        _write(path, [payload])
    monkeypatch.setattr(download, "MANIFEST_PAGE_SIZE", 1)
    with create_session() as session:
        source = EnvironmentSource(environment_id=project.environment_id, source_kind="logs", uri=str(studio_paths.data_dir), storage_provider="local")
        session.add(source)
        session.flush()
        for index, path in enumerate(paths):
            _manifest(session, source, path, event_min=index)
        session.commit()
        source_id = source.id
        original_reader = download.iter_system_log_lines
        published = False

        def publish_after_first_file(file_uri, **kwargs):
            nonlocal published
            yield from original_reader(file_uri, **kwargs)
            if not published:
                published = True
                with create_session() as writer:
                    from sqlalchemy import select
                    manifests = list(writer.scalars(select(LogFileManifest).where(LogFileManifest.source_id == source_id).order_by(LogFileManifest.id)))
                    writer.delete(manifests[1])
                    manifests[2].event_min_timestamp = datetime(2026, 10, 3, tzinfo=timezone.utc)
                    extra = studio_paths.data_dir / "new-job-1.jsonl"
                    _write(extra, [_line(_row(job_id="job-1", session_id=None, message="new"))])
                    _manifest(writer, writer.get(EnvironmentSource, source_id), extra, event_min=4)
                    writer.commit()

        monkeypatch.setattr(download, "iter_system_log_lines", publish_after_first_file)
        prepared = prepare_system_log_download(session, [source], job_id="job-1")
        refresh_workspace_reads(session)
        assert not session.in_transaction()
        assert b"".join(prepared.iter_chunks()) == b"".join(original)
        assert published


@pytest.mark.parametrize("during_read", [False, True])
def test_local_revision_changes_fail_without_a_partial_download(
    studio_paths, studio_project_factory, monkeypatch, during_read,
):
    from datacoolie_studio.domains.logs import system_log_download as download

    project = studio_project_factory()
    path = studio_paths.data_dir / "system-job-1.jsonl"
    _write(path, [_line(_row(job_id="job-1", session_id=None, message="before"))])
    with create_session() as session:
        source = EnvironmentSource(environment_id=project.environment_id, source_kind="logs", uri=str(studio_paths.data_dir), storage_provider="local")
        session.add(source)
        session.flush()
        _manifest(session, source, path, event_min=1)
        session.commit()
        extra = _line(_row(job_id="job-1", session_id=None, message="appended"))
        if during_read:
            original_reader = download.iter_system_log_lines

            def append_after_read(file_uri, **kwargs):
                yield from original_reader(file_uri, **kwargs)
                with path.open("ab") as handle:
                    handle.write(extra)

            monkeypatch.setattr(download, "iter_system_log_lines", append_after_read)
        else:
            with path.open("ab") as handle:
                handle.write(extra)
        with pytest.raises(DownloadRevisionConflictError, match="changed"):
            prepare_system_log_download(session, [source], job_id="job-1")
        assert download._download_leases.acquire(blocking=False)
        download._download_leases.release()


def test_response_start_failure_closes_an_unstarted_download(
    studio_paths, studio_project_factory,
):
    project = studio_project_factory()
    with create_session() as session:
        source = EnvironmentSource(environment_id=project.environment_id, source_kind="logs", uri=str(studio_paths.data_dir), storage_provider="local")
        session.add(source)
        session.commit()
        prepared = prepare_system_log_download(session, [source], job_id="job-1")
        response = PreparedSystemLogStreamingResponse(prepared)

        async def send(message):
            raise OSError("header send aborted")

        async def receive():
            return {"type": "http.disconnect"}

        with pytest.raises(ClientDisconnect):
            asyncio.run(response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send))
        assert prepared._closed


def test_known_empty_remote_object_does_not_block_other_job_records(
    studio_paths, studio_project_factory, monkeypatch,
):
    project = studio_project_factory()
    payloads = {"s3://bucket/empty-job-1.jsonl": b"", "s3://bucket/populated-job-1.jsonl": _line(_row(job_id="job-1", session_id=None, message="present"))}
    revisions = {uri: StorageRevision(canonical_uri=uri, size=len(payload), last_modified=datetime(2026, 10, 4, tzinfo=timezone.utc), provider_revision="etag") for uri, payload in payloads.items()}

    class Adapter:
        def stat(self, uri):
            return revisions[uri]

        def open_read(self, uri):
            return BytesIO(payloads[uri])

    monkeypatch.setattr("datacoolie_studio.domains.logs.system_log_download.create_storage_adapter", lambda *args, **kwargs: Adapter())
    with create_session() as session:
        source = EnvironmentSource(environment_id=project.environment_id, source_kind="logs", uri="s3://bucket", storage_provider="s3", storage_auth_mode="ambient")
        session.add(source)
        session.flush()
        for uri, revision in revisions.items():
            session.add(LogFileManifest(source_id=source.id, file_uri=uri, file_kind="system_json", revision_json=revision_json(revision), status="ok"))
        session.commit()
        prepared = prepare_system_log_download(session, [source], job_id="job-1")
        assert b"".join(prepared.iter_chunks()) == payloads["s3://bucket/populated-job-1.jsonl"]
        # A provider's missing-size metadata may coalesce to zero. Reading one
        # byte detects a nonempty stream without permitting unbounded staging.
        payloads["s3://bucket/empty-job-1.jsonl"] = b"x"
        with pytest.raises(DownloadRevisionConflictError, match="size"):
            prepare_system_log_download(session, [source], job_id="job-1")
