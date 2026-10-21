from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from datacoolie_studio.domains.analytics import candidate_state as analytics_candidate_state
from datacoolie_studio.domains.analytics import contracts as analytics_contracts
from datacoolie_studio.domains.analytics import store as analytics_store
from datacoolie_studio.domains.analytics.contracts import DataflowFile, JobFile
from datacoolie_studio.domains.logs import staging as log_staging
from datacoolie_studio.domains.logs import batching
from datacoolie_studio.domains.logs.discovery import DiscoveredLogFile
from datacoolie_studio.domains.storage.adapters import StorageAdapter
from datacoolie_studio.domains.storage.concurrency import map_storage_io


def stage_upgrade_log_batches(
    adapter: StorageAdapter,
    source_id: int,
    target_analytics_path: Path,
    changed_files: list[dict[str, Any]],
    *,
    candidate_by_uri: dict[str, DiscoveredLogFile],
    staging_dir: Path,
    storage_provider: str,
    resume_checkpoint: dict[str, Any] | None = None,
    checkpoint_callback: Callable[[int, dict[str, Any]], None] | None = None,
    removed_file_uris: list[str] | None = None,
) -> dict[str, Any]:
    """Materialize, validate, and commit bounded candidate batches."""

    if removed_file_uris:
        removal = analytics_store.publish_rows(source_id, [], [], removed_file_uris, [], database_path=target_analytics_path, finalize_generation=False)
        if not removal["published"]:
            return {**removal, "batch_count": 0}

    file_row_counts: dict[str, int] = {}
    admission_summaries: dict[str, Any] = {}
    parsed_dataflow_records = 0
    parsed_job_records = 0
    errors: list[dict[str, Any]] = []
    resume_batches = batching.resume_batches(resume_checkpoint)
    saved_files = batching.checkpoint_file_map(resume_checkpoint)
    current_files = {
        str(file_state["file_uri"]): (
            str(file_state["file_kind"]),
            str(file_state["revision_json"]),
        )
        for file_state in changed_files
    }
    if any(
        current_files.get(file_uri) != identity
        for file_uri, identity in saved_files.items()
    ):
        analytics_candidate_state.clear_candidate_source(
            target_analytics_path,
            source_id,
        )
        resume_batches = {}
    skipped_last_batch = False

    batches = batching.file_batches(changed_files)
    if not batches:
        result = analytics_store.publish_rows(
            source_id,
            [],
            [],
            [],
            [],
            database_path=target_analytics_path,
            source_files=[],
            finalize_generation=False,
            mark_source=True,
        )
        return {
            **result,
            "parsed_job_records": 0,
            "batch_count": 1,
        }

    for batch_index, batch in enumerate(batches):
        batch_identity = batching.batch_identity(batch)
        saved_checkpoint = resume_batches.get(batch_identity)
        expected_files = [
            {
                "file_uri": str(file_state["file_uri"]),
                "file_kind": str(file_state["file_kind"]),
                "revision_json": str(file_state["revision_json"]),
            }
            for file_state in batch
        ]
        if (
            saved_checkpoint
            and saved_checkpoint.get("files") == expected_files
            and analytics_candidate_state.candidate_batch_is_complete(
                target_analytics_path,
                source_id,
                expected_files,
                saved_checkpoint.get("file_row_counts") or {},
            )
        ):
            saved_file_counts = saved_checkpoint.get("file_row_counts") or {}
            if isinstance(saved_file_counts, dict):
                file_row_counts.update(
                    {
                        str(uri): int(count or 0)
                        for uri, count in saved_file_counts.items()
                    }
                )
            parsed_dataflow_records += int(
                saved_checkpoint.get("parsed_dataflow_records") or 0
            )
            parsed_job_records += int(saved_checkpoint.get("parsed_job_records") or 0)
            admission_summaries.update(saved_checkpoint.get("file_admission_summaries") or {})
            skipped_last_batch = batch_index == len(batches) - 1
            continue

        result = _stage_one_upgrade_batch(
            adapter,
            source_id,
            target_analytics_path,
            batch,
            batch_index=batch_index,
            batch_count=len(batches),
            batch_identity=batch_identity,
            expected_files=expected_files,
            candidate_by_uri=candidate_by_uri,
            staging_dir=staging_dir,
            storage_provider=storage_provider,
            checkpoint_callback=checkpoint_callback,
        )
        parsed_dataflow_records += int(result.get("parsed_dataflow_records") or 0)
        parsed_job_records += int(result.get("parsed_job_records") or 0)
        file_row_counts.update(result.get("file_row_counts") or {})
        admission_summaries.update(result.get("file_admission_summaries") or {})
        if result.get("errors"):
            errors.extend(result["errors"])
            break

    if not errors and skipped_last_batch:
        marker_result = analytics_store.publish_rows(
            source_id,
            [],
            [],
            [],
            [],
            database_path=target_analytics_path,
            source_files=[],
            finalize_generation=False,
            mark_source=True,
        )
        if marker_result.get("errors"):
            errors.extend(marker_result["errors"])

    return {
        "parsed_dataflow_records": parsed_dataflow_records,
        "parsed_job_records": parsed_job_records,
        "file_row_counts": file_row_counts,
        "errors": errors,
        "published": not errors,
        "batch_count": len(batches),
        "file_admission_summaries": admission_summaries,
    }


def _stage_one_upgrade_batch(
    adapter: StorageAdapter,
    source_id: int,
    target_analytics_path: Path,
    batch: list[dict[str, Any]],
    *,
    batch_index: int,
    batch_count: int,
    batch_identity: str,
    expected_files: list[dict[str, str]],
    candidate_by_uri: dict[str, DiscoveredLogFile],
    staging_dir: Path,
    storage_provider: str,
    checkpoint_callback: Callable[[int, dict[str, Any]], None] | None,
) -> dict[str, Any]:
    def materialize_file(file_state: dict[str, Any]) -> tuple[dict[str, Any], str]:
        file_uri = str(file_state["file_uri"])
        read_uri = log_staging.materialize_log_uri(
            adapter,
            candidate_by_uri[file_uri],
            staging_dir,
            storage_provider,
        )
        return file_state, read_uri

    materialized_files: list[tuple[dict[str, Any], str]] = []
    with batching.log_byte_budget.acquire(batching.batch_bytes(batch)):
        try:
            materialized_files = map_storage_io(adapter, materialize_file, batch)
            dataflow_files: list[DataflowFile] = []
            job_files: list[JobFile] = []
            for file_state, read_uri in materialized_files:
                file_uri = str(file_state["file_uri"])
                file_kind = str(file_state["file_kind"])
                revision_json = str(file_state["revision_json"])
                descriptor = analytics_contracts.LogFileDescriptor(
                    canonical_uri=file_uri,
                    file_kind=file_kind,
                    revision_json=revision_json,
                    read_uri=read_uri,
                    partition_date=(
                        str(file_state["partition_value"])
                        if file_state.get("partition_value") is not None
                        else None
                    ),
                )
                if file_kind == "dataflow_json":
                    dataflow_files.append(descriptor)
                elif file_kind == "job_json":
                    job_files.append(descriptor)

            result = analytics_store.publish_rows(
                source_id,
                dataflow_files,
                [],
                [],
                [str(file_state["file_uri"]) for file_state in batch],
                database_path=target_analytics_path,
                source_files=batch,
                job_files=job_files,
                finalize_generation=False,
                mark_source=batch_index == batch_count - 1,
            )
            if result.get("errors"):
                return result
            if checkpoint_callback is not None:
                checkpoint_callback(
                    source_id,
                    {
                        "status": "completed",
                        "batch_identity": batch_identity,
                        "files": expected_files,
                        "file_row_counts": result.get("file_row_counts") or {},
                        "file_admission_summaries": result.get("file_admission_summaries") or {},
                        "materialization_fingerprint": batching.materialization_fingerprint(),
                        "parsed_dataflow_records": int(
                            result.get("parsed_dataflow_records") or 0
                        ),
                        "parsed_job_records": int(
                            result.get("parsed_job_records") or 0
                        ),
                    },
                )
            return result
        finally:
            for file_state, read_uri in materialized_files:
                if read_uri != str(file_state["file_uri"]):
                    try:
                        Path(read_uri).unlink()
                    except OSError:
                        pass
