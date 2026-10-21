from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from datacoolie_studio.core.time import parse_utc_datetime
from datacoolie_studio.db.models import utc_now
from datacoolie_studio.domains.analytics import (
    cache_state,
    contracts as analytics_contracts,
    dataflow_projection,
    job_projection,
    publication,
    schema,
    schema_tables,
    staging,
    ownership,
)
from datacoolie_studio.domains.analytics.errors import (
    AnalyticsFileChangedDuringPublishError,
    AnalyticsSchemaIncompatibleError,
)
from datacoolie_studio.domains.analytics.sql import (
    quote_identifier,
)

def publish_rows(
    source_id: int,
    dataflow_files: list[analytics_contracts.DataflowFile],
    job_rows: list[tuple[str, str, str, dict[str, Any]]],
    removed_files: list[str],
    changed_files: list[str],
    *,
    database_path: Path,
    source_files: list[dict[str, Any]] | None = None,
    job_files: list[analytics_contracts.JobFile] | None = None,
    finalize_generation: bool = True,
    mark_source: bool = True,
) -> dict[str, Any]:
    if not job_rows:
        return publish_rows_batched(
            source_id,
            [analytics_contracts.LogPublishBatch(
                dataflow_files=tuple(dataflow_files), job_files=tuple(job_files or ()),
                source_files=tuple(source_files or ()),
            )],
            removed_files, changed_files, database_path=database_path,
            finalize_generation=finalize_generation, mark_source=mark_source,
        )
    from datacoolie_studio.domains.analytics import access as analytics_access

    with analytics_access.analytics_maintenance_lock:
        return _publish_rows_locked(
            source_id,
            dataflow_files,
            job_rows,
            removed_files,
            changed_files,
            database_path=database_path,
            source_files=source_files,
            job_files=job_files,
            finalize_generation=finalize_generation,
            mark_source=mark_source,
        )


def publish_rows_batched(
    source_id: int,
    batches: Iterable[analytics_contracts.LogPublishBatch],
    removed_files: list[str],
    changed_files: list[str],
    *,
    database_path: Path,
    on_batch_detached: Callable[[analytics_contracts.LogPublishBatch], None]
    | None = None,
    finalize_generation: bool = True,
    mark_source: bool = True,
) -> dict[str, Any]:
    """Publish bounded log batches in one atomic DuckDB generation.

    The iterator may materialize one batch immediately before yielding it. DuckDB
    copies that batch into temporary relations before the iterator is resumed, so
    the caller can delete its owned raw files without retaining the complete input
    in the local staging directory or in Python row buffers.
    """

    from datacoolie_studio.domains.analytics import access as analytics_access

    with analytics_access.analytics_maintenance_lock:
        return _publish_rows_batched_locked(
            source_id,
            batches,
            removed_files,
            changed_files,
            database_path=database_path,
            on_batch_detached=on_batch_detached,
            finalize_generation=finalize_generation,
            mark_source=mark_source,
        )


def _publish_rows_batched_locked(
    source_id: int,
    batches: Iterable[analytics_contracts.LogPublishBatch],
    removed_files: list[str],
    changed_files: list[str],
    *,
    database_path: Path,
    on_batch_detached: Callable[[analytics_contracts.LogPublishBatch], None]
    | None = None,
    finalize_generation: bool = True,
    mark_source: bool = True,
) -> dict[str, Any]:
    from datacoolie_studio.domains.analytics import access as analytics_access

    path = database_path
    blocked_status = _blocking_upgrade_status(path)
    if blocked_status is not None:
        _close_batch_iterator(batches)
        return {
            "parsed_dataflow_records": 0,
            "parsed_job_records": 0,
            "file_row_counts": {},
            "errors": [
                {
                    "uri": str(path),
                    "message": "A complete analytics cache upgrade is in progress",
                    "code": "analytics_upgrade_in_progress",
                }
            ],
            "published": False,
        }

    if not finalize_generation:
        return _write_batched_duckdb_rows(
            path,
            source_id,
            batches,
            removed_files,
            changed_files,
            on_batch_detached=on_batch_detached,
            finalize_generation=False,
            mark_source=mark_source,
        )
    if analytics_access.schema_rebuild_required(path):
        _close_batch_iterator(batches)
        return _analytics_upgrade_required_result(path)
    return _write_batched_duckdb_rows(
        path,
        source_id,
        batches,
        removed_files,
        changed_files,
        on_batch_detached=on_batch_detached,
        finalize_generation=True,
        mark_source=mark_source,
    )


def _write_batched_duckdb_rows(
    path: Path,
    source_id: int,
    batches: Iterable[analytics_contracts.LogPublishBatch],
    removed_files: list[str],
    changed_files: list[str],
    *,
    on_batch_detached: Callable[[analytics_contracts.LogPublishBatch], None]
    | None = None,
    finalize_generation: bool = True,
    mark_source: bool = True,
) -> dict[str, Any]:
    """Stage all bounded batches, then mutate the target in one transaction."""

    from datacoolie_studio.domains.analytics import access as analytics_access

    path.parent.mkdir(parents=True, exist_ok=True)
    parsed_dataflow_records = 0
    parsed_job_records = 0
    file_row_counts: dict[str, int] = {}
    errors: list[dict[str, str]] = []
    conn = analytics_access.connect(path)
    staged_batches: list[dict[str, Any]] = []
    source_fence_files: list[dict[str, Any]] = []
    source_fence_read_uris: dict[str, str] = {}
    file_admission_summaries: dict[str, dict[str, Any]] = {}
    dataflow_stage_tables: list[str] = []
    dataflow_stage_views: list[str] = []
    job_stage_tables: list[str] = []
    job_stage_views: list[str] = []
    transaction_started = False
    staging_duration_ms = 0.0
    publication_duration_ms = 0.0
    batch_iterator = iter(batches)
    try:
        schema_tables.ensure_tables(conn)
        try:
            staging_started = time.perf_counter()
            for batch_index, batch in enumerate(batch_iterator):
                dataflow_files = tuple(batch.dataflow_files)
                job_files = tuple(batch.job_files)
                # Capture a local descriptor's revision before DuckDB reads it.
                # The post-stage and pre-commit checks below then fence the
                # bytes that were actually admitted into this generation.
                batch_fence_files, batch_fence_read_uris = _source_revision_fence(
                    batch.source_files,
                    [*dataflow_files, *job_files],
                )
                dataflow_stage = staging.stage_native_batch(conn, dataflow_files, kind="dataflow", stage_index=batch_index)
                if dataflow_stage:
                    dataflow_stage_tables.extend(dataflow_stage.tables)
                job_stage = staging.stage_native_batch(conn, job_files, kind="job", stage_index=batch_index)
                if job_stage:
                    job_stage_tables.extend(job_stage.tables)
                for stage in (dataflow_stage, job_stage):
                    if stage:
                        for item, summary in zip(stage.items, stage.summaries):
                            file_admission_summaries[item.canonical_uri] = summary
                _assert_ingest_files_stable(batch_fence_files, batch_fence_read_uris)
                source_fence_files.extend(batch_fence_files)
                source_fence_read_uris.update(batch_fence_read_uris)
                staged_batches.append(
                    {
                        "batch": batch,
                        "dataflow_stage": dataflow_stage,
                        "job_stage": job_stage,
                    }
                )
                if on_batch_detached is not None:
                    on_batch_detached(batch)

            # All stages use one generated schema; inspect each typed relation
            # once. ensure_tables already validated the target before staging.
            for staged in staged_batches:
                for kind in ("dataflow", "job"):
                    stage = staged[f"{kind}_stage"]
                    if stage:
                        _preflight_native_batch(conn, stage, kind=kind)
            staging_duration_ms = round(
                (time.perf_counter() - staging_started) * 1000,
                3,
            )

            # Every local input is checked once more after all source bytes
            # have been staged and before the publication transaction starts.
            _assert_ingest_files_stable(
                source_fence_files,
                source_fence_read_uris,
            )

            publication_started = time.perf_counter()
            conn.execute("BEGIN TRANSACTION")
            transaction_started = True
            replaced = sorted(set([*removed_files, *changed_files]))
            if replaced:
                for table in (schema.DATAFLOW_TABLE, schema.JOB_TABLE):
                    conn.execute(f"DELETE FROM {table} WHERE _source_id = ? AND _file_uri IN (SELECT unnest(?::VARCHAR[]))",
                                 [source_id, replaced])
            for staged in staged_batches:
                for kind in ("dataflow", "job"):
                    stage = staged[f"{kind}_stage"]
                    if stage:
                        insert_native_batch(conn, source_id, stage, kind=kind)
                        for item, row_count in zip(stage.items, stage.counts):
                            file_row_counts[item.canonical_uri] = row_count
                            if kind == "dataflow":
                                parsed_dataflow_records += row_count
                            else:
                                parsed_job_records += row_count
            if mark_source:
                cache_state.mark_cache_source(conn, source_id, refreshed_at=utc_now())
            if finalize_generation:
                publication.refresh_filter_values(conn, source_id)
                publication.publish_generation(
                    conn,
                    dataflow_column_types=schema.DATAFLOW_COLUMN_TYPES,
                    job_column_types=schema.JOB_COLUMN_TYPES,
                    published_at=utc_now(),
                    source_ids=[source_id],
                )
            else:
                cache_state.mark_candidate_building(conn)
            # The source fence must also cover derived writes and generation
            # publication. A local source changing at this point rolls back
            # the transaction rather than exposing a mixed generation.
            _assert_ingest_files_stable(
                source_fence_files,
                source_fence_read_uris,
            )
            conn.execute("COMMIT")
            publication_duration_ms = round(
                (time.perf_counter() - publication_started) * 1000,
                3,
            )
        except Exception as exc:
            if transaction_started:
                conn.execute("ROLLBACK")
            errors.append(
                {
                    "uri": str(path),
                    "message": str(exc),
                    "code": getattr(exc, "code", "publish_failed"),
                }
            )
            parsed_dataflow_records = 0
            parsed_job_records = 0
            file_row_counts.clear()
            file_admission_summaries.clear()
    finally:
        _close_batch_iterator(batch_iterator)
        staging.drop_staged_relations(
            conn,
            tables=[*dataflow_stage_tables, *job_stage_tables],
            views=[*dataflow_stage_views, *job_stage_views],
        )
        conn.close()
    return {
        "parsed_dataflow_records": parsed_dataflow_records,
        "parsed_job_records": parsed_job_records,
        "file_row_counts": file_row_counts,
        "errors": errors,
        "published": not errors,
        "batch_count": len(staged_batches),
        "file_admission_summaries": file_admission_summaries,
        "staging_duration_ms": staging_duration_ms,
        "publication_duration_ms": publication_duration_ms,
    }


def _close_batch_iterator(iterator: Iterable[Any]) -> None:
    close = getattr(iterator, "close", None)
    if callable(close):
        close()


def _publish_rows_locked(
    source_id: int,
    dataflow_files: list[analytics_contracts.DataflowFile],
    job_rows: list[tuple[str, str, str, dict[str, Any]]],
    removed_files: list[str],
    changed_files: list[str],
    *,
    database_path: Path,
    source_files: list[dict[str, Any]] | None = None,
    job_files: list[analytics_contracts.JobFile] | None = None,
    finalize_generation: bool = True,
    mark_source: bool = True,
) -> dict[str, Any]:
    from datacoolie_studio.domains.analytics import access as analytics_access

    path = database_path
    blocked_status = _blocking_upgrade_status(path)
    if blocked_status is not None:
        return {
            "parsed_dataflow_records": 0,
            "file_row_counts": {},
            "errors": [
                {
                    "uri": str(path),
                    "message": "A complete analytics cache upgrade is in progress",
                    "code": "analytics_upgrade_in_progress",
                }
            ],
            "published": False,
        }
    # A complete upgrade deliberately keeps its candidate in ``building`` state
    # between source/batch commits.  It must not be routed through the normal
    # "schema is not ready, discard and rebuild" path on the next batch.
    if not finalize_generation:
        return _write_duckdb_rows(
            path,
            source_id,
            dataflow_files,
            job_rows,
            removed_files,
            changed_files,
            source_files=source_files,
            job_files=job_files,
            finalize_generation=False,
            mark_source=mark_source,
        )
    if analytics_access.schema_rebuild_required(path):
        return _analytics_upgrade_required_result(path)
    return _write_duckdb_rows(
        path,
        source_id,
        dataflow_files,
        job_rows,
        removed_files,
        changed_files,
        source_files=source_files,
        job_files=job_files,
        finalize_generation=True,
        mark_source=mark_source,
    )


def _blocking_upgrade_status(path: Path) -> dict[str, Any] | None:
    return ownership.blocking_upgrade_status(path)


def _analytics_upgrade_required_result(path: Path) -> dict[str, Any]:
    """Route every existing incompatible cache to the complete-cache owner."""

    return {
        "parsed_dataflow_records": 0,
        "parsed_job_records": 0,
        "file_row_counts": {},
        "errors": [
            {
                "uri": str(path),
                "code": "analytics_upgrade_required",
                "message": (
                    "The existing analytics cache requires its complete "
                    "upgrade or rebuild owner before source refresh"
                ),
            }
        ],
        "published": False,
    }


def _write_duckdb_rows(
    path: Path,
    source_id: int,
    dataflow_files: list[analytics_contracts.DataflowFile],
    job_rows: list[tuple[str, str, str, dict[str, Any]]],
    removed_files: list[str],
    changed_files: list[str],
    *,
    source_files: list[dict[str, Any]] | None = None,
    job_files: list[analytics_contracts.JobFile] | None = None,
    finalize_generation: bool = True,
    mark_source: bool = True,
) -> dict[str, Any]:
    from datacoolie_studio.domains.analytics import access as analytics_access

    path.parent.mkdir(parents=True, exist_ok=True)
    parsed_dataflow_records = 0
    file_row_counts: dict[str, int] = {}
    errors: list[dict[str, str]] = []
    conn = analytics_access.connect(path)
    dataflow_stage_tables: tuple[str, ...] = ()
    dataflow_stage_views: tuple[str, ...] = ()
    job_stage_tables: tuple[str, ...] = ()
    job_stage_views: tuple[str, ...] = ()
    parsed_job_records = 0
    transaction_started = False
    try:
        schema_tables.ensure_tables(conn)
        try:
            if job_rows:
                from datacoolie_studio.domains.analytics.log_contract import admit_log_record

                projected_rows = []
                line_counts: dict[str, int] = {}
                for uri, kind, revision, record in job_rows:
                    line_counts[uri] = line_counts.get(uri, 0) + 1
                    line = line_counts[uri]
                    if not admit_log_record(record, expected_type="job_run_log", uri=uri, line=line):
                        continue
                    projected_rows.append((uri, kind, revision, {**record, "_record_line": line}))
                    file_row_counts[uri] = file_row_counts.get(uri, 0) + 1
                job_rows = projected_rows
                parsed_job_records = len(job_rows)
            # Read each kind as one explicit file list before opening the live
            # publication transaction. The temporary tables retain exact
            # filename provenance while avoiding a second scan of source bytes.
            # Capture local descriptor revisions before the native readers open
            # their relations; later checks fence the admitted bytes.
            source_fence_files, source_fence_read_uris = _source_revision_fence(
                source_files or [],
                [*dataflow_files, *(job_files or [])],
            )
            dataflow_stage = staging.stage_native_batch(conn, dataflow_files, kind="dataflow", stage_index=0)
            job_stage = None
            if dataflow_stage:
                dataflow_stage_tables = dataflow_stage.tables
                _preflight_native_batch(conn, dataflow_stage, kind="dataflow")
            job_stage = staging.stage_native_batch(conn, job_files or [], kind="job", stage_index=0)
            if job_stage:
                job_stage_tables = job_stage.tables
                _preflight_native_batch(conn, job_stage, kind="job")
            _assert_ingest_files_stable(
                source_fence_files,
                source_fence_read_uris,
            )
            conn.execute("BEGIN TRANSACTION")
            transaction_started = True
            stale_files = [*removed_files, *changed_files]
            for file_uri in stale_files:
                if schema.table_exists(conn, schema.DATAFLOW_TABLE):
                    conn.execute(f"DELETE FROM {schema.DATAFLOW_TABLE} WHERE _source_id = ? AND _file_uri = ?", [source_id, file_uri])
                if schema.table_exists(conn, schema.JOB_TABLE):
                    conn.execute(f"DELETE FROM {schema.JOB_TABLE} WHERE _source_id = ? AND _file_uri = ?", [source_id, file_uri])
            for kind, stage in (("dataflow", dataflow_stage), ("job", job_stage)):
                if stage:
                    insert_native_batch(conn, source_id, stage, kind=kind)
                    for item, row_count in zip(stage.items, stage.counts):
                        file_row_counts[item.canonical_uri] = row_count
                        if kind == "dataflow":
                            parsed_dataflow_records += row_count
                        else:
                            parsed_job_records += row_count
            if job_rows:
                insert_typed_rows(conn, schema.JOB_TABLE, source_id, job_rows, schema.JOB_COLUMN_TYPES)
            _assert_ingest_files_stable(
                source_fence_files,
                source_fence_read_uris,
            )
            if mark_source:
                cache_state.mark_cache_source(conn, source_id, refreshed_at=utc_now())
            if finalize_generation:
                publication.refresh_filter_values(conn, source_id)
                publication.publish_generation(
                    conn,
                    dataflow_column_types=schema.DATAFLOW_COLUMN_TYPES,
                    job_column_types=schema.JOB_COLUMN_TYPES,
                    published_at=utc_now(),
                    source_ids=[source_id],
                )
            else:
                cache_state.mark_candidate_building(conn)
            _assert_ingest_files_stable(
                source_fence_files,
                source_fence_read_uris,
            )
            conn.execute("COMMIT")
        except Exception as exc:
            if transaction_started:
                conn.execute("ROLLBACK")
            errors.append(
                {
                    "uri": str(path),
                    "message": str(exc),
                    "code": getattr(exc, "code", "publish_failed"),
                }
            )
            parsed_dataflow_records = 0
            parsed_job_records = 0
            file_row_counts.clear()
        finally:
            staging.drop_staged_relations(
                conn,
                tables=[*dataflow_stage_tables, *job_stage_tables],
                views=[*dataflow_stage_views, *job_stage_views],
            )
    finally:
        conn.close()
    return {
        "parsed_dataflow_records": parsed_dataflow_records,
        "parsed_job_records": parsed_job_records,
        "file_row_counts": file_row_counts,
        "errors": errors,
        "published": not errors,
    }


def _file_revision_json(file_uri: str) -> str:
    stat = Path(file_uri).stat()
    return json.dumps({"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}, sort_keys=True)


def _source_revision_fence(
    source_files: Iterable[dict[str, Any]],
    descriptors: Iterable[analytics_contracts.LogFileDescriptor],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Capture the local source revisions that must survive publication.

    Remote descriptors can point at a short-lived materialized ``read_uri``;
    those bytes are already revision-checked by the storage adapter and may be
    removed by ``on_batch_detached``. Only descriptors read from their
    canonical local URI participate in the final DuckDB fence. When callers do
    not provide source inventory entries, capture the local descriptor stat at
    the end of staging so the transaction still has an expected revision.
    """

    fenced_files = [dict(file_state) for file_state in source_files]
    by_canonical = {
        str(file_state["file_uri"]): file_state
        for file_state in fenced_files
        if file_state.get("file_uri") is not None
    }
    read_uris: dict[str, str] = {}
    for descriptor in descriptors:
        canonical_uri = str(descriptor.canonical_uri)
        read_uri = str(descriptor.read_uri)
        read_uris[canonical_uri] = read_uri
        if read_uri != canonical_uri:
            continue
        state = by_canonical.get(canonical_uri)
        if state is None:
            state = {
                "file_uri": canonical_uri,
                "revision_json": _file_revision_json(canonical_uri),
            }
            fenced_files.append(state)
            by_canonical[canonical_uri] = state
        elif not state.get("revision_json"):
            state["revision_json"] = _file_revision_json(canonical_uri)
    return fenced_files, read_uris


def _assert_ingest_files_stable(
    files: list[dict[str, Any]],
    read_uris: dict[str, str],
) -> None:
    """Abort the transaction when bytes changed after candidate discovery."""
    for file_state in files:
        file_uri = str(file_state["file_uri"])
        # The canonical file is the only byte stream this process can fence.
        # A remote descriptor's materialized read URI may have been detached
        # before this final check and is intentionally not re-opened here.
        if read_uris.get(file_uri) != file_uri:
            continue
        expected = str(file_state["revision_json"])
        try:
            actual = _file_revision_json(file_uri)
        except OSError as exc:
            raise AnalyticsFileChangedDuringPublishError(
                f"Log file became unavailable during sync: {file_uri}"
            ) from exc
        if not _revision_equivalent(expected, actual):
            raise AnalyticsFileChangedDuringPublishError(
                f"Log file changed during sync and was not published: {file_uri}"
            )


def _revision_equivalent(left_json: str | None, right_json: str | None) -> bool:
    if left_json is None or right_json is None:
        return left_json == right_json
    try:
        left = json.loads(left_json)
        right = json.loads(right_json)
    except (TypeError, json.JSONDecodeError):
        return left_json == right_json
    return left.get("size") == right.get("size") and left.get("mtime_ns") == right.get("mtime_ns")


def insert_typed_rows(
    conn,
    table_name: str,
    source_id: int,
    rows: list[tuple[str, str, str, dict[str, Any]]],
    column_types: dict[str, str],
) -> None:
    """Insert native records through the same lossless codec as file ingestion."""
    from datacoolie_studio.domains.analytics.record_codec import insert_records

    kind = "dataflow_run_log" if table_name == schema.DATAFLOW_TABLE else "job_run_log"
    line_counts: dict[str, int] = {}
    metadata = []
    for file_uri, file_kind, revision_json, row in rows:
        line_counts[file_uri] = line_counts.get(file_uri, 0) + 1
        event_time = parse_utc_datetime(row.get("__event_time")) or parse_utc_datetime(row.get("end_time")) or parse_utc_datetime(row.get("start_time"))
        file_date = event_time.date() if event_time else None
        if file_date is None:
            match = re.search(r"(20\d{2}[-_/]\d{2}[-_/]\d{2})", file_uri)
            if match:
                file_date = date.fromisoformat(match.group(1).replace("_", "-").replace("/", "-"))
        metadata.append({
            "_source_id": source_id, "_file_uri": file_uri, "_file_kind": file_kind,
            "_file_date": file_date, "_source_size": _revision_value(revision_json, "size"),
            "_source_mtime_ns": _revision_value(revision_json, "mtime_ns"),
            "_ingested_at": utc_now(), "__event_time": event_time, "__run_date": file_date,
            "_record_line": row.get("_record_line", line_counts[file_uri]),
        })
    insert_records(conn, table_name, [row for _, _, _, row in rows], kind=kind, metadata=metadata)



def _preflight_native_batch(conn, stage, *, kind: str) -> None:
    expected = schema.DATAFLOW_COLUMN_TYPES if kind == "dataflow" else schema.JOB_COLUMN_TYPES
    actual = {
        row[0]: row[1] for row in conn.execute(f"DESCRIBE {stage.relation}").fetchall()
    }
    for column, dtype in expected.items():
        if column in schema.GENERATED_CACHE_COLUMNS:
            continue
        if column not in actual or not schema.duckdb_type_matches(actual[column], dtype):
            raise AnalyticsSchemaIncompatibleError(
                f"Invalid staged datatype for {column!r} in {stage.items[0].canonical_uri}"
            )


def insert_native_batch(conn, source_id: int, stage, *, kind: str) -> None:
    """One set-based base write, preserving the existing projection owners."""
    metadata = stage.metadata
    ingested_times = []
    for item in stage.items:
        observed = utc_now()
        if ingested_times and observed <= ingested_times[-1]:
            observed = ingested_times[-1] + timedelta(microseconds=1)
        ingested_times.append(observed)
    values = [
        [item.partition_date or _file_date(item.canonical_uri, {}) if kind == "dataflow" else None for item in stage.items],
        [_revision_value(item.revision_json, "size") for item in stage.items],
        [_revision_value(item.revision_json, "mtime_ns") for item in stage.items],
        ingested_times,
    ]
    # Metadata has one row per descriptor, including empty/excluded files.
    metadata_values = "SELECT unnest(?::DATE[]) AS dataflow_file_date, unnest(?::BIGINT[]) AS source_size, unnest(?::BIGINT[]) AS source_mtime_ns, unnest(?::TIMESTAMPTZ[]) AS ingested_at, unnest(?::BIGINT[]) AS file_key"
    metadata_join = f"{metadata} m JOIN ({metadata_values}) v USING (file_key)"
    source_join = f"{stage.relation} s JOIN ({metadata_join}) ON s.{quote_identifier(stage.file_key_column)} = m.file_key"
    common = [
        "?::BIGINT AS _source_id", "m.canonical_uri AS _file_uri", "m.file_kind AS _file_kind",
        "v.source_size AS _source_size", "v.source_mtime_ns AS _source_mtime_ns",
        "v.ingested_at AS _ingested_at",
    ]
    if kind == "dataflow":
        projection = dataflow_projection.dataflow_source_projection(
            conn, "", file_kind="dataflow_json", source_relation=stage.relation,
            partition_expression="CAST(m.partition_date AS DATE)", column_prefix="s.",
        )
        projection = ", ".join([*common, "v.dataflow_file_date AS _file_date", projection])
        table = schema.DATAFLOW_TABLE
    else:
        source_columns = set(schema.JOB_COLUMN_TYPES) - schema.GENERATED_CACHE_COLUMNS
        payload = [f"s.{quote_identifier(column)} AS {quote_identifier(column)}" for column in source_columns]
        event_columns = source_columns | set(stage.source_types or {})
        event_time = job_projection.job_event_time_expression(event_columns, column_prefix="s.")
        source_run_date = 'TRY_CAST(s."__run_date" AS DATE)' if "__run_date" in event_columns else "NULL::DATE"
        uri_date = job_projection.uri_date_expression("", sql_expression="m.canonical_uri")
        run_date = f"COALESCE(CAST(m.partition_date AS DATE), {source_run_date}, CAST(timezone('UTC', {event_time}) AS DATE), {uri_date})"
        projection = ", ".join([*payload, *common, f"{event_time} AS __event_time", f"{run_date} AS __run_date", f"{run_date} AS _file_date"])
        table = schema.JOB_TABLE
    conn.execute(f"INSERT INTO {table} BY NAME SELECT {projection} FROM {source_join} ORDER BY m.file_key, s._record_line",
                 [source_id, *values, list(range(len(stage.items)))])


def insert_dataflow_file(
    conn,
    source_id: int,
    file_uri: str,
    file_kind: str,
    revision_json: str,
    *,
    read_uri: str | None = None,
    partition_date: str | None = None,
    source_relation: str | None = None,
) -> int:
    _assert_direct_target_schema(conn, kind="dataflow")
    descriptor = analytics_contracts.DataflowFile(file_uri, file_kind, revision_json, read_uri or file_uri, partition_date)
    if source_relation is None:
        stage = staging.stage_native_batch(conn, [descriptor], kind="dataflow", stage_index=0)
        try:
            _preflight_native_batch(conn, stage, kind="dataflow")
            insert_native_batch(conn, source_id, stage, kind="dataflow")
            return stage.counts[0]
        finally:
            staging.drop_staged_relations(conn, tables=stage.tables)
    stage = _single_relation_batch(conn, descriptor, source_relation, kind="dataflow")
    try:
        _preflight_native_batch(conn, stage, kind="dataflow")
        insert_native_batch(conn, source_id, stage, kind="dataflow")
        return stage.counts[0]
    finally:
        staging.drop_staged_relations(conn, tables=stage.tables, views=("__dc_single_dataflow",))


def insert_job_file(
    conn,
    source_id: int,
    file_uri: str,
    file_kind: str,
    revision_json: str,
    *,
    partition_date: str | None = None,
    source_relation: str | None = None,
    source_types: dict[str, str] | None = None,
) -> int:
    if source_relation is None:
        raise AnalyticsSchemaIncompatibleError("Job batch insertion requires a staged JSON relation")
    _assert_direct_target_schema(conn, kind="job")
    descriptor = analytics_contracts.JobFile(file_uri, file_kind, revision_json, file_uri, partition_date)
    stage = _single_relation_batch(conn, descriptor, source_relation, kind="job", source_types=source_types)
    try:
        _preflight_native_batch(conn, stage, kind="job")
        insert_native_batch(conn, source_id, stage, kind="job")
        return stage.counts[0]
    finally:
        staging.drop_staged_relations(conn, tables=stage.tables, views=("__dc_single_job",))



def _assert_direct_target_schema(conn, *, kind):
    table = schema.DATAFLOW_TABLE if kind == "dataflow" else schema.JOB_TABLE
    columns = schema.DATAFLOW_COLUMN_TYPES if kind == "dataflow" else schema.JOB_COLUMN_TYPES
    if not schema.typed_table_schema_is_current(conn, table, columns):
        raise AnalyticsSchemaIncompatibleError(f"{kind.title()} storage requires a complete candidate rebuild")


def _single_relation_batch(conn, item, source_relation, *, kind, source_types=None):
    """Adapt an existing typed relation to the same batch insertion owner."""
    metadata_name = f"__dc_single_{kind}_metadata"
    view_name = f"__dc_single_{kind}"
    expected = schema.DATAFLOW_COLUMN_TYPES if kind == "dataflow" else schema.JOB_COLUMN_TYPES
    key_column = staging.internal_column_name("__dc_file_key", expected)
    count = int(conn.execute(f"SELECT count(*) FROM {source_relation}").fetchone()[0])
    metadata = quote_identifier(metadata_name)
    conn.execute(f"CREATE TEMP TABLE {metadata} AS SELECT 0::BIGINT AS file_key, ?::VARCHAR AS canonical_uri, ?::VARCHAR AS file_kind, ?::VARCHAR AS partition_date", [item.canonical_uri, item.file_kind, item.partition_date])
    try:
        conn.execute(f"CREATE TEMP VIEW {quote_identifier(view_name)} AS SELECT *, 0::BIGINT AS {quote_identifier(key_column)} FROM {source_relation}")
    except Exception:
        conn.execute(f"DROP TABLE {metadata}")
        raise
    return staging.NativeBatchStage(quote_identifier(view_name), metadata, key_column, (item,), (), (count,), (metadata_name,), source_types)


def _revision_value(revision_json: str, key: str) -> Any:
    try:
        revision = json.loads(revision_json)
    except json.JSONDecodeError:
        return None
    return revision.get(key)


def _file_date(file_uri: str, row: dict[str, Any]) -> str | None:
    if row.get("__run_date"):
        return str(row["__run_date"])[:10]
    for key in ("end_time", "start_time"):
        if row.get(key):
            return str(row[key])[:10]
    match = re.search(r"(20\d{2}[-_/]\d{2}[-_/]\d{2})", file_uri)
    return match.group(1).replace("_", "-").replace("/", "-") if match else None
