from __future__ import annotations

import json
from pathlib import Path

import pytest

from datacoolie_studio.db.models import EnvironmentSource, LogFileManifest
from datacoolie_studio.db.session import create_session
from datacoolie_studio.domains.logs import reader, system_log_download
from datacoolie_studio.domains.logs.file_state import revision_json
from datacoolie_studio.domains.logs.system_log_download import (
    DownloadAdmissionError,
    prepare_system_log_download,
)
from datacoolie_studio.domains.logs.system_log_filename import parse_system_log_filename
from datacoolie_studio.domains.logs.system_log_selection import (
    filename_contains_job,
    system_log_manifest_statement,
)
from datacoolie_studio.domains.logs.system_logs import system_log_records
from datacoolie_studio.domains.storage.adapters import LocalStorageAdapter
from tests.support.v6_records import native_record


def _line(job: str, message: str, session_id: str | None = "session-a") -> bytes:
    row = native_record({
        "_type": "system_log", "job_id": job, "log_session_id": session_id,
        "msg": message, "ts": "2026-10-10T01:00:00+00:00",
    })
    return json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


def _source(session, environment_id: int, root: Path) -> EnvironmentSource:
    source = EnvironmentSource(
        environment_id=environment_id, source_kind="logs", uri=str(root),
        storage_provider="local",
    )
    session.add(source)
    session.flush()
    return source


def _file(session, source, path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    revision = LocalStorageAdapter().stat(str(path))
    parsed = parse_system_log_filename(str(path))
    session.add(LogFileManifest(
        source_id=source.id, file_uri=revision.canonical_uri,
        file_kind="system_json", revision_json=revision_json(revision),
        filename_job_id=parsed.job_id, filename_job_id_alt=parsed.job_id_alt,
        log_timestamp=parsed.log_timestamp, row_count=999, status="ok",
        # A single content-derived session hint must not prune mixed sessions.
        log_session_id="misleading-hint",
    ))


def test_indexed_preview_and_download_read_all_job_parts_only(
    studio_paths, studio_project_factory, monkeypatch,
):
    project = studio_project_factory()
    root = studio_paths.data_dir / "source"
    snapshot = root / "system_20261010_010000_1_0_job-1.json"
    part = root / "system_20261010_010001_1_0_job-1_part_00000001.json"
    other = root / "system_20261010_010000_1_0_job-2.json"
    first = _line("job-1", "one")
    child = json.loads(_line("job-1", "child"))
    child["dataflow_id"] = "flow-1"
    child_bytes = json.dumps(child, separators=(",", ":")).encode() + b"\n"
    duplicate = _line("job-1", "two")
    part_bytes = duplicate + duplicate + _line("job-1", "null", None)
    with create_session() as session:
        source = _source(session, project.environment_id, root)
        source_b = _source(session, project.environment_id, root / "other-source")
        _file(session, source, snapshot, first + child_bytes)
        _file(session, source, part, part_bytes)
        _file(session, source, other, b"invalid unrelated JSON\n")
        _file(session, source_b, root / "other-source" / snapshot.name, _line("job-1", "other source"))
        session.commit()
        read_files = []
        original = reader.iter_system_log_lines

        def track(uri, **kwargs):
            read_files.append(Path(uri).name)
            yield from original(uri, **kwargs)

        monkeypatch.setattr(reader, "iter_system_log_lines", track)
        scope = dict(job_id="job-1", source_id=source.id,
                     log_session_scope="exact", log_session_id="session-a")
        first_page = system_log_records(session, [source, source_b], **scope, limit=2)
        second_page = system_log_records(session, [source, source_b], **scope, limit=2, offset=2)
        assert [row["msg"] for row in first_page["records"] + second_page["records"]] == ["one", "two", "two"]
        assert first_page["total"] == 3
        assert {item["file_uri"] for item in first_page["files"]} == {snapshot.as_posix(), part.as_posix()}
        assert [item["row_count"] for item in first_page["files"]] == [2, 3]
        assert read_files == [snapshot.name, part.name] * 2
        assert not first_page["errors"]
        read_files.clear()
        searched = system_log_records(session, [source], **scope, q="no-match")
        assert searched["total"] == 0
        assert [item["row_count"] for item in searched["files"]] == [2, 3]
        monkeypatch.setattr(system_log_download, "iter_system_log_lines", track)
        read_files.clear()
        prepared = prepare_system_log_download(session, [source, source_b], **scope)
        assert b"".join(prepared.iter_chunks()) == first + child_bytes + duplicate + duplicate
        assert read_files == [snapshot.name, part.name]
        read_files.clear()
        absent = system_log_records(session, [source], job_id="absent-job")
        assert absent == {"records": [], "total": 0, "files": [], "errors": []}
        prepared = prepare_system_log_download(session, [source], job_id="absent-job")
        assert b"".join(prepared.iter_chunks()) == b""
        assert read_files == []


@pytest.mark.parametrize("job,token", [
    (" job-1 ", "job-1"), (" \t ", "default"),
    ("a/b", "a%2Fb"), ("a%2Fb", "a%252Fb"),
    ("công_việc", "c%C3%B4ng_vi%E1%BB%87c"),
    ("job-1_part_00000001", "job-1_part_00000001"),
])
def test_candidate_normalization_keeps_exact_record_identity(
    studio_paths, studio_project_factory, job, token,
):
    project = studio_project_factory()
    root = studio_paths.data_dir / "source"
    path = root / f"system_20261010_010000_1_0_{token}.json"
    with create_session() as session:
        source = _source(session, project.environment_id, root)
        exact = _line(job, "exact", "")
        _file(session, source, path, exact)
        session.commit()
        scope = dict(job_id=job, source_id=source.id, log_session_scope="exact", log_session_id="")
        preview = system_log_records(session, [source], **scope)
        assert [row["msg"] for row in preview["records"]] == ["exact"]
        assert not preview["errors"]
        prepared = prepare_system_log_download(session, [source], **scope)
        assert b"".join(prepared.iter_chunks()) == exact


def test_unparsed_filename_keeps_mixed_jobs_and_sessions_readable(
    studio_paths, studio_project_factory,
):
    project = studio_project_factory()
    root = studio_paths.data_dir / "source"
    content = _line("job-1", "null", None) + _line("job-2", "other") + _line("job-1", "empty", "")
    with create_session() as session:
        source = _source(session, project.environment_id, root)
        _file(session, source, root / "opaque-job-1.json", content)
        session.commit()
        preview = system_log_records(session, [source], job_id="job-1", log_session_scope="null")
        assert [row["msg"] for row in preview["records"]] == ["null"]
        assert preview["files"][0]["row_count"] == 3
        prepared = prepare_system_log_download(session, [source], job_id="job-1", log_session_scope="null")
        assert b"".join(prepared.iter_chunks()) == _line("job-1", "null", None)


@pytest.mark.parametrize("content,code", [
    (b"", None),
    (_line("job-1", "prefix") + b"bad JSON\n", "invalid_json"),
    (_line("job-2", "mismatch"), "system_log_filename_identity_mismatch"),
    (b'{"log_schema_version":4,"_type":"system_log"}\n', "unsupported_log_schema"),
])
def test_selected_file_diagnostics_and_counts(
    studio_paths, studio_project_factory, content, code,
):
    project = studio_project_factory()
    root = studio_paths.data_dir / "source"
    with create_session() as session:
        source = _source(session, project.environment_id, root)
        _file(session, source, root / "system_20261010_010000_1_0_job-1.json", content)
        session.commit()
        preview = system_log_records(session, [source], job_id="job-1")
        assert preview["records"] == []
        assert preview["files"][0]["row_count"] == (1 if code == "system_log_filename_identity_mismatch" else 0)
        assert [item["code"] for item in preview["errors"]] == ([code] if code else [])
        if code:
            with pytest.raises(DownloadAdmissionError):
                prepare_system_log_download(session, [source], job_id="job-1")
        else:
            prepared = prepare_system_log_download(session, [source], job_id="job-1")
            assert b"".join(prepared.iter_chunks()) == b""


def test_indexed_preview_paginates_more_than_500_records_across_parts(
    studio_paths, studio_project_factory, monkeypatch,
):
    project = studio_project_factory()
    root = studio_paths.data_dir / "source"
    original = reader.iter_system_log_lines
    read_files = []

    def track(uri, **kwargs):
        read_files.append(Path(uri).name)
        yield from original(uri, **kwargs)

    with create_session() as session:
        source = _source(session, project.environment_id, root)
        for part_number in (1, 2):
            path = root / f"system_20261010_010000_1_0_job-1_part_{part_number:08d}.json"
            content = b"".join(_line("job-1", str(index)) for index in range((part_number - 1) * 300, part_number * 300))
            _file(session, source, path, content)
        session.commit()
        monkeypatch.setattr(reader, "iter_system_log_lines", track)
        first = system_log_records(session, [source], job_id="job-1", limit=500)
        second = system_log_records(session, [source], job_id="job-1", limit=500, offset=500)
        assert first["total"] == second["total"] == 600
        assert [int(row["msg"]) for row in first["records"] + second["records"]] == list(range(600))
        assert len(read_files) == 4
        assert [file["row_count"] for file in first["files"]] == [300, 300]


def test_selected_missing_file_reports_no_accepted_records(
    studio_paths, studio_project_factory,
):
    project = studio_project_factory()
    root = studio_paths.data_dir / "source"
    path = root / "system_20261010_010000_1_0_job-1.json"
    with create_session() as session:
        source = _source(session, project.environment_id, root)
        _file(session, source, path, _line("job-1", "one"))
        session.commit()
        path.unlink()
        preview = system_log_records(session, [source], job_id="job-1")
        assert preview["records"] == []
        assert preview["files"][0]["row_count"] == 0
        assert preview["errors"][0]["code"] == "storage_read_failed"


def test_job_candidate_query_uses_filename_indexes(studio_project_factory):
    studio_project_factory()
    with create_session() as session:
        statement = system_log_manifest_statement([1], "job-1").compile(
            dialect=session.get_bind().dialect, compile_kwargs={"literal_binds": True},
        )
        query_plan = session.connection().exec_driver_sql(
            "EXPLAIN QUERY PLAN " + str(statement)
        ).fetchall()
        details = "\n".join(str(row[3]) for row in query_plan)
        assert "COVERING INDEX ix_log_file_manifest_source_kind_filename_job " in details
        assert "COVERING INDEX ix_log_file_manifest_source_kind_filename_job_alt " in details
        assert "SCAN log_file_manifest" not in details


@pytest.mark.parametrize("uri,job,matched", [
    ("/logs/job-1/opaque.json", "job-1", False),
    ("s3://job-1/logs/opaque.json", "job-1", False),
    ("/logs/archive-job-1-part2.json", "job-1", True),
    ("/logs/archive-Job-1.json", "job-1", False),
    ("/logs/archive-job_x.json", "job_%", False),
    ("/logs/archive-job_%.json", "job_%", True),
    ("/logs/archive-a%252Fb.json", "a%2Fb", True),
    ("file:///logs/archive-a%252Fb.json", "a/b", True),
    ("s3://bucket/archive-c%C3%B4ng_vi%E1%BB%87c.json", "công_việc", True),
    ("/logs/archive-trailing%2E_part_001.json", "trailing.", True),
    ("/logs/archive.json", " \t ", False),
    ("/logs/archive-default.json", " \t ", True),
])
def test_filename_fallback_is_literal_basename_only(uri, job, matched):
    assert filename_contains_job(uri, job) is matched


def test_filename_fallback_skips_unmatched_contents_and_keeps_all_matches(
    studio_paths, studio_project_factory, monkeypatch,
):
    project = studio_project_factory()
    root = studio_paths.data_dir / "source"
    reads = []
    original = reader.iter_system_log_lines

    def track(uri, **kwargs):
        reads.append(Path(uri).name)
        yield from original(uri, **kwargs)

    with create_session() as session:
        source = _source(session, project.environment_id, root)
        source_b = _source(session, project.environment_id, root / "another")
        first = root / "archive-job-1-a.json"
        second = root / "archive-job-1-b.json"
        _file(session, source, first, _line("job-1", "one"))
        _file(session, source, second, _line("job-1", "two"))
        _file(session, source, root / "unrelated.json", b"invalid JSON")
        _file(session, source, root / "job-1" / "opaque.json", b"invalid JSON")
        _file(session, source_b, root / "another" / "job-1.json", b"invalid JSON")
        # More than one metadata page: missing files must never be opened.
        for index in range(520):
            session.add(LogFileManifest(
                source_id=source.id, file_kind="system_json",
                file_uri=str(root / f"unrelated-{index}.json"), revision_json="{}", status="ok",
            ))
        session.commit()
        monkeypatch.setattr(reader, "iter_system_log_lines", track)
        scope = dict(job_id="job-1", source_id=source.id)
        preview = system_log_records(session, [source, source_b], **scope)
        assert [row["msg"] for row in preview["records"]] == ["one", "two"]
        assert reads == [first.name, second.name]
        assert not preview["errors"]
        monkeypatch.setattr(system_log_download, "iter_system_log_lines", track)
        monkeypatch.setattr(system_log_download, "MANIFEST_PAGE_SIZE", 1)
        reads.clear()
        prepared = prepare_system_log_download(session, [source, source_b], **scope)
        assert b"".join(prepared.iter_chunks()) == _line("job-1", "one") + _line("job-1", "two")
        assert reads == [first.name, second.name]
        reads.clear()
        assert system_log_records(session, [source], job_id="absent") == {
            "records": [], "total": 0, "files": [], "errors": [],
        }
        prepared = prepare_system_log_download(session, [source], job_id="absent")
        assert b"".join(prepared.iter_chunks()) == b""
        assert reads == []


def test_unindexed_unmatched_small_manifest_set_never_reads_contents(
    studio_paths, studio_project_factory, monkeypatch,
):
    project = studio_project_factory()
    root = studio_paths.data_dir / "source"
    reads = []
    original = reader.iter_system_log_lines

    def track(uri, **kwargs):
        reads.append(Path(uri).name)
        yield from original(uri, **kwargs)

    with create_session() as session:
        source = _source(session, project.environment_id, root)
        for index in range(3):
            session.add(LogFileManifest(
                source_id=source.id, file_kind="system_json",
                file_uri=str(root / f"unrelated-{index}.json"),
                revision_json="{}", status="ok",
            ))
        session.commit()
        monkeypatch.setattr(reader, "iter_system_log_lines", track)

        result = system_log_records(session, [source], job_id="absent-job")

        assert result == {"records": [], "total": 0, "files": [], "errors": []}
        assert reads == []


def test_indexed_files_suppress_filename_fallback(
    studio_paths, studio_project_factory,
):
    project = studio_project_factory()
    root = studio_paths.data_dir / "source"
    with create_session() as session:
        source = _source(session, project.environment_id, root)
        _file(session, source, root / "system_20261010_010000_1_0_job-1.json", _line("job-1", "indexed"))
        _file(session, source, root / "archive-job-1.json", b"invalid JSON")
        session.commit()
        preview = system_log_records(session, [source], job_id="job-1")
        assert [row["msg"] for row in preview["records"]] == ["indexed"]
        assert len(preview["files"]) == 1
        assert not preview["errors"]
        prepared = prepare_system_log_download(session, [source], job_id="job-1")
        assert b"".join(prepared.iter_chunks()) == _line("job-1", "indexed")


@pytest.mark.parametrize("indexed", [True, False])
def test_preview_manifest_pages_keep_snapshot_during_sync(
    studio_paths, studio_project_factory, monkeypatch, indexed,
):
    from sqlalchemy import select
    from datacoolie_studio.domains.logs import system_logs, system_log_selection

    project = studio_project_factory()
    root = studio_paths.data_dir / "source"
    with create_session() as session:
        source = _source(session, project.environment_id, root)
        for index in range(3):
            name = (f"system_20261010_01000{index}_1_0_job-1.json" if indexed
                    else f"archive-job-1-{index}.json")
            _file(session, source, root / name, _line("job-1", str(index)))
        session.commit()
        source_id = source.id
        published = False

        def selected_files(*args, **kwargs):
            nonlocal published
            for manifest in system_log_selection.system_log_manifests(*args, **kwargs, page_size=1):
                yield manifest
                if not published:
                    published = True
                    with create_session() as writer:
                        manifests = list(writer.scalars(select(LogFileManifest).where(
                            LogFileManifest.source_id == source_id,
                        ).order_by(LogFileManifest.id)))
                        writer.delete(manifests[1])
                        extra = root / ("system_20261010_010004_1_0_job-1.json" if indexed
                                        else "archive-job-1-new.json")
                        _file(writer, writer.get(EnvironmentSource, source_id), extra, _line("job-1", "new"))
                        writer.commit()

        monkeypatch.setattr(system_logs, "system_log_manifests", selected_files)
        result = system_log_records(session, [source], job_id="job-1")
        assert published
        assert [row["msg"] for row in result["records"]] == ["0", "1", "2"]
        assert len(result["files"]) == 3
        assert not result["errors"]
