from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from pathlib import PurePosixPath
from types import SimpleNamespace
from zipfile import ZipFile

import pytest


def test_remote_directory_snapshot_preserves_package_modules(
    tmp_path: Path, monkeypatch
):
    from datacoolie_studio.domains.code_artifacts import materializer
    from datacoolie_studio.domains.code_artifacts.indexer import (
        build_artifact_index,
        read_artifact_module,
    )
    from datacoolie_studio.domains.storage.adapters import (
        StorageObject,
        StorageRevision,
    )
    from datacoolie_studio.domains.storage.inventory import StorageInventory

    modified = datetime(2026, 7, 23, tzinfo=timezone.utc)
    objects = {
        "s3://bucket/code/functions/__init__.py": b"",
        "s3://bucket/code/functions/sources.py": (
            b"def read_orders():\n    return 'orders'\n"
        ),
    }

    class FakeAdapter:
        def inventory(self, request):
            result = [
                StorageObject(
                    canonical_uri=key,
                    name=key.rsplit("/", 1)[-1],
                    object_type="file",
                    size=len(value),
                    last_modified=modified,
                    provider_revision=f"etag-{index}",
                )
                for index, (key, value) in enumerate(objects.items())
            ]
            return StorageInventory(
                objects=tuple(result),
                completeness="complete",
                requests=1,
                pages=1,
                directories_visited=1,
                objects_inspected=len(result),
                matching_objects=len(result),
                retries=0,
                throttles=0,
                bytes_read=0,
                duration_ms=1,
            )

        def stat(self, uri):
            value = objects[uri]
            return StorageRevision(
                canonical_uri=uri,
                size=len(value),
                last_modified=modified,
                provider_revision=f"etag-{list(objects).index(uri)}",
            )

        def materialize(self, uri, target, expected_revision=None):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(objects[uri])
            return self.stat(uri)

    monkeypatch.setattr(
        materializer, "source_materialization_cache_dir", lambda: tmp_path / "cache"
    )
    monkeypatch.setattr(
        materializer, "create_storage_adapter", lambda *_args, **_kwargs: FakeAdapter()
    )
    source = SimpleNamespace(
        id=41,
        uri="s3://bucket/code/functions",
        storage_provider="s3",
        storage_auth_mode="ambient",
        credential_profile_id=None,
        storage_config_json="{}",
        source_config_json='{"artifact_type":"directory"}',
    )

    snapshot, revision = materializer.materialize_remote_artifact(
        SimpleNamespace(), source, "directory"
    )
    indexed = build_artifact_index(snapshot, "directory")
    content, relative = read_artifact_module(
        snapshot, "directory", "functions.sources"
    )

    assert sorted(indexed["modules"]) == ["functions", "functions.sources"]
    assert relative == "sources.py"
    assert "read_orders" in content
    assert revision["provider_revision"]

    from datacoolie_studio.domains.code_artifacts import service

    materializer.clear_remote_artifact_snapshot(source.id)
    content, module_name, relative = service.read_code_artifact_function_source(
        source,
        "functions.sources.read_orders",
        session=SimpleNamespace(),
    )
    assert module_name == "functions.sources"
    assert relative == "sources.py"
    assert "read_orders" in content


def test_remote_directory_snapshot_downloads_only_changed_files(
    tmp_path: Path, monkeypatch
):
    from datacoolie_studio.domains.code_artifacts import materializer
    from datacoolie_studio.domains.storage.adapters import StorageObject
    from datacoolie_studio.domains.storage.inventory import StorageInventory

    modified = datetime(2026, 7, 28, tzinfo=timezone.utc)
    payloads = {
        "s3://bucket/code/functions/__init__.py": b"",
        "s3://bucket/code/functions/source.py": b"VALUE = 1\n",
    }
    revisions = {uri: "v1" for uri in payloads}

    class FakeAdapter:
        provider = "s3"

        def __init__(self) -> None:
            self.downloaded: list[str] = []

        def inventory(self, request):
            result = [
                StorageObject(
                    canonical_uri=key,
                    name=key.rsplit("/", 1)[-1],
                    object_type="file",
                    size=len(value),
                    last_modified=modified,
                    provider_revision=revisions[key],
                )
                for key, value in payloads.items()
            ]
            return StorageInventory(
                objects=tuple(result),
                completeness="complete",
                requests=1,
                pages=1,
                directories_visited=1,
                objects_inspected=len(result),
                matching_objects=len(result),
                retries=0,
                throttles=0,
                bytes_read=0,
                duration_ms=1,
            )

        def materialize(self, uri, target, expected_revision=None):
            self.downloaded.append(uri)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payloads[uri])
            return expected_revision

    adapter = FakeAdapter()
    monkeypatch.setattr(
        materializer, "source_materialization_cache_dir", lambda: tmp_path / "cache"
    )
    monkeypatch.setattr(
        materializer, "create_storage_adapter", lambda *_args, **_kwargs: adapter
    )
    source = SimpleNamespace(
        id=42,
        uri="s3://bucket/code",
        storage_provider="s3",
        storage_auth_mode="ambient",
        credential_profile_id=None,
        storage_config_json="{}",
        source_config_json='{"artifact_type":"directory"}',
    )

    materializer.materialize_remote_artifact(SimpleNamespace(), source, "directory")
    assert len(adapter.downloaded) == 2

    adapter.downloaded.clear()
    payloads["s3://bucket/code/functions/source.py"] = b"VALUE = 2\n"
    revisions["s3://bucket/code/functions/source.py"] = "v2"
    snapshot, _ = materializer.materialize_remote_artifact(
        SimpleNamespace(), source, "directory"
    )

    assert adapter.downloaded == ["s3://bucket/code/functions/source.py"]
    assert (Path(snapshot) / "functions" / "__init__.py").read_bytes() == b""
    assert (Path(snapshot) / "functions" / "source.py").read_bytes() == b"VALUE = 2\n"


def test_directory_index_is_static_and_respects_module_roots(tmp_path: Path):
    from datacoolie_studio.domains.code_artifacts.indexer import build_artifact_index

    package = tmp_path / "src" / "demo"
    package.mkdir(parents=True)
    marker = tmp_path / "executed.txt"
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "pipeline.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n",
        encoding="utf-8",
    )
    (tmp_path / "outside.py").write_text("VALUE = 1\n", encoding="utf-8")

    indexed = build_artifact_index(str(tmp_path), "directory", ["src"])

    assert marker.exists() is False
    assert sorted(indexed["modules"]) == ["demo", "demo.pipeline"]
    assert indexed["manifest"]["python_files"] == 3
    assert indexed["manifest"]["modules"] == 2


def test_directory_index_infers_package_from_root_init_and_deduplicates_prefix(tmp_path: Path):
    from datacoolie_studio.domains.code_artifacts.indexer import build_artifact_index

    package = tmp_path / "functions"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "sources.py").write_text("def load():\n    return 1\n", encoding="utf-8")

    automatic = build_artifact_index(str(package), "directory")
    legacy_prefix = build_artifact_index(str(package), "directory", module_prefix="functions")

    assert sorted(automatic["modules"]) == ["functions", "functions.sources"]
    assert sorted(legacy_prefix["modules"]) == ["functions", "functions.sources"]
    assert automatic["modules"]["functions.sources"]["path"] == "sources.py"


def test_directory_index_uses_basename_for_loose_python_file(tmp_path: Path):
    from datacoolie_studio.domains.code_artifacts.indexer import build_artifact_index

    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "load.py").write_text("VALUE = 1\n", encoding="utf-8")

    indexed = build_artifact_index(str(tmp_path), "directory")

    assert sorted(indexed["modules"]) == ["load"]
    assert indexed["modules"]["load"]["path"] == "scripts/load.py"


def test_directory_index_reports_duplicate_loose_modules(tmp_path: Path):
    from datacoolie_studio.domains.code_artifacts.indexer import build_artifact_index

    for folder in ("first", "second"):
        target = tmp_path / folder
        target.mkdir()
        (target / "load.py").write_text(f"ORIGIN = {folder!r}\n", encoding="utf-8")

    indexed = build_artifact_index(str(tmp_path), "directory")

    assert indexed["modules"] == {}
    assert indexed["diagnostics"] == [{
        "severity": "warning",
        "code": "duplicate_python_module",
        "message": "Multiple Python files resolve to module load",
        "details": {"module": "load", "paths": ["first/load.py", "second/load.py"]},
    }]


def test_python_file_artifact_indexes_and_reads_module(tmp_path: Path):
    from datacoolie_studio.domains.code_artifacts.indexer import build_artifact_index, read_artifact_module

    source = tmp_path / "load.py"
    source.write_text("def run():\n    return 1\n", encoding="utf-8")

    indexed = build_artifact_index(str(source), "python_file")
    content, relative_path = read_artifact_module(str(source), "python_file", "load")

    assert sorted(indexed["modules"]) == ["load"]
    assert relative_path == "load.py"
    assert "def run" in content


@pytest.mark.parametrize("artifact_type,suffix", [("zip", ".zip"), ("wheel", ".whl")])
def test_archive_index_uses_package_markers(tmp_path: Path, artifact_type: str, suffix: str):
    from datacoolie_studio.domains.code_artifacts.indexer import build_artifact_index

    archive_path = tmp_path / f"artifact{suffix}"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("functions/__init__.py", "")
        archive.writestr("functions/sources.py", "def load():\n    return 1\n")
        archive.writestr("scripts/cleanup.py", "VALUE = 1\n")

    indexed = build_artifact_index(str(archive_path), artifact_type)

    assert sorted(indexed["modules"]) == ["cleanup", "functions", "functions.sources"]


def test_installed_distribution_index_uses_package_markers(tmp_path: Path, monkeypatch):
    from datacoolie_studio.domains.code_artifacts import indexer

    site = tmp_path / "site"
    package = site / "functions"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "sources.py").write_text("def load():\n    return 1\n", encoding="utf-8")

    class FakeDistribution:
        version = "1.0"
        files = [PurePosixPath("functions/__init__.py"), PurePosixPath("functions/sources.py")]

        @staticmethod
        def locate_file(item: PurePosixPath) -> Path:
            return site / Path(str(item))

    monkeypatch.setattr(indexer.importlib.metadata, "distribution", lambda _: FakeDistribution())

    indexed = indexer.build_artifact_index("demo-package", "installed_distribution")

    assert sorted(indexed["modules"]) == ["functions", "functions.sources"]
    assert indexed["distribution_version"] == "1.0"


def test_installed_distribution_rebuilds_stale_analyzer_materialization(monkeypatch):
    from datacoolie_studio.domains.code_artifacts import service

    stale = SimpleNamespace(analyzer_version="artifact-index-v1", source_revision_json="{}")
    current = SimpleNamespace(analyzer_version=service.ANALYZER_VERSION, source_revision_json="{}")
    materializations = iter([stale, current])
    source = SimpleNamespace(
        id=7,
        uri="demo-package",
        source_kind="code",
        source_config_json='{"artifact_type": "installed_distribution"}',
    )
    refreshes: list[str] = []
    monkeypatch.setattr(service, "code_artifact_materialization", lambda *_: next(materializations))
    monkeypatch.setattr(service.sync, "stat_source", lambda *_args, **_kwargs: {"exists": False})
    monkeypatch.setattr(
        service,
        "refresh_code_artifact",
        lambda *_args, job_type: refreshes.append(job_type),
    )

    result = service.ensure_code_artifact_materialization(SimpleNamespace(), source)

    assert result is current
    assert refreshes == ["auto_refresh"]


def test_remote_code_ensure_reuses_current_materialization_without_sync(monkeypatch):
    from datacoolie_studio.domains.code_artifacts import service

    source_stat = {
        "provider": "s3",
        "uri": "s3://bucket/functions",
        "exists": True,
        "object_type": "directory",
        "provider_revision": "revision-1",
    }
    source = SimpleNamespace(
        id=12,
        uri="s3://bucket/functions",
        storage_provider="s3",
        source_config_json=json.dumps({"artifact_type": "directory"}),
    )
    materialization = SimpleNamespace(
        analyzer_version=service.ANALYZER_VERSION,
        source_revision_json=json.dumps({"source_stat": source_stat}),
    )
    refreshes = []

    monkeypatch.setattr(service, "code_artifact_materialization", lambda *_: materialization)
    monkeypatch.setattr(
        service,
        "materialize_remote_artifact",
        lambda *_args, **_kwargs: ("D:/cache/source-12/functions", source_stat),
    )
    monkeypatch.setattr(
        service,
        "refresh_code_artifact",
        lambda *_args, **kwargs: refreshes.append(kwargs["job_type"]),
    )

    result = service.ensure_code_artifact_materialization(SimpleNamespace(), source)

    assert result is materialization
    assert refreshes == []


def test_remote_code_ensure_refreshes_when_provider_revision_changes(monkeypatch):
    from datacoolie_studio.domains.code_artifacts import service

    source = SimpleNamespace(
        id=13,
        uri="s3://bucket/functions",
        storage_provider="s3",
        source_config_json=json.dumps({"artifact_type": "directory"}),
    )
    stale = SimpleNamespace(
        analyzer_version=service.ANALYZER_VERSION,
        source_revision_json=json.dumps(
            {"source_stat": {"provider_revision": "revision-1"}}
        ),
    )
    current = SimpleNamespace(
        analyzer_version=service.ANALYZER_VERSION,
        source_revision_json=json.dumps(
            {"source_stat": {"provider_revision": "revision-2"}}
        ),
    )
    materializations = iter([stale, current])
    refreshes = []

    monkeypatch.setattr(
        service, "code_artifact_materialization", lambda *_: next(materializations)
    )
    monkeypatch.setattr(
        service,
        "materialize_remote_artifact",
        lambda *_args, **_kwargs: (
            "D:/cache/source-13/functions",
            {"provider_revision": "revision-2"},
        ),
    )
    monkeypatch.setattr(
        service,
        "refresh_code_artifact",
        lambda *_args, **kwargs: refreshes.append(kwargs["job_type"]),
    )

    result = service.ensure_code_artifact_materialization(SimpleNamespace(), source)

    assert result is current
    assert refreshes == ["auto_refresh"]


def test_project_discovery_uses_same_automatic_package_rules(tmp_path: Path):
    from datacoolie_studio.domains.code_artifacts.indexer import build_artifact_index
    from datacoolie_studio.domains.sources.discovery import discover_datacoolie_project_sources

    package = tmp_path / "functions"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "sources.py").write_text("VALUE = 1\n", encoding="utf-8")

    discovered = discover_datacoolie_project_sources(str(tmp_path), include_metadata=False)

    assert discovered.errors == []
    assert len(discovered.code_artifacts) == 1
    artifact = discovered.code_artifacts[0]
    assert artifact.uri == str(package)
    assert artifact.source_config["artifact_type"] == "directory"
    assert "module_prefix" not in artifact.source_config
    indexed = build_artifact_index(
        artifact.uri,
        artifact.source_config["artifact_type"],
        artifact.source_config["module_roots"],
    )
    assert sorted(indexed["modules"]) == ["functions", "functions.sources"]


def test_project_discovery_accepts_direct_python_file(tmp_path: Path):
    from datacoolie_studio.domains.sources.discovery import discover_datacoolie_project_sources

    source = tmp_path / "load.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")

    discovered = discover_datacoolie_project_sources(
        str(tmp_path),
        include_metadata=False,
        code_uri=str(source),
    )

    assert discovered.errors == []
    assert len(discovered.code_artifacts) == 1
    assert discovered.code_artifacts[0].source_config["artifact_type"] == "python_file"


@pytest.mark.parametrize("suffix,artifact_type", [(".zip", "zip"), (".whl", "wheel")])
def test_project_discovery_accepts_archive_code_path(tmp_path: Path, suffix: str, artifact_type: str):
    from datacoolie_studio.domains.sources.discovery import discover_datacoolie_project_sources

    archive_path = tmp_path / f"code{suffix}"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("functions/__init__.py", "")
        archive.writestr("functions/sources.py", "VALUE = 1\n")

    discovered = discover_datacoolie_project_sources(
        str(tmp_path),
        include_metadata=False,
        code_uri=str(archive_path),
    )

    assert discovered.errors == []
    assert len(discovered.code_artifacts) == 1
    assert discovered.code_artifacts[0].source_config["artifact_type"] == artifact_type
    assert discovered.code_artifacts[0].record_counts == {"python_files": 2}


def test_archive_index_rejects_unsafe_member_even_when_not_python(tmp_path: Path):
    from datacoolie_studio.domains.code_artifacts.indexer import ArtifactIndexError, build_artifact_index

    archive_path = tmp_path / "unsafe.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("package/module.py", "VALUE = 1\n")
        archive.writestr("../README.txt", "unsafe")

    with pytest.raises(ArtifactIndexError, match="Unsafe archive member path"):
        build_artifact_index(str(archive_path), "zip")


def test_code_artifact_api_lifecycle(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "studio.db"
    artifact_root = tmp_path / "artifact"
    package = artifact_root / "src" / "demo"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "pipeline.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(db_path))

    from fastapi.testclient import TestClient

    from datacoolie_studio.main import app

    with TestClient(app) as client:
        project = client.post("/api/v1/projects", json={"name": "demo"}).json()
        environment = client.post(
            f"/api/v1/projects/{project['id']}/environments",
            json={"name": "dev"},
        ).json()
        artifact = client.post(
            f"/api/v1/environments/{environment['id']}/code-artifacts",
            json={
                "uri": str(artifact_root),
                "label": "pipeline code",
                "source_config": {"artifact_type": "directory", "module_roots": ["src"]},
            },
        ).json()

        assert artifact["source_config"]["artifact_type"] == "directory"
        assert artifact["source_config"]["module_roots"] == ["src"]

        refreshed = client.post(f"/api/v1/environments/{environment['id']}/code-artifacts/{artifact['id']}/refresh").json()
        assert refreshed["status"] == "ok"
        assert refreshed["revision"]["artifact_type"] == "directory"
        assert refreshed["revision"]["python_files"] == 2

        listed = client.get(f"/api/v1/environments/{environment['id']}/code-artifacts").json()
        assert listed[0]["latest_validation"]["status"] == "ok"
        assert listed[0]["latest_validation"]["record_counts"] == {"python_files": 2}

        validation = client.post(f"/api/v1/environments/{environment['id']}/code-artifacts/{artifact['id']}/validate").json()
        assert validation["status"] == "ok"
        assert validation["record_counts"] == {"python_files": 2}

        refreshed_again = client.post(
            f"/api/v1/environments/{environment['id']}/code-artifacts/{artifact['id']}/refresh"
        ).json()
        assert refreshed_again["status"] == "ok"
        listed_after_refresh = client.get(
            f"/api/v1/environments/{environment['id']}/code-artifacts"
        ).json()
        assert listed_after_refresh[0]["latest_validation"] == validation

        with sqlite3.connect(db_path) as connection:
            assert connection.execute("select count(*) from code_artifact_materializations").fetchone()[0] == 1
            assert connection.execute("select count(*) from source_observations").fetchone()[0] == 1
            job_types = [
                row[0]
                for row in connection.execute(
                    "select job_type from sync_jobs order by id"
                ).fetchall()
            ]
            assert job_types.count("initial_refresh") == 1
            assert job_types.count("auto_refresh") == 1
            assert job_types.count("force_refresh") == 2

        impact = client.get(f"/api/v1/environments/{environment['id']}/code-artifacts/{artifact['id']}/delete-impact")
        assert impact.status_code == 200
        impact_body = impact.json()
        assert impact_body["metadata_file_deleted"] is False
        assert "original code artifact will not be deleted" in impact_body["summary"]
        impact_counts = {item["kind"]: item["count"] for item in impact_body["impacts"]}
        assert impact_counts["materialization"] == 1
        assert impact_counts["source_observation"] == 1
        assert impact_counts["sync_job"] == 4

        response = client.delete(f"/api/v1/environments/{environment['id']}/code-artifacts/{artifact['id']}")
        assert response.status_code == 204
        with sqlite3.connect(db_path) as connection:
            assert connection.execute("select count(*) from code_artifact_materializations").fetchone()[0] == 0
            assert connection.execute("select count(*) from source_observations").fetchone()[0] == 0
            assert connection.execute("select count(*) from sync_jobs").fetchone()[0] == 0

    os.environ.pop("DATACOOLIE_STUDIO_DB", None)
