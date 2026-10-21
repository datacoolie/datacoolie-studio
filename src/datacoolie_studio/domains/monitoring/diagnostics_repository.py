from __future__ import annotations

from datacoolie_studio.domains.monitoring.dimensions import dimension_text_sql

from datetime import date, datetime
from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.analytics import schema as analytics_schema
from datacoolie_studio.domains.analytics.serving_facts import (
    MONITORING_DATAFLOW_FACTS_TABLE as DATAFLOW_TABLE,
    MONITORING_JOB_FACTS_TABLE as JOB_TABLE,
)
from datacoolie_studio.domains.monitoring.context import reader as analytics_reader
from datacoolie_studio.domains.monitoring.read_models.jobs import (
    reconciliation_metric_summary_sql,
    reconciliation_sql,
    reconciliation_summary_sql,
)
from datacoolie_studio.domains.monitoring.log_repository import (
    monitoring_filter_sql,
    monitoring_requires_comparison_subset,
    monitoring_comparison_subset_restrictions,
    monitoring_requires_child_existence,
    monitoring_job_direct_filters,
)
from datacoolie_studio.domains.monitoring.diagnostics_findings import (
    FINDING_GROUP_DEFAULT_LIMIT,
    FindingGroup,
    FindingsContext,
    finding_group_summary,
    findings_context,
    findings_summary,
    list_finding_groups,
    list_finding_objects,
    validate_finding_request,
    validate_group_kind_filter,
)
from datacoolie_studio.domains.analytics.record_codec import ROOT_MAPPINGS


_FIELD_GROUPS = (
    ("identity/linkage", "dataflow", ("job_id", "dataflow_id", "dataflow_run_id"), "universal"),
    ("time/status", "dataflow", ("status", "start_time", "end_time"), "universal"),
    ("runtime duration", "dataflow", ("duration_seconds",), "universal"),
    ("source evidence", "dataflow", ("start_time", "end_time", "duration_seconds", "status", "rows_read", "selection"), "source"),
    ("transform evidence", "dataflow", ("start_time", "end_time", "duration_seconds", "status"), "transform"),
    ("destination evidence", "dataflow", ("start_time", "end_time", "duration_seconds", "status", "operation_type", "rows_written", "rows_inserted", "rows_updated", "rows_deleted", "files_added", "files_removed", "bytes_added", "bytes_removed", "bytes_saved"), "destination"),
    ("watermark evidence", "dataflow", ("load_status", "loaded_watermark", "merge_base", "candidate", "watermark_to_save", "save_status", "saved_watermark"), "watermark"),
    ("identity/linkage", "job", ("job_id",), "universal"),
    ("time/status", "job", ("status",), "universal"),
    ("runtime duration", "job", ("duration_seconds",), "optional"),
    ("job totals", "job", ("total_dataflows", "total_succeeded", "total_failed", "total_skipped"), "universal"),
    ("runtime context", "job", ("engine_name", "metadata_provider_name", "platform_name"), "optional"),
)

def _schema_node(record_type: str, field: str, phase: str | None = None) -> Any:
    """Resolve a field from the pinned native mapping used by admission.

    Diagnostics must not maintain a second list of required members.  The
    compiled mapping is the source of truth for both v6 and v7 typed records;
    phase fields live below the final attempt struct.
    """

    mapping = ROOT_MAPPINGS[record_type]
    if phase is None:
        return mapping.get(field)
    attempts = mapping.get("attempts")
    if attempts is None or attempts.item is None:
        return None
    phase_node = next((child for child in attempts.item.children if child.name == phase), None)
    if phase_node is None:
        return None
    return next((child for child in phase_node.children if child.name == field), None)


def _schema_required(record_type: str, field: str, phase: str | None = None) -> bool:
    node = _schema_node(record_type, field, phase)
    return bool(node and node.required)



def diagnostics_read_model(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    *,
    grain: str,
    timezone_name: str,
    analytics_context: tuple[Any, list[int], str] | None = None,
) -> dict[str, Any]:
    """Build the Diagnostics Monitoring payload from bounded DuckDB aggregates.

    Diagnostics is intentionally assembled from summary, linkage, and evidence
    aggregates. No request transfers the full dataflow/job fact set into Python.
    """
    if analytics_context is not None:
        return _diagnostics_read_model_from_connection(
            analytics_context[0], analytics_context[1], analytics_context[2], filters, grain, timezone_name,
        )
    with analytics_reader(paths) as (conn, source_ids, generation):
        return _diagnostics_read_model_from_connection(
            conn, source_ids, generation, filters, grain, timezone_name,
        )


def _diagnostics_read_model_from_connection(
    conn: Any,
    source_ids: list[int],
    generation: str,
    filters: dict[str, str],
    grain: str,
    timezone_name: str,
) -> dict[str, Any]:
    if conn is None or not source_ids:
        return _empty_read_model(generation)

    ctes, params = _diagnostics_ctes(source_ids, filters)
    summary = _single_row(conn.execute(_summary_sql(ctes), params))
    effective_grain = _effective_grain(filters, summary, grain)
    linkage_scope = "subset" if monitoring_requires_comparison_subset(filters) else "full"
    linkage_counts = _linkage_counts(conn, ctes, params, scope_coverage=linkage_scope)
    linkage_evidence = (
        _linkage_evidence(conn, ctes, params, scope_coverage=linkage_scope)
        if any(
            linkage_counts.get(category)
            for category in (
                "orphan_dataflow_job_id",
                "job_without_dataflow_records",
                "job_children_unassessed",
            )
        )
        else []
    )
    reconciliation = _reconciliation(
        conn,
        ctes,
        params,
        scope_coverage=linkage_scope,
    )
    reconciliation_by_metric = reconciliation.pop("by_metric", [])
    field_completeness = _field_completeness(conn, ctes, params)
    source_coverage = _source_coverage(conn, ctes, params)
    capture = _diagnostic_capture(conn, ctes, params)
    record_evidence = _record_evidence(conn, ctes, params, effective_grain, timezone_name)

    dataflow_records = int(summary.get("dataflow_records") or 0)
    job_records = int(summary.get("job_records") or 0)
    matched_count = int(linkage_counts.get("matched") or 0)
    orphan_count = int(linkage_counts.get("orphan_dataflow_job_id") or 0)
    job_only_count = int(linkage_counts.get("job_without_dataflow_records") or 0)
    union_count = matched_count + orphan_count + job_only_count + int(
        linkage_counts.get("job_zero_children_expected") or 0
    ) + int(linkage_counts.get("job_children_unassessed") or 0)
    field_issues = sum(
        1
        for item in field_completeness
        if item["actionable"] and float(item["completeness_rate"]) < 95
    )
    required_field_groups = [item for item in field_completeness if item["actionable"]]
    readiness_slots = sum(float(item["records"]) * float(item["required_fields"]) for item in required_field_groups)
    field_readiness_rate = _rate(
        sum(float(item["present_values"]) for item in required_field_groups),
        readiness_slots,
    ) if readiness_slots else None
    health_status = _health_status(
        dataflow_records=dataflow_records,
        job_records=job_records,
        orphan_job_ids=orphan_count,
        jobs_without_dataflow_records=job_only_count,
        reconciliation_mismatches=int(reconciliation["mismatch_count"]),
    )
    observed_loss_records = int(summary.get("log_records_dropped_observed_subtotal") or 0)
    observed_loss_bytes = int(summary.get("log_bytes_dropped_observed_subtotal") or 0)
    unknown_loss_jobs = int(summary.get("log_loss_unknown_jobs") or 0)
    if health_status == "healthy" and (
        observed_loss_records > 0
        or observed_loss_bytes > 0
        or unknown_loss_jobs
        or reconciliation.get("scope_coverage") != "full"
    ):
        health_status = "warning"
    queue, registry_summary = _investigation_queue_result(
        linkage_evidence,
        reconciliation,
        field_completeness,
        capture["evidence"],
        linkage_counts=linkage_counts,
        capture_summary=capture["summary"],
    )
    findings = _findings_context(ctes, params, filters, field_completeness)
    finding_groups = list_finding_groups(
        conn, findings, limit=FINDING_GROUP_DEFAULT_LIMIT, offset=0,
    )
    findings_report = _findings_summary(
        conn, findings, filters, loaded_groups=len(finding_groups),
        generation=generation, has_evidence=bool(dataflow_records or job_records),
    )
    diagnostics = {
        "kpis": {
            "health_status": health_status,
            "matched_job_ids": matched_count,
            "orphan_dataflow_job_ids": orphan_count,
            "jobs_without_dataflow_records": job_only_count,
            "job_linkage_rate": _rate(matched_count, union_count),
            "reconciliation_mismatches": int(reconciliation["mismatch_count"]),
            "affected_reconciliation_jobs": int(reconciliation.get("summary", {}).get("mismatch_jobs_count") or 0),
            # These counters belong to source/readiness control state.  A
            # filtered report has no evidence to assert a zero, so retain the
            # public fields as Unknown until Sources supplies an observation.
            "read_errors": None,
            "cache_warning_count": None,
            "field_readiness_rate": field_readiness_rate,
            "field_readiness_issues": field_issues,
            "conditional_evidence_groups": sum(1 for item in field_completeness if item["applicability"] == "conditional"),
            "log_records_dropped": summary.get("log_records_dropped"),
            "log_bytes_dropped": summary.get("log_bytes_dropped"),
            "log_loss_job_count": int(summary.get("log_loss_job_count") or 0),
            "log_records_dropped_reporting_jobs": int(summary.get("log_records_dropped_reporting_jobs") or 0),
            "log_bytes_dropped_reporting_jobs": int(summary.get("log_bytes_dropped_reporting_jobs") or 0),
            "log_records_dropped_observed_subtotal": summary.get("log_records_dropped_observed_subtotal"),
            "log_bytes_dropped_observed_subtotal": summary.get("log_bytes_dropped_observed_subtotal"),
            "log_loss_unknown_jobs": unknown_loss_jobs,
            "log_loss_coverage": _rate(
                int(summary.get("log_records_dropped_reporting_jobs") or 0),
                int(summary.get("log_loss_job_count") or 0),
            ),
            "log_bytes_loss_coverage": _rate(
                int(summary.get("log_bytes_dropped_reporting_jobs") or 0),
                int(summary.get("log_loss_job_count") or 0),
            ),
            "diagnostic_affected_records": capture["summary"]["affected_records"],
            "diagnostic_known_markers": capture["summary"]["known_markers"],
            "diagnostic_omitted_marker_records": capture["summary"]["omitted_marker_records"],
        },
        "diagnostic_capture": capture,
        "record_evidence_by_date": record_evidence,
        "job_linkage_summary": _linkage_summary(linkage_counts, union_count),
        "assessment": _assessment(
            health_status=health_status,
            summary=summary,
            linkage_counts=linkage_counts,
            reconciliation=reconciliation,
        ),
        "reconciliation_by_metric": reconciliation_by_metric,
        "field_completeness": field_completeness,
        "source_coverage": source_coverage,
        "registry_summary": registry_summary,
        "investigation_queue": queue,
        "finding_groups": finding_groups,
        "findings_summary": findings_report["summary"],
        "scope_notices": findings_report["notices"],
        "read_errors": [],
    }
    coverage = {
        "dataflow_records": dataflow_records,
        "job_records": job_records,
        "linked_job_ids": matched_count,
        "dataflow_job_ids": matched_count + orphan_count,
        "job_ids": union_count,
        "orphan_dataflow_job_ids": orphan_count,
        "jobs_without_dataflow_records": job_only_count,
        "read_errors": None,
        "status": _coverage_status(dataflow_records, job_records),
        "scope_coverage": reconciliation.get("scope_coverage", "full"),
        "log_records_dropped": summary.get("log_records_dropped"),
        "log_bytes_dropped": summary.get("log_bytes_dropped"),
        "log_loss_job_count": int(summary.get("log_loss_job_count") or 0),
        "log_records_dropped_reporting_jobs": int(summary.get("log_records_dropped_reporting_jobs") or 0),
        "log_bytes_dropped_reporting_jobs": int(summary.get("log_bytes_dropped_reporting_jobs") or 0),
        "log_records_dropped_observed_subtotal": summary.get("log_records_dropped_observed_subtotal"),
        "log_bytes_dropped_observed_subtotal": summary.get("log_bytes_dropped_observed_subtotal"),
        "log_loss_unknown_jobs": int(summary.get("log_loss_unknown_jobs") or 0),
        "log_loss_coverage": _rate(
            int(summary.get("log_records_dropped_reporting_jobs") or 0),
            int(summary.get("log_loss_job_count") or 0),
        ),
        "log_bytes_loss_coverage": _rate(
            int(summary.get("log_bytes_dropped_reporting_jobs") or 0),
            int(summary.get("log_loss_job_count") or 0),
        ),
    }
    return {
        "generation": generation,
        "effective_grain": effective_grain,
        "summary": summary,
        "coverage": coverage,
        "reconciliation": reconciliation,
        "diagnostics": diagnostics,
    }


def _empty_read_model(generation: str) -> dict[str, Any]:
    return {
        "generation": generation,
        "effective_grain": "day",
        "summary": {
            "dataflow_records": 0,
            "job_records": 0,
            "date_min": None,
            "date_max": None,
            "latest_log_at": None,
            "earliest_log_at": None,
            "latest_job_log_at": None,
            "latest_dataflow_log_at": None,
            "active_engines": 0,
            "active_metadata_providers": 0,
            "log_loss_job_count": 0,
            "log_records_dropped_reporting_jobs": 0,
            "log_bytes_dropped_reporting_jobs": 0,
            "log_loss_unknown_jobs": 0,
            "log_records_dropped_observed_subtotal": None,
            "log_bytes_dropped_observed_subtotal": None,
        },
        "coverage": {
            "dataflow_records": 0,
            "job_records": 0,
            "linked_job_ids": 0,
            "dataflow_job_ids": 0,
            "job_ids": 0,
            "orphan_dataflow_job_ids": 0,
            "jobs_without_dataflow_records": 0,
            "read_errors": None,
            "status": "no_records",
            "scope_coverage": "none",
            "log_loss_job_count": 0,
            "log_records_dropped_reporting_jobs": 0,
            "log_bytes_dropped_reporting_jobs": 0,
            "log_records_dropped_observed_subtotal": None,
            "log_bytes_dropped_observed_subtotal": None,
            "log_loss_unknown_jobs": 0,
            "log_loss_coverage": None,
        },
        "reconciliation": {
            "status": "no_evidence",
            "scope_coverage": "none",
            "mismatch_count": 0,
            "summary": {
                "total_checks_count": 0,
                "total_jobs_count": 0,
                "comparable_checks_count": 0,
                "comparable_jobs_count": 0,
                "mismatch_checks_count": 0,
                "mismatch_jobs_count": 0,
                "unassessed_checks_count": 0,
                "unassessed_jobs_count": 0,
                "scope_coverage": "none",
                "unassessed_reason": "no_evidence",
            },
            "checks": [],
        },
        "diagnostics": {
            "kpis": {
                "health_status": "no_evidence",
                "matched_job_ids": 0,
                "orphan_dataflow_job_ids": 0,
                "jobs_without_dataflow_records": 0,
                "job_linkage_rate": 0,
                "reconciliation_mismatches": 0,
                "affected_reconciliation_jobs": 0,
                "read_errors": None,
                "cache_warning_count": None,
                "field_readiness_rate": None,
                "field_readiness_issues": 0,
                "conditional_evidence_groups": 0,
                "log_records_dropped": None,
                "log_bytes_dropped": None,
                "log_loss_job_count": 0,
                "log_records_dropped_reporting_jobs": 0,
                "log_bytes_dropped_reporting_jobs": 0,
                "log_records_dropped_observed_subtotal": None,
                "log_bytes_dropped_observed_subtotal": None,
                "log_loss_unknown_jobs": 0,
                "log_loss_coverage": None,
                "log_bytes_loss_coverage": None,
                "diagnostic_affected_records": 0,
                "diagnostic_known_markers": 0,
                "diagnostic_omitted_marker_records": 0,
            },
            "record_evidence_by_date": [],
            "job_linkage_summary": _linkage_summary({}, 0),
            "assessment": {
                "status": "no_evidence",
                "reasons": [{"code": "no_evidence", "severity": "info", "count": 0}],
                "coverage": "none",
            },
            "reconciliation_by_metric": [],
            "field_completeness": [],
            "source_coverage": [],
            "registry_summary": {"total_issues": 0, "loaded_issues": 0, "limit": 200, "truncated": False},
            "investigation_queue": [],
            "finding_groups": [],
            "findings_summary": _empty_findings_summary(generation),
            "scope_notices": [],
            "diagnostic_capture": {"summary": {"affected_records": 0, "known_markers": 0, "omitted_marker_records": 0, "overflow_records": 0, "evidence_records_count": 0, "evidence_limit": 50, "truncated": False}, "by_reason": [], "evidence": []},
            "read_errors": [],
        },
    }


def _empty_findings_summary(generation: str) -> dict[str, Any]:
    return {
        "analytics_generation": generation,
        "scope": "none",
        "issue_groups": 0,
        "warning_groups": 0,
        "limitation_groups": 0,
        "groups_total": 0,
        "groups_loaded": 0,
        "group_limit": FINDING_GROUP_DEFAULT_LIMIT,
        "affected_objects": [],
        "restrictions": [],
    }


def _findings_context(
    ctes: str,
    params: list[Any],
    filters: dict[str, str],
    field_completeness: list[dict[str, Any]],
) -> FindingsContext:
    scope = "subset" if monitoring_requires_comparison_subset(filters) else "full"
    return findings_context(
        _linkage_ctes(ctes, scope_coverage=scope),
        params,
        scope=scope,
        child_relation="filtered_dataflows" if scope != "full" else "comparison_dataflows",
        field_completeness=field_completeness,
    )


def _findings_summary(
    conn: Any,
    context: FindingsContext,
    filters: dict[str, str],
    *,
    loaded_groups: int,
    generation: str,
    has_evidence: bool,
) -> dict[str, Any]:
    result = findings_summary(conn, context)
    counts = result["group_counts"]
    return {
        "summary": {
            "analytics_generation": generation,
            "scope": context.scope if has_evidence else "none",
            "issue_groups": counts["issue"],
            "warning_groups": counts["warning"],
            "limitation_groups": counts["limitation"],
            "groups_total": sum(counts.values()),
            "groups_loaded": loaded_groups,
            "group_limit": FINDING_GROUP_DEFAULT_LIMIT,
            "affected_objects": result["affected_objects"],
            "restrictions": monitoring_comparison_subset_restrictions(filters),
        },
        "notices": [
            {**notice, "restrictions": monitoring_comparison_subset_restrictions(filters)}
            for notice in result["notices"]
        ],
    }


def diagnostics_findings_evidence(
    paths: list[EnvironmentSource],
    filters: dict[str, str],
    *,
    view: str,
    group: FindingGroup | None,
    limit: int,
    offset: int,
    sort_by: str | None,
    sort_dir: str | None,
    kind_filter: str | None = None,
    analytics_context: tuple[Any, list[int], str] | None = None,
) -> dict[str, Any]:
    """Page finding groups or one group's affected objects from the full population."""

    validate_finding_request(
        view, group.key if group else None, sort_by, sort_dir, kind_filter,
    )
    if analytics_context is not None:
        return _findings_evidence_from_connection(
            *analytics_context, filters, view, group, limit, offset, sort_by, sort_dir, kind_filter,
        )
    with analytics_reader(paths) as reader_context:
        return _findings_evidence_from_connection(
            *reader_context, filters, view, group, limit, offset, sort_by, sort_dir, kind_filter,
        )


def _findings_evidence_from_connection(
    conn: Any,
    source_ids: list[int],
    generation: str,
    filters: dict[str, str],
    view: str,
    group: FindingGroup | None,
    limit: int,
    offset: int,
    sort_by: str | None,
    sort_dir: str | None,
    kind_filter: str | None,
) -> dict[str, Any]:
    empty = {"generation": generation, "group": None, "groups": [], "objects": [], "total": 0}
    if conn is None or not source_ids:
        return empty
    ctes, params = _diagnostics_ctes(source_ids, filters)
    needs_fields = view == "registry" or (group is not None and group.rule == "field_completeness")
    context = _findings_context(
        ctes, params, filters,
        _field_completeness(conn, ctes, params) if needs_fields else [],
    )
    if view == "registry":
        groups = list_finding_groups(
            conn, context, limit=limit, offset=offset, sort_by=sort_by,
            sort_dir=sort_dir, kind_filter=kind_filter,
        )
        counts = findings_summary(conn, context)["group_counts"]
        total = sum(counts[kind] for kind in validate_group_kind_filter(kind_filter))
        return {**empty, "groups": groups, "total": total}
    assert group is not None
    selected = finding_group_summary(conn, context, group)
    if selected is None:
        return empty
    objects = list_finding_objects(
        conn, context, group, limit=limit, offset=offset, sort_by=sort_by, sort_dir=sort_dir,
    )
    return {
        **empty,
        "group": selected,
        "objects": objects,
        "total": selected["affected_objects"],
    }


def _diagnostics_ctes(source_ids: list[int], filters: dict[str, str]) -> tuple[str, list[Any]]:
    placeholders = ", ".join("?" for _ in source_ids)
    dataflow_where, dataflow_params = monitoring_filter_sql(
        filters,
        "d",
        "jl",
        dataflow_table=DATAFLOW_TABLE,
        dataflow_event_time_column="event_time",
    )
    job_where, job_params = monitoring_filter_sql(
        monitoring_job_direct_filters(filters),
        "j",
        "j",
        include_dataflow_filters=False,
        dataflow_table=DATAFLOW_TABLE,
    )
    job_scope = (
        " AND EXISTS ("
        "SELECT 1 FROM filtered_dataflows df "
        "WHERE df._source_id = j._source_id AND df.job_id = j.job_id "
        "AND df.log_session_id IS NOT DISTINCT FROM j.log_session_id"
        ")"
        if monitoring_requires_child_existence(filters)
        else ""
    )
    # Lexical IDs including empty text and ``unknown`` remain valid
    # producer identities.  The source/session pair is part of every linkage
    # key because the same job ID may be reused across sources or sessions.
    normalized_job_id = "CAST({alias}.job_id AS VARCHAR)"
    sql = f"""
        WITH job_lookup AS (
          SELECT _source_id, log_session_id, job_id,
                 ARG_MAX(engine_name, COALESCE(observed_at, event_time)) AS engine_name,
                 ARG_MAX(metadata_provider_name, COALESCE(observed_at, event_time)) AS metadata_provider_name,
                 ARG_MAX(platform_name, COALESCE(observed_at, event_time)) AS platform_name
          FROM {JOB_TABLE}
          WHERE _source_id IN ({placeholders}) AND job_id IS NOT NULL
          GROUP BY _source_id, log_session_id, job_id
        ),
        filtered_dataflows AS (
          SELECT
            d._source_id, d.log_session_id, d._file_uri, d._file_kind, d._ingested_at,
            d._record_line, 0::HUGEINT AS log_records_dropped,
            0::HUGEINT AS log_bytes_dropped,
            d.log_schema_version,
            d.job_id AS raw_job_id,
            {normalized_job_id.format(alias='d')} AS job_id,
            d.dataflow_id, d.dataflow_run_id, d.dataflow_name,
            lower(COALESCE(NULLIF(CAST(d.status AS VARCHAR), ''), 'unknown')) AS status,
            lower(COALESCE(NULLIF(CAST(d.status AS VARCHAR), ''), 'unknown')) AS normalized_status,
            d.start_time, d.end_time,
            d.event_time AS event_time,
            COALESCE(d.run_date, CAST(timezone('UTC', d.event_time) AS DATE)) AS run_date,
            d.duration_seconds, d.source_duration_seconds, d.transform_duration_seconds,
            d.destination_duration_seconds, d.source_name, d.source_connection_type,
            d.source_rows_read AS source_rows_read,
            d.destination_name, d.destination_connection_type,
            d.destination_load_type, d.source_watermark_columns,
            ds.read_request AS read_request,
            ds.attempts AS attempts,
            ds.diagnostic_issues AS diagnostic_issues,
            ds.attempts[-1].watermark AS watermark_evidence,
            d.destination_operation_type,
            d.destination_files_removed AS destination_files_removed,
            d.destination_bytes_removed AS destination_bytes_removed
          FROM {DATAFLOW_TABLE} d
          LEFT JOIN {analytics_schema.DATAFLOW_TABLE} ds
            ON ds._source_id = d._source_id
           AND ds._file_uri = d._file_uri
           AND ds._record_line = d._record_line
          LEFT JOIN job_lookup jl
            ON jl._source_id = d._source_id
           AND jl.log_session_id IS NOT DISTINCT FROM d.log_session_id
           AND jl.job_id = d.job_id
          WHERE d._source_id IN ({placeholders}){dataflow_where}
        ),
        filtered_jobs AS (
          SELECT
            j._source_id, j.log_session_id, j._file_uri, j._file_kind, j._ingested_at,
            j._record_line, j.log_records_dropped, j.log_bytes_dropped, j.observed_at,
            j.log_schema_version,
            {normalized_job_id.format(alias='j')} AS job_id,
            lower(COALESCE(NULLIF(CAST(j.status AS VARCHAR), ''), 'unknown')) AS status,
            lower(COALESCE(NULLIF(CAST(j.status AS VARCHAR), ''), 'unknown')) AS normalized_status,
            TRY_CAST(j.start_time AS TIMESTAMPTZ) AS start_time,
            TRY_CAST(j.end_time AS TIMESTAMPTZ) AS end_time,
            j.event_time AS event_time,
            j.duration_seconds, j.engine_name, j.metadata_provider_name, j.platform_name,
            j.total_dataflows, j.total_succeeded, j.total_failed, j.total_skipped
          FROM {JOB_TABLE} j
          WHERE j._source_id IN ({placeholders}){job_where}{job_scope}
        ),
        comparison_dataflows AS (
          SELECT
            d2._source_id,
            d2.log_session_id,
            CAST(d2.job_id AS VARCHAR) AS job_id,
            LOWER(COALESCE(NULLIF(CAST(d2.status AS VARCHAR), ''), 'unknown')) AS normalized_status,
            d2.event_time,
            CASE WHEN fd._source_id IS NOT NULL THEN TRUE ELSE FALSE END AS in_filter
          FROM {DATAFLOW_TABLE} d2
          INNER JOIN (
            SELECT DISTINCT _source_id, log_session_id, job_id
            FROM filtered_jobs
            WHERE job_id IS NOT NULL
          ) selected_jobs
            ON selected_jobs._source_id = d2._source_id
           AND selected_jobs.log_session_id IS NOT DISTINCT FROM d2.log_session_id
           AND selected_jobs.job_id = CAST(d2.job_id AS VARCHAR)
          LEFT JOIN filtered_dataflows fd
            ON fd._source_id = d2._source_id
           AND fd.log_session_id IS NOT DISTINCT FROM d2.log_session_id
           AND fd._file_uri = d2._file_uri
           AND fd._record_line = d2._record_line
          WHERE d2._source_id IN ({placeholders})
        )
    """
    return sql, [*source_ids, *source_ids, *dataflow_params, *source_ids, *job_params, *source_ids]


def _summary_sql(ctes: str) -> str:
    return f"""
        {ctes}
        SELECT
          (SELECT COUNT(*) FROM filtered_dataflows) AS dataflow_records,
          (SELECT COUNT(*) FROM filtered_jobs) AS job_records,
          (SELECT MIN(run_date) FROM (
            SELECT run_date FROM filtered_dataflows
            UNION ALL
            SELECT CAST(timezone('UTC', event_time) AS DATE) AS run_date FROM filtered_jobs
          )) AS date_min,
          (SELECT MAX(run_date) FROM (
            SELECT run_date FROM filtered_dataflows
            UNION ALL
            SELECT CAST(timezone('UTC', event_time) AS DATE) AS run_date FROM filtered_jobs
          )) AS date_max,
          (SELECT MAX(event_time) FROM (SELECT event_time FROM filtered_dataflows UNION ALL SELECT event_time FROM filtered_jobs)) AS latest_log_at,
          (SELECT MIN(event_time) FROM (SELECT event_time FROM filtered_dataflows UNION ALL SELECT event_time FROM filtered_jobs)) AS earliest_log_at,
          (SELECT MAX(event_time) FROM filtered_jobs) AS latest_job_log_at,
          (SELECT MAX(event_time) FROM filtered_dataflows) AS latest_dataflow_log_at,
          (SELECT COUNT(DISTINCT {dimension_text_sql('engine_name')}) FROM filtered_jobs) AS active_engines,
          (SELECT COUNT(DISTINCT {dimension_text_sql('metadata_provider_name')}) FROM filtered_jobs) AS active_metadata_providers,
          (SELECT COUNT(*) FROM filtered_jobs) AS log_loss_job_count,
          (SELECT COUNT(*) FILTER (WHERE log_records_dropped IS NOT NULL) FROM filtered_jobs) AS log_records_dropped_reporting_jobs,
          (SELECT COUNT(*) FILTER (WHERE log_bytes_dropped IS NOT NULL) FROM filtered_jobs) AS log_bytes_dropped_reporting_jobs,
          (SELECT COUNT(*) FILTER (
             WHERE log_records_dropped IS NULL OR log_bytes_dropped IS NULL
           ) FROM filtered_jobs) AS log_loss_unknown_jobs,
          (SELECT SUM(log_records_dropped) FILTER (WHERE log_records_dropped IS NOT NULL) FROM filtered_jobs) AS log_records_dropped_observed_subtotal,
          (SELECT SUM(log_bytes_dropped) FILTER (WHERE log_bytes_dropped IS NOT NULL) FROM filtered_jobs) AS log_bytes_dropped_observed_subtotal,
          (SELECT CASE WHEN COUNT(*) = COUNT(log_records_dropped)
                       THEN SUM(log_records_dropped) ELSE NULL END
             FROM filtered_jobs) AS log_records_dropped,
          (SELECT CASE WHEN COUNT(*) = COUNT(log_bytes_dropped)
                       THEN SUM(log_bytes_dropped) ELSE NULL END
             FROM filtered_jobs) AS log_bytes_dropped
    """


def _linkage_ctes(ctes: str, *, scope_coverage: str = "full") -> str:
    child_scope = "TRUE" if scope_coverage == "full" else "FALSE"
    return f"""
        {ctes},
        filtered_dataflow_by_job AS (
          SELECT _source_id, log_session_id, job_id,
                 COUNT(*) AS dataflow_records, MAX(event_time) AS latest_dataflow_time
          FROM filtered_dataflows
          WHERE job_id IS NOT NULL
          GROUP BY _source_id, log_session_id, job_id
        ),
        recorded_dataflow_by_job AS (
          SELECT _source_id, log_session_id, job_id,
                 COUNT(*) AS recorded_dataflow_records,
                 MAX(event_time) AS latest_recorded_dataflow_time,
                 COUNT(*) FILTER (WHERE in_filter = FALSE) AS excluded_dataflow_records
          FROM comparison_dataflows
          WHERE job_id IS NOT NULL
          GROUP BY _source_id, log_session_id, job_id
        ),
        dataflow_by_job AS (
          SELECT
            COALESCE(fd._source_id, rd._source_id) AS _source_id,
            COALESCE(fd.log_session_id, rd.log_session_id) AS log_session_id,
            COALESCE(fd.job_id, rd.job_id) AS job_id,
            fd.dataflow_records AS dataflow_records,
            fd.latest_dataflow_time AS latest_dataflow_time,
            fd.dataflow_records AS filtered_dataflow_records,
            rd.recorded_dataflow_records,
            rd.latest_recorded_dataflow_time,
            rd.excluded_dataflow_records
          FROM filtered_dataflow_by_job fd
          FULL OUTER JOIN recorded_dataflow_by_job rd
            ON rd._source_id = fd._source_id
           AND rd.log_session_id IS NOT DISTINCT FROM fd.log_session_id
           AND rd.job_id = fd.job_id
        ),
        jobs_by_id AS (
          SELECT _source_id, log_session_id, job_id,
                 ARG_MAX(status, event_time) AS job_status,
                 ARG_MAX(total_dataflows, event_time) AS job_total_dataflows,
                 ARG_MAX(log_records_dropped, event_time) AS job_log_records_dropped,
                 ARG_MAX(log_bytes_dropped, event_time) AS job_log_bytes_dropped,
                 ARG_MAX(observed_at, event_time) AS job_observed_at,
                 MAX(event_time) AS latest_job_time
          FROM filtered_jobs
          WHERE job_id IS NOT NULL
          GROUP BY _source_id, log_session_id, job_id
        ),
        linkage_base AS (
          SELECT
            COALESCE(df._source_id, j._source_id) AS _source_id,
            COALESCE(df.log_session_id, j.log_session_id) AS log_session_id,
            COALESCE(df.job_id, j.job_id) AS job_id,
            df.dataflow_records,
            df.latest_dataflow_time,
            df.filtered_dataflow_records,
            df.recorded_dataflow_records,
            df.latest_recorded_dataflow_time,
            df.excluded_dataflow_records,
            j.job_status,
            j.job_total_dataflows,
            j.job_log_records_dropped,
            j.job_observed_at,
            j.latest_job_time,
            CASE
              WHEN df.job_id IS NOT NULL AND j.job_id IS NOT NULL
                   AND COALESCE(df.filtered_dataflow_records, 0) > 0 THEN 'matched'
              WHEN df.job_id IS NOT NULL AND j.job_id IS NOT NULL THEN 'job_children_unassessed'
              WHEN df.job_id IS NOT NULL THEN 'orphan_dataflow_job_id'
              WHEN {child_scope} = FALSE THEN 'job_children_unassessed'
              WHEN j.job_status IS NULL OR j.job_status NOT IN ('succeeded', 'failed', 'skipped') THEN 'job_children_unassessed'
              WHEN j.job_total_dataflows IS NULL THEN 'job_children_unassessed'
              WHEN j.job_log_records_dropped IS NULL OR j.job_log_records_dropped > 0 THEN 'job_children_unassessed'
              WHEN j.job_observed_at IS NULL THEN 'job_children_unassessed'
              WHEN COALESCE(df.excluded_dataflow_records, 0) > 0 THEN 'job_children_unassessed'
              WHEN df.latest_recorded_dataflow_time IS NOT NULL
                   AND j.job_observed_at < df.latest_recorded_dataflow_time THEN 'job_children_unassessed'
              WHEN j.job_total_dataflows = 0 THEN 'job_zero_children_expected'
              ELSE 'job_without_dataflow_records'
            END AS category
          FROM dataflow_by_job df
          FULL OUTER JOIN jobs_by_id j
            ON j._source_id = df._source_id
           AND j.log_session_id IS NOT DISTINCT FROM df.log_session_id
           AND j.job_id = df.job_id
        ),
        linkage AS (
          SELECT linkage_base.*,
                 CASE
                   WHEN category <> 'job_children_unassessed' THEN NULL
                   WHEN {child_scope} = FALSE THEN 'filtered_child_scope'
                   WHEN job_status IS NULL OR job_status NOT IN ('succeeded', 'failed', 'skipped') THEN 'nonterminal'
                   WHEN job_log_records_dropped IS NULL THEN 'unknown_loss'
                   WHEN job_log_records_dropped > 0 THEN 'lost_detail'
                   WHEN job_observed_at IS NULL THEN 'unknown_snapshot'
                   WHEN latest_recorded_dataflow_time IS NOT NULL
                        AND job_observed_at < latest_recorded_dataflow_time THEN 'stale_snapshot'
                   WHEN job_total_dataflows IS NULL THEN 'unknown_total'
                   WHEN COALESCE(excluded_dataflow_records, 0) > 0 THEN 'range_incomplete'
                   ELSE 'unassessed'
                 END AS unassessed_reason,
                 CASE WHEN {child_scope} = FALSE THEN 'subset'
                      ELSE 'insufficient_coverage' END AS unassessed_scope
          FROM linkage_base
        )
    """


def _linkage_counts(
    conn: Any,
    ctes: str,
    params: list[Any],
    *,
    scope_coverage: str = "full",
) -> dict[str, int]:
    result = conn.execute(
        f"""
        {_linkage_ctes(ctes, scope_coverage=scope_coverage)}
        SELECT category, COUNT(*) AS count FROM linkage GROUP BY category
        """,
        params,
    )
    return {str(row["category"]): int(row["count"] or 0) for row in _rows(result)}


def _linkage_evidence(
    conn: Any,
    ctes: str,
    params: list[Any],
    *,
    scope_coverage: str = "full",
) -> list[dict[str, Any]]:
    result = conn.execute(
        f"""
        {_linkage_ctes(ctes, scope_coverage=scope_coverage)},
        ranked AS (
          SELECT *, row_number() OVER (PARTITION BY category ORDER BY job_id) AS row_number
          FROM linkage
          WHERE category NOT IN ('matched', 'job_zero_children_expected')
        )
          SELECT category, _source_id, log_session_id, job_id, job_status, dataflow_records,
                 filtered_dataflow_records, recorded_dataflow_records,
                 latest_recorded_dataflow_time, excluded_dataflow_records,
                 job_total_dataflows, job_log_records_dropped, job_observed_at,
                 unassessed_reason, unassessed_scope,
               COALESCE(latest_dataflow_time, latest_job_time) AS latest_time
        FROM ranked
        WHERE row_number <= 200
        ORDER BY CASE category
          WHEN 'orphan_dataflow_job_id' THEN 0
          ELSE 2
        END, job_id
        """,
        params,
    )
    evidence = []
    for row in _rows(result):
        category = str(row["category"])
        evidence.append({
            "category": category,
            "_source_id": row["_source_id"],
            "log_session_id": row["log_session_id"],
            "job_id": row["job_id"],
            "job_status": row["job_status"] if category != "orphan_dataflow_job_id" else "missing_job_log",
            "dataflow_records": int(row["dataflow_records"] or 0),
            "filtered_dataflow_records": int(row["filtered_dataflow_records"] or 0),
            "recorded_dataflow_records": int(row["recorded_dataflow_records"] or 0),
            "latest_recorded_dataflow_time": row.get("latest_recorded_dataflow_time"),
            "excluded_dataflow_records": int(row["excluded_dataflow_records"] or 0),
            "job_total_dataflows": int(row["job_total_dataflows"]) if row.get("job_total_dataflows") is not None else None,
            "job_log_records_dropped": int(row["job_log_records_dropped"]) if row.get("job_log_records_dropped") is not None else None,
            "job_observed_at": row.get("job_observed_at"),
            "unassessed_reason": row.get("unassessed_reason"),
            "unassessed_scope": row.get("unassessed_scope"),
            "latest_time": row["latest_time"],
        })
    return evidence


def _reconciliation(
    conn: Any,
    ctes: str,
    params: list[Any],
    *,
    scope_coverage: str = "full",
) -> dict[str, Any]:
    relation = "filtered_dataflows" if scope_coverage != "full" else "comparison_dataflows"
    values = _rows(
        conn.execute(
            f"{ctes} {reconciliation_sql(scope_coverage, child_relation=relation)}",
            params,
        )
    )
    summary_rows = _rows(
        conn.execute(
            f"{ctes} {reconciliation_summary_sql(scope_coverage, child_relation=relation)}",
            params,
        )
    )
    metric_rows = _rows(
        conn.execute(
            f"{ctes} {reconciliation_metric_summary_sql(scope_coverage, child_relation=relation)}",
            params,
        )
    )
    summary = summary_rows[0] if summary_rows else {}
    mismatch_count = int(
        summary["mismatch_checks_count"]
        if "mismatch_checks_count" in summary
        else (values[0].get("mismatch_count") if values else 0)
        or 0
    )
    insufficient_count = int(
        summary["unassessed_checks_count"]
        if "unassessed_checks_count" in summary
        else (values[0].get("insufficient_count") if values else 0)
        or 0
    )
    checks = [{key: value for key, value in row.items() if key not in {"mismatch_count", "insufficient_count"}} for row in values]
    by_metric = []
    for row in metric_rows:
        by_metric.append({
            "metric": row.get("metric"),
            "total_checks_count": int(row.get("total_checks_count") or 0),
            "comparable_checks_count": int(row.get("comparable_checks_count") or 0),
            "mismatch_count": int(row.get("mismatch_count") or 0),
            "affected_jobs": int(row.get("affected_jobs") or 0),
            "absolute_difference": int(row.get("absolute_difference") or 0),
            "unassessed_checks_count": int(row.get("unassessed_checks_count") or 0),
            "unassessed_jobs_count": int(row.get("unassessed_jobs_count") or 0),
            "severity": row.get("severity") or "info",
        })
    summary = {
        "total_checks_count": int(summary.get("total_checks_count") or 0),
        "total_jobs_count": int(summary.get("total_jobs_count") or 0),
        "comparable_checks_count": int(summary.get("comparable_checks_count") or 0),
        "comparable_jobs_count": int(summary.get("comparable_jobs_count") or 0),
        "mismatch_checks_count": int(summary.get("mismatch_checks_count") or 0),
        "mismatch_jobs_count": int(summary.get("mismatch_jobs_count") or 0),
        "unassessed_checks_count": int(summary.get("unassessed_checks_count") or 0),
        "unassessed_jobs_count": int(summary.get("unassessed_jobs_count") or 0),
        "scope_coverage": summary.get("scope_coverage") or scope_coverage,
        "unassessed_reason": summary.get("unassessed_reason"),
    }
    has_population = summary["total_checks_count"] > 0
    if not has_population:
        summary["scope_coverage"] = "none"
        summary["unassessed_reason"] = "no_evidence"
    public_scope = (
        "none"
        if not has_population
        else "subset"
        if scope_coverage != "full"
        else "insufficient_coverage"
        if insufficient_count
        else "full"
    )
    return {
        "status": "warning" if mismatch_count else "insufficient_coverage" if insufficient_count else "ok" if has_population else "no_evidence",
        "scope_coverage": public_scope,
        "mismatch_count": mismatch_count,
        "summary": summary,
        "by_metric": by_metric,
        "checks": checks,
    }


def _field_completeness(conn: Any, ctes: str, params: list[Any]) -> list[dict[str, Any]]:
    def presence_expression(record_type: str, field: str, phase: str | None) -> str:
        if _schema_required(record_type, field, phase):
            # A required member is structurally present when the record uses a
            # pinned v6/v7 schema, even when its admitted value is NULL. An
            # unrecognised version cannot establish presence, so leave it
            # outside the presence denominator rather than calling it missing.
            return "CASE WHEN TRY_CAST(log_schema_version AS INTEGER) IN (6, 7) THEN 1 ELSE NULL END"
        return "NULL::BIGINT"

    def known_presence_expression(record_type: str, field: str, phase: str | None) -> str:
        if _schema_required(record_type, field, phase):
            return "CASE WHEN TRY_CAST(log_schema_version AS INTEGER) IN (6, 7) THEN 1 ELSE 0 END"
        return "0::BIGINT"

    def availability_expression(record_type: str, field: str, phase: str | None) -> str:
        if phase:
            parent = f"attempts[-1].{phase}"
            member = f"{parent}.{field}"
            return f"CASE WHEN {parent} IS NOT NULL AND {member} IS NOT NULL THEN 1 ELSE 0 END"
        return f"CASE WHEN {field} IS NOT NULL THEN 1 ELSE 0 END"

    groups = []
    aggregates = []
    for index, (group, record_type, fields, applicability) in enumerate(_FIELD_GROUPS):
        phase = applicability if applicability not in {"universal", "optional"} else None
        relation = "filtered_dataflows" if record_type == "dataflow" else "filtered_jobs"
        predicate = f"attempts[-1].{phase} IS NOT NULL" if phase and record_type == "dataflow" else "TRUE"
        # Native JSON admission does not retain a distinction between an
        # optional member that was omitted and one that was admitted as NULL.
        # Keep value availability measurable, but leave structural presence
        # unknown for the optional group instead of reporting every NULL as a
        # missing schema field.
        present_slots = " + ".join(presence_expression(record_type, field, phase) for field in fields)
        known_presence_slots = " + ".join(known_presence_expression(record_type, field, phase) for field in fields)
        available_slots = " + ".join(availability_expression(record_type, field, phase) for field in fields)
        aggregates.append(
            f"SELECT {index} AS group_index, COUNT(*) AS records, SUM({present_slots}) AS present, "
            f"SUM({known_presence_slots}) AS known, "
            f"SUM({available_slots}) AS available "
            f"FROM {relation} WHERE {predicate}"
        )
        groups.append((group, record_type, fields, applicability))
    measured = _rows(conn.execute(ctes + " " + " UNION ALL ".join(aggregates) + " ORDER BY group_index", params))
    result = []
    for (group, record_type, fields, applicability), counts in zip(groups, measured, strict=True):
        records = int(counts.get("records") or 0)
        present = int(counts["present"]) if counts.get("present") is not None else None
        known = int(counts.get("known") or 0)
        available = int(counts.get("available") or 0)
        total_slots = records * len(fields)
        completeness = _rate(present, known) if present is not None and known else None
        availability = _rate(available, total_slots) if total_slots else None
        unknown_slots = max(0, total_slots - known)
        required_slots = sum(
            1 for field in fields if _schema_required(record_type, field, applicability if applicability not in {"universal", "optional"} else None)
        )
        result.append({
            "group": group, "record_type": record_type, "fields": ", ".join(fields),
            "records": records, "required_fields": required_slots,
            "present_values": present,
            "missing_values": max(0, known - present) if present is not None else None,
            "unknown_presence_values": unknown_slots,
            "available_values": available,
            "missing_available_values": max(0, total_slots - available) if applicability != "optional" else None,
            "completeness_rate": completeness,
            "presence_rate": completeness,
            "availability_rate": availability,
            "severity": _completeness_severity(completeness, records) if completeness is not None else "info",
            "applicability": "universal" if applicability == "universal" else "optional" if applicability == "optional" else "conditional",
            "presence_known": unknown_slots == 0,
            "actionable": bool(records) and applicability != "optional",
        })
    return result


def _diagnostic_capture(conn: Any, ctes: str, params: list[Any]) -> dict[str, Any]:
    issue_ctes = f"""{ctes}, capture_records AS (
      SELECT _source_id, log_session_id, job_id, dataflow_id, dataflow_run_id, dataflow_name,
             _file_uri, _file_kind, _record_line, event_time, diagnostic_issues
      FROM filtered_dataflows
    ), issue_rows AS (
      SELECT r.*, i.issue.field AS field, i.issue.reason AS reason
      FROM capture_records r
      CROSS JOIN UNNEST(r.diagnostic_issues) AS i(issue)
    )"""
    summary = _single_row(conn.execute(f"""{issue_ctes}
      SELECT (SELECT COUNT(*) FROM capture_records WHERE len(diagnostic_issues) > 0) AS affected_records,
             COUNT(*) AS known_markers,
             (SELECT COUNT(*) FROM (
                SELECT DISTINCT _source_id, log_session_id, job_id, dataflow_id,
                                dataflow_run_id, _file_uri, _record_line
                FROM issue_rows WHERE reason = 'omitted'
              )) AS omitted_marker_records,
             (SELECT COUNT(*) FROM (
                SELECT DISTINCT _source_id, log_session_id, job_id, dataflow_id,
                                dataflow_run_id, _file_uri, _record_line
                FROM issue_rows WHERE reason = 'omitted'
              )) AS overflow_records
      FROM issue_rows""", params))
    reasons = _rows(conn.execute(f"{issue_ctes} SELECT reason, COUNT(*) AS count FROM issue_rows GROUP BY reason ORDER BY reason", params))
    evidence_count_row = _single_row(conn.execute(f"{issue_ctes} SELECT COUNT(*) AS evidence_records_count FROM issue_rows", params))
    evidence = _rows(conn.execute(f"""{issue_ctes}
      SELECT _source_id, log_session_id, job_id, dataflow_id, dataflow_run_id, dataflow_name,
             _file_uri, _file_kind, _record_line, event_time AS latest_time, field, reason
      FROM issue_rows ORDER BY event_time DESC NULLS LAST, _source_id, dataflow_run_id, field LIMIT 50""", params))
    summary.update({
        "evidence_records_count": int(evidence_count_row.get("evidence_records_count") or 0),
        "evidence_limit": 50,
        "truncated": int(evidence_count_row.get("evidence_records_count") or 0) > len(evidence),
    })
    for item in evidence:
        item["record_type"] = "dataflow_run_log"
    return {"summary": summary, "by_reason": reasons, "evidence": evidence}


def _source_coverage(conn: Any, ctes: str, params: list[Any]) -> list[dict[str, Any]]:
    result = conn.execute(
        f"""
        {ctes},
        source_records AS (
          SELECT _source_id, MAX(_file_kind) AS file_kind,
                  COUNT(*) AS dataflow_records, 0::BIGINT AS job_records,
                  MAX(event_time) AS latest_log_at, MAX(_ingested_at) AS latest_ingested_at,
                  0::BIGINT AS log_loss_job_count,
                  0::BIGINT AS log_records_dropped_reporting_jobs,
                  0::BIGINT AS log_bytes_dropped_reporting_jobs,
                  0::BIGINT AS log_loss_unknown_jobs,
                  NULL::HUGEINT AS log_records_dropped_observed_subtotal,
                  NULL::HUGEINT AS log_bytes_dropped_observed_subtotal,
                  CASE WHEN COUNT(*) = COUNT(log_records_dropped)
                       THEN SUM(log_records_dropped) ELSE NULL END AS log_records_dropped,
                  CASE WHEN COUNT(*) = COUNT(log_bytes_dropped)
                       THEN SUM(log_bytes_dropped) ELSE NULL END AS log_bytes_dropped
          FROM filtered_dataflows GROUP BY _source_id
          UNION ALL
          SELECT _source_id, MAX(_file_kind),
                  0::BIGINT, COUNT(*), MAX(event_time), MAX(_ingested_at),
                  COUNT(*)::BIGINT,
                  COUNT(log_records_dropped)::BIGINT,
                  COUNT(log_bytes_dropped)::BIGINT,
                  COUNT(*) FILTER (
                    WHERE log_records_dropped IS NULL OR log_bytes_dropped IS NULL
                  )::BIGINT,
                  SUM(log_records_dropped),
                  SUM(log_bytes_dropped),
                  CASE WHEN COUNT(*) = COUNT(log_records_dropped)
                       THEN SUM(log_records_dropped) ELSE NULL END,
                  CASE WHEN COUNT(*) = COUNT(log_bytes_dropped)
                       THEN SUM(log_bytes_dropped) ELSE NULL END
          FROM filtered_jobs GROUP BY _source_id
        ),
        source_files AS (
          SELECT _source_id, _file_uri FROM filtered_dataflows WHERE _file_uri IS NOT NULL
          UNION
          SELECT _source_id, _file_uri FROM filtered_jobs WHERE _file_uri IS NOT NULL
        ),
        source_file_counts AS (
          SELECT _source_id, COUNT(*) AS file_count FROM source_files GROUP BY _source_id
        )
        SELECT sr._source_id, ANY_VALUE(sr.file_kind) AS file_kind,
               COALESCE(ANY_VALUE(sf.file_count), 0) AS file_count,
               SUM(sr.dataflow_records) AS dataflow_records, SUM(sr.job_records) AS job_records,
         MAX(sr.latest_log_at) AS latest_log_at, MAX(sr.latest_ingested_at) AS latest_ingested_at,
         SUM(sr.log_loss_job_count) AS log_loss_job_count,
         SUM(sr.log_records_dropped_reporting_jobs) AS log_records_dropped_reporting_jobs,
         SUM(sr.log_bytes_dropped_reporting_jobs) AS log_bytes_dropped_reporting_jobs,
         SUM(sr.log_loss_unknown_jobs) AS log_loss_unknown_jobs,
         SUM(sr.log_records_dropped_observed_subtotal) AS log_records_dropped_observed_subtotal,
         SUM(sr.log_bytes_dropped_observed_subtotal) AS log_bytes_dropped_observed_subtotal,
         CASE WHEN COUNT(sr.log_records_dropped) = COUNT(*)
              THEN SUM(sr.log_records_dropped) ELSE NULL END AS log_records_dropped,
         CASE WHEN COUNT(sr.log_bytes_dropped) = COUNT(*)
              THEN SUM(sr.log_bytes_dropped) ELSE NULL END AS log_bytes_dropped
        FROM source_records sr
        LEFT JOIN source_file_counts sf ON sf._source_id = sr._source_id
        GROUP BY sr._source_id
        ORDER BY SUM(sr.dataflow_records) + SUM(sr.job_records) DESC, sr._source_id
        """,
        params,
    )
    coverage = []
    for row in _rows(result):
        dataflow_records = int(row["dataflow_records"] or 0)
        job_records = int(row["job_records"] or 0)
        coverage.append({
            "source": f"source:{int(row['_source_id'])}",
            "source_id": int(row["_source_id"]),
            "file_kind": row["file_kind"] or "unknown",
            "file_count": int(row["file_count"] or 0),
            "job_records": job_records,
            "dataflow_records": dataflow_records,
            "records": dataflow_records + job_records,
             "latest_log_at": row["latest_log_at"],
             "latest_ingested_at": row["latest_ingested_at"],
             "log_records_dropped": row["log_records_dropped"],
             "log_bytes_dropped": row["log_bytes_dropped"],
            "log_loss_job_count": int(row.get("log_loss_job_count") or 0),
            "log_records_dropped_reporting_jobs": int(row.get("log_records_dropped_reporting_jobs") or 0),
            "log_bytes_dropped_reporting_jobs": int(row.get("log_bytes_dropped_reporting_jobs") or 0),
            "log_loss_unknown_jobs": int(row.get("log_loss_unknown_jobs") or 0),
            "log_records_dropped_observed_subtotal": row.get("log_records_dropped_observed_subtotal"),
            "log_bytes_dropped_observed_subtotal": row.get("log_bytes_dropped_observed_subtotal"),
            "warning_count": 1 if (
                int(row.get("log_records_dropped_observed_subtotal") or 0) > 0
                or int(row.get("log_bytes_dropped_observed_subtotal") or 0) > 0
                or int(row.get("log_loss_unknown_jobs") or 0) > 0
            ) else 0,
            "status": "warning" if (
                int(row.get("log_records_dropped_observed_subtotal") or 0) > 0
                or int(row.get("log_bytes_dropped_observed_subtotal") or 0) > 0
                or int(row.get("log_loss_unknown_jobs") or 0) > 0
            ) else "ok",
        })
    return coverage


def _record_evidence(conn: Any, ctes: str, params: list[Any], grain: str, timezone_name: str) -> list[dict[str, Any]]:
    result = conn.execute(
        f"""
        {ctes},
        dataflow_counts AS (
          SELECT date_trunc(?, timezone(?, event_time)) AS bucket, COUNT(*) AS dataflow_records
          FROM filtered_dataflows GROUP BY bucket
        ),
        job_counts AS (
          SELECT date_trunc(?, timezone(?, event_time)) AS bucket, COUNT(*) AS job_records
          FROM filtered_jobs GROUP BY bucket
        ),
        dataflow_ids AS (
          SELECT DISTINCT date_trunc(?, timezone(?, event_time)) AS bucket,
                          _source_id, log_session_id, job_id
          FROM filtered_dataflows WHERE job_id IS NOT NULL
        ),
        job_ids AS (
          SELECT DISTINCT date_trunc(?, timezone(?, event_time)) AS bucket,
                          _source_id, log_session_id, job_id
          FROM filtered_jobs WHERE job_id IS NOT NULL
        ),
        linkage_counts AS (
          SELECT COALESCE(df.bucket, j.bucket) AS bucket,
                 SUM(CASE WHEN df.job_id IS NOT NULL AND j.job_id IS NOT NULL THEN 1 ELSE 0 END) AS matched_job_ids,
                 SUM(CASE WHEN df.job_id IS NOT NULL AND j.job_id IS NULL THEN 1 ELSE 0 END) AS orphan_dataflow_job_ids,
                 SUM(CASE WHEN df.job_id IS NULL AND j.job_id IS NOT NULL THEN 1 ELSE 0 END) AS jobs_without_dataflow_records
          FROM dataflow_ids df FULL OUTER JOIN job_ids j
            ON df.bucket IS NOT DISTINCT FROM j.bucket
           AND df._source_id = j._source_id
           AND df.log_session_id IS NOT DISTINCT FROM j.log_session_id
           AND df.job_id = j.job_id
          GROUP BY COALESCE(df.bucket, j.bucket)
        )
        SELECT COALESCE(df.bucket, j.bucket, l.bucket) AS bucket,
               COALESCE(df.dataflow_records, 0) AS dataflow_records,
               COALESCE(j.job_records, 0) AS job_records,
               COALESCE(l.matched_job_ids, 0) AS matched_job_ids,
               COALESCE(l.orphan_dataflow_job_ids, 0) AS orphan_dataflow_job_ids,
               COALESCE(l.jobs_without_dataflow_records, 0) AS jobs_without_dataflow_records
        FROM dataflow_counts df
        FULL OUTER JOIN job_counts j ON df.bucket IS NOT DISTINCT FROM j.bucket
        FULL OUTER JOIN linkage_counts l ON COALESCE(df.bucket, j.bucket) IS NOT DISTINCT FROM l.bucket
        ORDER BY bucket
        """,
        [*params, grain, timezone_name, grain, timezone_name, grain, timezone_name, grain, timezone_name],
    )
    rows = []
    for row in _rows(result):
        bucket = row["bucket"]
        bucket_label = _bucket_label(bucket, grain)
        matched = int(row["matched_job_ids"] or 0)
        orphan = int(row["orphan_dataflow_job_ids"] or 0)
        job_only = int(row["jobs_without_dataflow_records"] or 0)
        rows.append({
            "bucket": bucket_label,
            "date": bucket_label,
            "bucket_start": bucket,
            "dataflow_records": int(row["dataflow_records"] or 0),
            "job_records": int(row["job_records"] or 0),
            "matched_job_ids": matched,
            "orphan_dataflow_job_ids": orphan,
            "jobs_without_dataflow_records": job_only,
            "linkage_rate": _rate(matched, matched + orphan + job_only),
        })
    return rows


def _linkage_summary(counts: dict[str, int], union_count: int) -> list[dict[str, Any]]:
    values = (
        ("matched", "Matched", "good"),
        ("orphan_dataflow_job_id", "Orphan dataflow job IDs", "bad"),
        ("job_without_dataflow_records", "Missing expected child records", "bad"),
        ("job_zero_children_expected", "No child records expected", "info"),
        ("job_children_unassessed", "Child records unassessed", "info"),
    )
    return [
        {
            "category": category,
            "label": label,
            "count": int(counts.get(category) or 0),
            "share": _rate(int(counts.get(category) or 0), union_count),
            "severity": "good" if not counts.get(category) else severity,
        }
        for category, label, severity in values
    ]


def _investigation_queue(
    linkage_evidence: list[dict[str, Any]],
    reconciliation: dict[str, Any],
    field_completeness: list[dict[str, Any]],
    capture_evidence: list[dict[str, Any]],
    *,
    limit: int | None = 200,
) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    for evidence in linkage_evidence:
        category = str(evidence["category"])
        if category == "matched":
            continue
        if category == "orphan_dataflow_job_id":
            queue.append(_queue_row(
                "bad", "orphan dataflow job id",
                "Dataflow records reference a job_id with no matching job log.",
                str(evidence["job_id"]), evidence["latest_time"],
                {"_source_id": evidence["_source_id"], "log_session_id": evidence["log_session_id"],
                 "job_id": evidence["job_id"], "dataflow_records": evidence["dataflow_records"]},
                "Check job_run_log coverage for the same run window.",
            ))
        elif category == "job_children_unassessed":
            reason = str(evidence.get("unassessed_reason") or "unassessed")
            scope = str(evidence.get("unassessed_scope") or "unknown")
            queue.append(_queue_row(
                "info", "job children unassessed",
                f"The job child population is unassessed ({reason}).",
                str(evidence["job_id"]), evidence["latest_time"],
                {"_source_id": evidence["_source_id"], "log_session_id": evidence["log_session_id"],
                 "job_id": evidence["job_id"], "job_total_dataflows": evidence["job_total_dataflows"],
                 "job_status": evidence.get("job_status"),
                 "job_observed_at": evidence.get("job_observed_at"),
                 "job_log_records_dropped": evidence.get("job_log_records_dropped"),
                 "filtered_dataflow_records": evidence.get("filtered_dataflow_records"),
                 "recorded_dataflow_records": evidence.get("recorded_dataflow_records"),
                 "excluded_dataflow_records": evidence.get("excluded_dataflow_records"),
                 "unassessed_reason": reason,
                 "scope_coverage": scope},
                "Inspect the job snapshot and recorded child population for this scope.",
            ))
        elif category == "job_zero_children_expected":
            continue
        else:
            queue.append(_queue_row(
                "bad", "missing expected dataflow records",
                "A terminal job reports child dataflows, but no matching child records were found.",
                str(evidence["job_id"]), evidence["latest_time"],
                {"_source_id": evidence["_source_id"], "log_session_id": evidence["log_session_id"],
                 "job_id": evidence["job_id"], "job_total_dataflows": evidence["job_total_dataflows"]},
                "Check dataflow_run_log coverage and cache sync for this job.",
            ))
    for check in reconciliation["checks"]:
        if check.get("severity") != "warning":
            coverage_reason = str(check.get("scope_coverage") or "unknown")
            queue.append(_queue_row("info", "insufficient reconciliation coverage",
                f"Cannot compare {check['metric']}: {coverage_reason}.",
                str(check["job_id"]), None, check, "Inspect snapshot timing and dropped log records."))
            continue
        queue.append(_queue_row(
            "bad" if check.get("scope_coverage") == "full" and check.get("expected") != check.get("observed") else "warning",
            "reconciliation mismatch",
            f"{check['metric']} expected {check['expected']} but observed {check['observed']}.",
            str(check["job_id"]), None, check,
            "Inspect the job drawer and child dataflow records.",
        ))
    for row in field_completeness:
        if not row["actionable"] or row["severity"] not in {"bad", "warning"}:
            continue
        queue.append(_queue_row(
            str(row["severity"]), "field completeness",
            f"{row['record_type']} {row['group']} completeness is {row['completeness_rate']}%.",
            f"{row['record_type']} · {row['group']}", None, row,
            "Confirm the log version emits the fields used by Monitoring pages.",
        ))
    for item in capture_evidence:
        queue.append(_queue_row("warning", "diagnostic capture",
            "Diagnostic detail is incomplete; execution status is unchanged.",
            str(item["dataflow_run_id"] if item["dataflow_run_id"] is not None else item["job_id"]), item["latest_time"], item,
            "Inspect canonical diagnostic issue paths and reasons for this run."))
    ordered = sorted(
        queue,
        key=lambda row: (-_severity_rank(str(row["severity"])), -_timestamp_value(row["latest_time"]), str(row["category"])),
    )
    return ordered if limit is None else ordered[:limit]


def _investigation_queue_result(
    linkage_evidence: list[dict[str, Any]],
    reconciliation: dict[str, Any],
    field_completeness: list[dict[str, Any]],
    capture_evidence: list[dict[str, Any]],
    *,
    linkage_counts: dict[str, int],
    capture_summary: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return the bounded registry and its full pre-limit counts."""

    rows = _investigation_queue(
        linkage_evidence,
        reconciliation,
        field_completeness,
        capture_evidence,
    )
    field_issue_count = sum(
        1
        for row in field_completeness
        if row.get("actionable") and row.get("severity") in {"bad", "warning"}
    )
    total_issues = (
        int(linkage_counts.get("orphan_dataflow_job_id") or 0)
        + int(linkage_counts.get("job_without_dataflow_records") or 0)
        + int(linkage_counts.get("job_children_unassessed") or 0)
        + int(reconciliation.get("summary", {}).get("mismatch_checks_count") or 0)
        + int(reconciliation.get("summary", {}).get("unassessed_checks_count") or 0)
        + field_issue_count
        + int(capture_summary.get("known_markers") or 0)
    )
    limit = 200
    return rows, {
        "total_issues": total_issues,
        "loaded_issues": len(rows),
        "limit": limit,
        "truncated": total_issues > len(rows),
    }


def _queue_row(
    severity: str,
    category: str,
    issue: str,
    target: str,
    latest_time: object,
    evidence: dict[str, Any],
    action_hint: str,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "category": category,
        "issue": issue,
        "target": target,
        "evidence": evidence,
        "latest_time": latest_time,
        "action_hint": action_hint,
        "_source_id": evidence.get("_source_id"),
        "log_session_id": evidence.get("log_session_id"),
        "job_id": evidence.get("job_id"),
        "dataflow_id": evidence.get("dataflow_id"),
        "dataflow_run_id": evidence.get("dataflow_run_id"),
    }


def _assessment(
    *,
    health_status: str,
    summary: dict[str, Any],
    linkage_counts: dict[str, int],
    reconciliation: dict[str, Any],
) -> dict[str, Any]:
    """Compose a domain assessment without conflating field coverage."""

    reasons: list[dict[str, Any]] = []
    orphan_count = int(linkage_counts.get("orphan_dataflow_job_id") or 0)
    missing_count = int(linkage_counts.get("job_without_dataflow_records") or 0)
    unassessed_count = int(linkage_counts.get("job_children_unassessed") or 0)
    mismatch_count = int(reconciliation.get("mismatch_count") or 0)
    dropped = summary.get("log_records_dropped_observed_subtotal")
    dropped_bytes = summary.get("log_bytes_dropped_observed_subtotal")
    reconciliation_summary = reconciliation.get("summary", {})
    if orphan_count:
        reasons.append({"code": "orphan_dataflow_job_id", "severity": "issue", "count": orphan_count})
    if missing_count:
        reasons.append({"code": "missing_expected_dataflow_records", "severity": "issue", "count": missing_count})
    if mismatch_count:
        reasons.append({"code": "reconciliation_mismatch", "severity": "issue", "count": mismatch_count})
    if dropped is not None and int(dropped or 0) > 0:
        reasons.append({"code": "log_loss", "severity": "warning", "count": int(dropped)})
    if dropped_bytes is not None and int(dropped_bytes or 0) > 0 and not (dropped is not None and int(dropped or 0) > 0):
        reasons.append({"code": "log_bytes_loss", "severity": "warning", "count": int(dropped_bytes)})
    unknown_loss_jobs = int(summary.get("log_loss_unknown_jobs") or 0)
    if unknown_loss_jobs:
        reasons.append({
            "code": "log_loss_unassessed",
            "severity": "info",
            "count": unknown_loss_jobs,
        })
    if unassessed_count:
        reasons.append({"code": "linkage_unassessed", "severity": "info", "count": unassessed_count})
    if int(reconciliation.get("summary", {}).get("unassessed_checks_count") or 0) > 0:
        reasons.append({
            "code": "reconciliation_unassessed",
            "severity": "info",
            "count": int(reconciliation["summary"]["unassessed_checks_count"]),
            "scope_coverage": reconciliation.get("scope_coverage"),
        })
    if health_status == "no_evidence" or (not summary.get("job_records") and not summary.get("dataflow_records")):
        status = "no_evidence"
    elif any(item["severity"] == "issue" for item in reasons):
        status = "issue"
    elif any(item["severity"] == "warning" for item in reasons):
        status = "warning"
    elif (
        int(reconciliation_summary.get("comparable_checks_count") or 0) > 0
        and int(reconciliation_summary.get("unassessed_checks_count") or 0) > 0
    ):
        status = "partial"
    elif (
        int(reconciliation_summary.get("total_checks_count") or 0) == 0
        or str(reconciliation_summary.get("scope_coverage") or "").lower() == "none"
    ):
        status = "not_assessed"
    elif reasons:
        status = "not_assessed"
    else:
        status = "clear"
    return {
        "status": status,
        "reasons": reasons,
        "coverage": reconciliation.get("scope_coverage") or "none",
    }


def _effective_grain(filters: dict[str, str], summary: dict[str, Any], fallback: str) -> str:
    if str(filters.get("range") or "").strip().lower() != "all":
        return fallback
    start = _timestamp(summary.get("earliest_log_at"))
    end = _timestamp(summary.get("latest_log_at"))
    if start is None or end is None:
        return fallback
    requested = str(filters.get("grain") or "auto").strip().lower()
    if requested not in {"auto", "hour", "day", "week", "month"}:
        requested = "auto"
    span_seconds = max(0, (end - start).total_seconds())
    minimum = (
        "hour" if span_seconds <= 3 * 86400
        else "day" if span_seconds <= 90 * 86400
        else "week" if span_seconds <= 365 * 86400
        else "month"
    )
    if requested == "auto":
        return minimum
    grains = ("hour", "day", "week", "month")
    return grains[max(grains.index(requested), grains.index(minimum))]


def _timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _coverage_status(dataflow_records: int, job_records: int) -> str:
    if not dataflow_records and not job_records:
        return "no_records"
    if not dataflow_records or not job_records:
        return "partial"
    return "ok"


def _health_status(
    *,
    dataflow_records: int,
    job_records: int,
    orphan_job_ids: int,
    jobs_without_dataflow_records: int,
    reconciliation_mismatches: int,
) -> str:
    if not dataflow_records and not job_records:
        return "no_evidence"
    if orphan_job_ids or jobs_without_dataflow_records or reconciliation_mismatches:
        return "has_issues"
    if bool(dataflow_records) != bool(job_records):
        return "warning"
    return "healthy"


def _completeness_severity(rate: float, records: int) -> str:
    if records == 0:
        return "info"
    if rate < 80:
        return "bad"
    if rate < 95:
        return "warning"
    return "good"


def _bucket_label(value: object, grain: str) -> str:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return "unknown"
    if not isinstance(value, datetime):
        return "unknown"
    if grain == "hour":
        return value.strftime("%Y-%m-%d %H:00")
    if grain == "week":
        year, week, _ = value.isocalendar()
        return f"{year}-W{week:02d}"
    if grain == "month":
        return value.strftime("%Y-%m")
    return value.strftime("%Y-%m-%d")


def _rate(part: int | float, whole: int | float) -> float:
    return round((part / whole) * 100, 2) if whole else 0


def _severity_rank(value: str) -> int:
    return {"bad": 4, "error": 4, "warning": 3, "info": 2, "good": 1}.get(value.lower(), 0)


def _timestamp_value(value: object) -> float:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return 0.0
    if not isinstance(value, datetime):
        return 0.0
    try:
        return value.timestamp()
    except (OSError, OverflowError, ValueError):
        return 0.0


def _single_row(result: Any) -> dict[str, Any]:
    row = result.fetchone()
    if row is None:
        return {}
    return {
        column[0]: _json_value(value)
        for column, value in zip(result.description, row)
    }


def _rows(result: Any) -> list[dict[str, Any]]:
    columns = [column[0] for column in result.description]
    return [
        {column: _json_value(value) for column, value in zip(columns, row)}
        for row in result.fetchall()
    ]


def _json_value(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (datetime, date)) else value
