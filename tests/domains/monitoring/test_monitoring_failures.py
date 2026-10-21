from __future__ import annotations


from datacoolie_studio.domains.monitoring.metrics.investigation import (
    enrich_dataflow_run_for_investigation as _enrich_dataflow_run_for_investigation,
)
from datacoolie_studio.domains.monitoring.metrics.failures_projections import _error_category, _failures_page


def test_error_category_rules_prefer_clear_patterns_and_fallback_to_other():
    assert _error_category("required package 'connectorx' not found. pip install connectorx") == "Dependency"
    assert _error_category("OAuth2 token request failed: [Errno 111] Connection refused") == "Connectivity"
    assert _error_category("authentication failed: invalid token") == "Authentication"
    assert _error_category("table or view `orders` does not exist") == "Missing object"
    assert _error_category("DELTA_FAILED_TO_MERGE_FIELDS: Failed to merge fields 'a' and 'a'") == "Schema / format"
    assert _error_category("replay assertion validation failed") == "Data quality"
    assert _error_category("api_auth__oauth2; api_page__next_link; replay__orders;") == "Other"
    assert _error_category("none") == "Unspecified"

def test_failures_page_groups_by_signature_phase_and_endpoint():
    rows = [
        {
            "job_id": "job-1",
            "dataflow_id": "df-1",
            "dataflow_name": "read_orders",
            "status": "failed",
            "source_status": "failed",
            "stage": "bronze",
            "source_name": "api",
            "destination_name": "lake",
            "source_error_message": "Path does not exist: /landing/orders/2026-06-20",
            "end_time": "2026-06-20T00:00:00+00:00",
        },
        {
            "job_id": "job-2",
            "dataflow_id": "df-1",
            "dataflow_name": "read_orders",
            "status": "failed",
            "source_status": "failed",
            "stage": "bronze",
            "source_name": "api",
            "destination_name": "lake",
            "source_error_message": "Path does not exist: /landing/orders/2026-06-21",
            "end_time": "2026-06-21T00:00:00+00:00",
        },
        {
            "job_id": "job-3",
            "dataflow_id": "df-2",
            "dataflow_name": "write_customers",
            "status": "failed",
            "destination_status": "failed",
            "stage": "silver",
            "source_name": "db",
            "destination_name": "lake",
            "destination_error_message": "DELTA_FAILED_TO_MERGE_FIELDS: failed to merge fields",
            "end_time": "2026-06-21T01:00:00+00:00",
        },
    ]
    jobs = [
        {
            "job_id": "job-1",
            "status": "failed",
            "error_message": "read_orders;",
            "stages": '["read_file", "transform_schema", "load_delta"]',
            "operation_types": "etl",
            "end_time": "2026-06-20T00:01:00+00:00",
        },
        {
            "job_id": "job-4",
            "status": "failed",
            "error_message": "write_customers;",
            "stages": "silver",
            "operation_types": "etl",
            "end_time": "2026-06-21T01:01:00+00:00",
        },
        {
            "job_id": "job-5",
            "status": "failed",
            "error_message": "compact_customers;",
            "stages": "silver",
            "operation_types": "maintenance",
            "end_time": "2026-06-21T02:01:00+00:00",
        }
    ]

    failures = _failures_page(rows, jobs)

    assert failures["kpis"]["failed_dataflows"] == 3
    assert failures["kpis"]["failed_jobs"] == 3
    assert failures["kpis"]["affected_jobs"] == 3
    assert failures["kpis"]["affected_dataflow_jobs"] == 3
    assert failures["kpis"]["affected_job_contexts"] == 3
    assert failures["kpis"]["affected_stages"] == 3
    assert failures["kpis"]["affected_routes"] == 2
    assert failures["kpis"]["repeated_signatures"] == 1
    assert failures["kpis"]["unique_signatures"] == 2
    assert failures["kpis"]["repeated_failure_runs"] == 2
    assert failures["kpis"]["repeated_failure_share"] == 66.67
    assert failures["kpis"]["top_cause_runs"] == 2
    assert failures["kpis"]["top_cause_share"] == 66.67
    assert failures["latest_queue"][0]["failure_phase"] == "destination"
    assert failures["latest_queue"][0]["failure_category"] == "Schema / format"
    category_matrix = {row["category"]: row for row in failures["failure_category_phase_matrix"]}
    assert category_matrix["Missing object"]["source"] == 2
    assert category_matrix["Schema / format"]["destination"] == 1
    stage_matrix = {row["name"]: row for row in failures["failed_by_stage"]}
    assert stage_matrix["bronze"]["count"] == 2
    assert stage_matrix["bronze"]["source"] == 2
    assert stage_matrix["silver"]["destination"] == 1
    top_dataflows = {row["dataflow_name"]: row for row in failures["top_failing_dataflows"]}
    assert top_dataflows["read_orders"]["error_count"] == 2
    assert top_dataflows["read_orders"]["source"] == 2
    assert top_dataflows["write_customers"]["destination"] == 1
    trend_by_date = {row["date"]: row for row in failures["failure_trend_by_date"]}
    assert trend_by_date["2026-06-20"]["failed_jobs"] == 1
    assert trend_by_date["2026-06-20"]["failed_dataflows"] == 1
    assert trend_by_date["2026-06-21"]["failed_dataflows"] == 2

    repeated = failures["repeated_signatures"][0]
    assert repeated["failed_runs"] == 2
    assert repeated["failure_phase"] == "source"
    assert repeated["failure_category"] == "Missing object"
    assert repeated["affected_jobs"] == 2

    endpoint = failures["endpoint_impact"][0]
    assert endpoint["source_name"] == "api"
    assert endpoint["destination_name"] == "lake"
    assert endpoint["failed_runs"] == 2

def test_failure_phase_uses_overhead_when_only_dataflow_error_exists():
    failures = _failures_page(
        [
            {
                "job_id": "job-1",
                "dataflow_id": "df-1",
                "dataflow_name": "orchestrate_orders",
                "status": "failed",
                "stage": "bronze",
                "error_message": "Scheduler timeout while dispatching task",
                "end_time": "2026-06-20T00:00:00+00:00",
            }
        ],
        [
            {
                "job_id": "job-1",
                "status": "failed",
                "error_message": "orchestrate_orders;",
                "end_time": "2026-06-20T00:01:00+00:00",
            }
        ],
    )

    assert failures["latest_queue"][0]["failure_phase"] == "overhead"
    assert failures["latest_queue"][0]["failure_message"] == "Scheduler timeout while dispatching task"
    category_matrix = {row["category"]: row for row in failures["failure_category_phase_matrix"]}
    assert category_matrix["Timeout / throttling"]["overhead"] == 1
    assert failures["kpis"]["top_cause_share"] == 100

def test_failure_phase_does_not_infer_phase_from_top_level_error_message():
    examples = [
        "Failed to read source: table orders was not found",
        "Transformer pipeline failed: invalid expression",
        "Failed to write destination: permission denied",
        "A generic dataflow failure without phase evidence",
    ]

    for index, message in enumerate(examples):
        enriched = _enrich_dataflow_run_for_investigation({
            "dataflow_run_id": f"run-{index}",
            "status": "failed",
            "error_message": message,
        })
        assert enriched["failure_phase"] == "overhead"
        assert enriched["failure_message"] == message

def test_failure_phase_prefers_failed_status_and_its_specific_error():
    specific_error = _enrich_dataflow_run_for_investigation({
        "dataflow_run_id": "run-specific",
        "status": "failed",
        "source_status": "failed",
        "source_error_message": "Source path was not found",
        "error_message": "Failed to write destination: permission denied",
    })
    failed_status = _enrich_dataflow_run_for_investigation({
        "dataflow_run_id": "run-status",
        "status": "failed",
        "transform_status": "failed",
        "error_message": "Failed to write destination: permission denied",
    })

    assert specific_error["failure_phase"] == "source"
    assert specific_error["failure_message"] == "Source path was not found"
    assert failed_status["failure_phase"] == "transform"
    assert failed_status["failure_message"] == "Failed to write destination: permission denied"
