from __future__ import annotations

import json

from datacoolie_studio.domains.analytics import schema, staging, store
from datacoolie_studio.domains.analytics.contracts import JobFile
from datacoolie_studio.domains.analytics.serving_facts import rebuild_monitoring_serving_facts
from datacoolie_studio.domains.monitoring.diagnostics_findings import (
    FindingRequestError,
    parse_finding_group,
)
from datacoolie_studio.domains.monitoring.diagnostics_repository import (
    diagnostics_findings_evidence,
    diagnostics_read_model,
)
from tests.domains.monitoring.test_round2_diagnostics_identity import _add_job
from tests.domains.monitoring.test_v6_freshness_contract import connection
from tests.support.v6_records import native_record

import pytest


def _add_jobs(conn, tmp_path, records):
    items = []
    for index, record in enumerate(records):
        path = tmp_path / f"job-{index}.json"
        path.write_text(json.dumps(record), encoding="utf-8")
        items.append(JobFile(str(path), "job_json", "{}", str(path)))
    relations, types, _, _ = staging.stage_job_relations(conn, items)
    for index in relations:
        store.insert_job_file(conn, 1, str(tmp_path / f"job-{index}.json"), "job_json", "{}", source_relation=relations[index], source_types=types[index])
    rebuild_monitoring_serving_facts(conn, dataflow_table=schema.DATAFLOW_TABLE,
        job_table=schema.JOB_TABLE, dataflow_column_types=schema.DATAFLOW_COLUMN_TYPES,
        job_column_types=schema.JOB_COLUMN_TYPES)


def _job(flow: dict, **fields) -> dict:
    return native_record({
        "_type": "job_run_log", "job_id": flow["job_id"],
        "log_session_id": flow["log_session_id"], "log_records_dropped": 0,
        "log_bytes_dropped": 0, "total_dataflows": 1, "total_succeeded": 1,
        "total_failed": 0, "total_skipped": 0, "observed_at": "2026-11-02T00:00:00Z",
        **fields,
    })


def _evidence(conn, view="registry", group=None, filters=None, **page):
    return diagnostics_findings_evidence(
        [], filters or {}, view=view,
        group=parse_finding_group(group) if group else None,
        limit=page.pop("limit", 50), offset=page.pop("offset", 0),
        sort_by=page.pop("sort_by", None), sort_dir=page.pop("sort_dir", None),
        analytics_context=(conn, [1], "test"),
    )


def _model(conn, filters=None):
    return diagnostics_read_model(
        [], filters or {}, grain="day", timezone_name="UTC", analytics_context=(conn, [1], "test"),
    )["diagnostics"]


def test_selector_is_structural_and_validated():
    group = parse_finding_group("diagnostic_capture|truncated|/a|b")
    assert (group.rule, group.reason, group.section) == ("diagnostic_capture", "truncated", "/a|b")
    assert group.kind == "warning"
    for bad in ("", "x|y|z", "log_loss|bogus|", "log_loss|records|x", "field_completeness|low_presence|; DROP",
                "diagnostic_capture|Bad Reason|/a"):
        with pytest.raises(FindingRequestError):
            parse_finding_group(bad)


def test_clean_population_has_no_findings(tmp_path):
    flow = native_record({})
    with connection(tmp_path, [flow]) as conn:
        _add_job(conn, tmp_path / "job.json", _job(flow))
        diagnostics = _model(conn)
        assert diagnostics["finding_groups"] == [] or all(
            item["kind"] == "limitation" or item["rule"] == "field_completeness"
            for item in diagnostics["finding_groups"]
        )
        assert diagnostics["scope_notices"] == []
        assert diagnostics["findings_summary"]["scope"] == "full"


def test_loss_is_a_warning_group_and_enumerates_the_exact_job(tmp_path):
    flow = native_record({})
    with connection(tmp_path, [flow]) as conn:
        _add_job(conn, tmp_path / "job.json", _job(flow, log_records_dropped=2, log_bytes_dropped=128, total_dataflows=2))
        diagnostics = _model(conn)
        groups = {item["group_key"]: item for item in diagnostics["finding_groups"]}
        loss = groups["log_loss|records_and_bytes|"]
        assert loss["kind"] == "warning" and loss["affected_objects"] == 1
        assert "reconciliation_mismatch|count_mismatch|" not in groups
        assert groups["reconciliation_unassessed|lost_detail|"]["checks"] == 4
        assert groups["reconciliation_unassessed|lost_detail|"]["affected_objects"] == 1
        detail = _evidence(conn, "detail", "log_loss|records_and_bytes|")
        assert detail["total"] == 1
        row = detail["objects"][0]
        assert (row["source_id"], row["log_session_id"], row["job_id"]) == (1, flow["log_session_id"], flow["job_id"])
        assert (row["records_dropped"], row["bytes_dropped"]) == (2, 128)
        checks = _evidence(conn, "detail", "reconciliation_unassessed|lost_detail|")["objects"][0]
        assert {item["metric"] for item in checks["metrics"]} == {
            "total_dataflows", "total_failed", "total_skipped", "total_succeeded",
        }


def test_confirmed_mismatch_groups_metrics_under_one_job(tmp_path):
    flow = native_record({})
    with connection(tmp_path, [flow]) as conn:
        _add_job(conn, tmp_path / "job.json", _job(flow, total_dataflows=3, total_succeeded=3))
        diagnostics = _model(conn)
        mismatch = next(item for item in diagnostics["finding_groups"] if item["rule"] == "reconciliation_mismatch")
        assert mismatch["kind"] == "issue"
        assert mismatch["affected_objects"] == 1
        assert mismatch["checks"] == 2
        objects = _evidence(conn, "detail", mismatch["group_key"])["objects"]
        assert {(item["metric"], item["expected"], item["observed"], item["difference"]) for item in objects[0]["metrics"]} == {
            ("total_dataflows", 3, 1, -2), ("total_succeeded", 3, 1, -2),
        }
        summary = diagnostics["findings_summary"]
        assert summary["issue_groups"] >= 1
        assert {"kind": "issue", "subject_kind": "job", "count": 1} in summary["affected_objects"]


def test_child_subset_filter_yields_scope_notice_without_confirmed_issues(tmp_path):
    flow = native_record({})
    with connection(tmp_path, [flow]) as conn:
        _add_job(conn, tmp_path / "job.json", _job(flow, total_dataflows=5, total_succeeded=5))
        diagnostics = _model(conn, {"status": "succeeded"})
        assert diagnostics["findings_summary"]["scope"] == "subset"
        assert diagnostics["findings_summary"]["restrictions"] == ["status"]
        notice = diagnostics["scope_notices"][0]
        assert (notice["code"], notice["jobs"], notice["checks"]) == ("filtered_child_scope", 1, 4)
        assert not [item for item in diagnostics["finding_groups"] if item["kind"] == "issue"]
        assert not [item for item in diagnostics["finding_groups"] if item["reason"] == "filtered_child_scope"]
        objects = _evidence(conn, "detail", notice["group_key"], filters={"status": "succeeded"})
        assert objects["total"] == 1 and objects["objects"][0]["job_id"] == flow["job_id"]


def test_capture_groups_are_complete_and_paged_beyond_the_page_limit(tmp_path):
    rows = [
        native_record({
            "dataflow_run_id": f"run-{index}",
            "diagnostic_issues": [{"field": f"/attempts/0/field{index}", "reason": "truncated"}],
        })
        for index in range(7)
    ]
    with connection(tmp_path, rows) as conn:
        first = _evidence(conn, limit=3, offset=0, sort_by="section", sort_dir="asc")
        assert first["total"] >= 7
        seen = []
        offset = 0
        while True:
            page = _evidence(conn, limit=3, offset=offset, sort_by="section", sort_dir="asc")
            if not page["groups"]:
                break
            seen.extend(item["group_key"] for item in page["groups"])
            offset += 3
        assert len(seen) == len(set(seen)) == first["total"]
        capture = [key for key in seen if key.startswith("diagnostic_capture|truncated|/attempts/0/field")]
        assert len(capture) == 7
        detail = _evidence(conn, "detail", capture[0])
        assert detail["total"] == 1
        assert detail["objects"][0]["dataflow_run_id"].startswith("run-")
        assert detail["objects"][0]["occurrences"] == 1


def test_byte_only_loss_is_an_actionable_warning_with_its_own_reason(tmp_path):
    flow = native_record({})
    with connection(tmp_path, [flow]) as conn:
        _add_job(conn, tmp_path / "job.json", _job(flow, log_records_dropped=0, log_bytes_dropped=64))
        groups = {item["group_key"]: item for item in _model(conn)["finding_groups"]}
        assert groups["log_loss|bytes|"]["kind"] == "warning"
        assert "log_loss|records|" not in groups


def test_null_and_empty_sessions_and_large_integers_keep_exact_identity(tmp_path):
    first = native_record({"log_session_id": None, "job_id": "same"})
    second = native_record({"log_session_id": "", "job_id": "same", "dataflow_run_id": "other-run"})
    big = 9007199254740993
    with connection(tmp_path, [first, second]) as conn:
        _add_jobs(conn, tmp_path, [
            _job(first, log_session_id=None, total_dataflows=big, total_succeeded=big),
            _job(second, log_session_id="", total_dataflows=big, total_succeeded=big),
        ])
        detail = _evidence(conn, "detail", "reconciliation_mismatch|count_mismatch|", sort_by="log_session_id", sort_dir="asc")
        assert detail["total"] == 2
        assert [item["log_session_id"] for item in detail["objects"]] == ["", None] or [
            item["log_session_id"] for item in detail["objects"]
        ] == [None, ""]
        for item in detail["objects"]:
            expected = next(metric for metric in item["metrics"] if metric["metric"] == "total_dataflows")
            assert expected["expected"] == big and expected["difference"] == 1 - big


def test_field_group_findings_page_and_drill_in_without_sql_interpolation(tmp_path):
    row = native_record({})
    row["attempts"][-1]["source"] = None
    with connection(tmp_path, [row]) as conn:
        groups = _evidence(conn)["groups"]
        assert all(item["group_key"].count("|") >= 2 for item in groups)
        field = [item for item in groups if item["rule"] == "field_completeness"]
        for item in field:
            detail = _evidence(conn, "detail", item["group_key"])
            assert detail["total"] == 1
            assert detail["objects"][0]["field_group"]["group"]
            assert _evidence(conn, "detail", item["group_key"], offset=1)["objects"] == []


def test_every_group_serves_objects_that_satisfy_the_public_contract(tmp_path):
    from datacoolie_studio.api.v1.contracts.monitoring_queries import MonitoringDiagnosticsEvidenceResponse

    flow = native_record({"diagnostic_issues": [{"field": "/message", "reason": "truncated"}]})
    orphan = native_record({"job_id": "orphan-job", "dataflow_run_id": "orphan-run"})
    with connection(tmp_path, [flow, orphan]) as conn:
        _add_job(conn, tmp_path / "job.json", _job(flow, log_records_dropped=2, log_bytes_dropped=64, total_dataflows=3))
        groups = _evidence(conn)["groups"]
        assert {item["rule"] for item in groups} >= {"log_loss", "diagnostic_capture", "orphan_job_log"}
        for group in groups:
            detail = _evidence(conn, "detail", group["group_key"])
            response = {"mode": "detail", "errors": [], "group": detail["group"], "objects": detail["objects"],
                        "summary": {"records": len(detail["objects"]), "total_records": detail["total"], "limit": 50, "offset": 0}}
            MonitoringDiagnosticsEvidenceResponse.model_validate(response)


def test_unknown_group_is_empty_and_invalid_requests_raise(tmp_path):
    flow = native_record({})
    with connection(tmp_path, [flow]) as conn:
        empty = _evidence(conn, "detail", "diagnostic_capture|truncated|/nothing")
        assert empty["group"] is None and empty["objects"] == [] and empty["total"] == 0
        with pytest.raises(FindingRequestError):
            _evidence(conn, "registry", sort_by="job_id")
        with pytest.raises(FindingRequestError):
            _evidence(conn, "detail", "log_loss|records|", sort_by="priority")
