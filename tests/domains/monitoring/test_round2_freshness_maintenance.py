from tests.support.v6_records import insert_native_rows
import json

from datacoolie_studio.domains.analytics import schema as analytics_schema
from datacoolie_studio.domains.monitoring.read_models.maintenance import (
    maintenance_evidence_read_model,
    maintenance_read_model,
)
from tests.domains.monitoring.monitoring_read_model_support import (
    _dataflow_metric_connection,
    _rebuild_serving_facts,
)


_MISSING_STATUS = object()


def _maintenance_payload(
    run_id: str,
    *,
    session_id: str = "session-1",
    files_added: int | None = 0,
    files_removed: int | None = 0,
    bytes_added: int | None = 0,
    bytes_removed: int | None = 0,
    bytes_saved: int | None = 0,
    rows_written: int | None = 0,
    details: str = "compact",
    detail_status: object = "succeeded",
    extra_detail: dict | None = None,
) -> tuple[str, dict]:
    detail = {"operation": details}
    if detail_status is not _MISSING_STATUS:
        detail["status"] = detail_status
    operation_details = [detail]
    if extra_detail is not None:
        operation_details.append(extra_detail)
    raw = {
        "log_schema_version": 6,
        "_type": "dataflow_run_log",
        "log_session_id": session_id,
        "job_id": "maintenance-job",
        "dataflow_id": "maintenance-flow",
        "dataflow_run_id": run_id,
        "operation_type": "maintenance",
        "status": "succeeded",
        "start_time": "2026-10-01T00:00:00Z",
        "end_time": "2026-10-01T00:00:01Z",
        "duration_seconds": 1,
        "destination_name": "destination",
        "destination_connection_type": "lakehouse",
        "destination_format": "delta",
        "destination_table": "curated.orders",
        "attempts": [{
            "status": "succeeded",
            "destination": {
                "operation_type": "maintenance",
                "operation_details": operation_details,
            },
        }],
    }
    payload = {
        **raw,
        "raw_record_json": json.dumps(raw, separators=(",", ":")),
        "destination_files_added": files_added,
        "destination_files_removed": files_removed,
        "destination_bytes_added": bytes_added,
        "destination_bytes_removed": bytes_removed,
        "destination_bytes_saved": bytes_saved,
        "destination_rows_written": rows_written,
        "destination_rows_inserted": 0 if rows_written is not None else None,
        "destination_rows_updated": 0 if rows_written is not None else None,
        "destination_rows_deleted": 0 if rows_written is not None else None,
    }
    return payload["raw_record_json"], payload


def _insert_maintenance(connection, payloads):
    insert_native_rows(
        connection,
        analytics_schema.DATAFLOW_TABLE,
        1,
        [
            (f"{payload['dataflow_run_id']}.parquet", "dataflow_parquet", raw, payload)
            for raw, payload in payloads
        ],
        analytics_schema.DATAFLOW_COLUMN_TYPES,
    )
    _rebuild_serving_facts(connection)


def test_maintenance_no_op_requires_known_zero_effects_and_details():
    connection = _dataflow_metric_connection()
    _insert_maintenance(connection, [
        _maintenance_payload("known-zero"),
        _maintenance_payload(
            "added-files", files_added=2, bytes_added=100,
        ),
        _maintenance_payload(
            "sparse", files_added=None, bytes_added=None,
            files_removed=0, bytes_removed=None, bytes_saved=None,
            rows_written=None,
        ),
        _maintenance_payload("contradictory-details", details="write"),
        _maintenance_payload(
            "positive-detail",
            extra_detail={
                "operation": "compact", "status": "succeeded",
                "metrics": {"bytes_removed": 1},
            },
        ),
    ])

    result = maintenance_read_model(
        [], {}, grain="day", timezone_name="UTC",
        analytics_context=(connection, [1], "round2"),
    )

    assert result["summary"]["no_op_runs"] == 1
    assert result["summary"]["bytes_reclaimed"] is None


def test_maintenance_no_op_requires_explicit_success_detail_status():
    connection = _dataflow_metric_connection()
    payloads = [
        _maintenance_payload("explicit-succeeded", detail_status="succeeded"),
        _maintenance_payload("explicit-success", detail_status="success"),
        _maintenance_payload("missing-status", detail_status=_MISSING_STATUS),
        _maintenance_payload("null-status", detail_status=None),
        _maintenance_payload("empty-status", detail_status=""),
        _maintenance_payload("pending-status", detail_status="pending"),
        _maintenance_payload("failed-status", detail_status="failed"),
    ]
    _insert_maintenance(connection, payloads)

    result = maintenance_read_model(
        [], {}, grain="day", timezone_name="UTC",
        analytics_context=(connection, [1], "round2-status"),
    )

    assert result["summary"]["no_op_runs"] == 2


def test_maintenance_target_registry_consolidates_sessions_and_keeps_latest_provenance():
    connection = _dataflow_metric_connection()
    first_raw, first = _maintenance_payload("run-a", session_id="session-a")
    second_raw, second = _maintenance_payload("run-b", session_id="session-b")
    second["end_time"] = "2026-10-02T00:00:01Z"
    second["raw_record_json"] = second["raw_record_json"].replace(
        "2026-10-01T00:00:01Z", "2026-10-02T00:00:01Z"
    )
    _insert_maintenance(connection, [(first_raw, first), (second["raw_record_json"], second)])

    evidence = maintenance_evidence_read_model(
        [], {}, limit=10, offset=0,
        sort_by="attention_priority", sort_dir="desc",
        analytics_context=(connection, [1], "round2"),
    )
    records = [row for row in evidence["records"] if row["destination_table"] == "curated.orders"]
    assert len(records) == 1
    assert records[0]["run_count"] == 2
    assert records[0]["log_session_id"] == "session-b"
    assert records[0]["dataflow_run_id"] == "run-b"
