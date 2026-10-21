import { describe, expect, it } from "vitest";
import { mergeDataflowRunDetail, monitoringDetailEvidenceRequest, monitoringFailureDetailKind, monitoringRegistryRunReference, sameMonitoringJobScope, validateMonitoringRegistryRunEvidence } from "./monitoringDetailEvidence";

const page = {
  limit: 100,
  offset: 200,
  sort: { sortBy: "duration_seconds", sortDir: "asc" as const },
};

describe("monitoring detail evidence request", () => {
  it("keeps an explicitly empty dataflow identity selectable as a dataflow failure", () => {
    expect(monitoringFailureDetailKind({ failure_kind: "failure", dataflow_id: "" })).toBe("dataflow");
    expect(monitoringFailureDetailKind({ failure_kind: "failure", dataflow_id: null })).toBe("failure");
    expect(monitoringFailureDetailKind({ failure_kind: "failure" })).toBe("failure");
  });

  it("resolves linked jobs and reconciliation within the reported source and session", () => {
    const row = { job_id: " JOB`UPPER ", log_session_id: "A", _source_id: 1 };
    expect(sameMonitoringJobScope(row, { ...row })).toBe(true);
    expect(sameMonitoringJobScope(row, { ...row, log_session_id: "B" })).toBe(false);
    expect(sameMonitoringJobScope(row, { ...row, _source_id: 2 })).toBe(false);
    expect(sameMonitoringJobScope(row, { ...row, job_id: "JOB`UPPER" })).toBe(false);
    expect(sameMonitoringJobScope({ job_id: row.job_id }, row)).toBe(true);
  });
  it("builds a bounded job child page", () => {
    const request = monitoringDetailEvidenceRequest(
      { kind: "job", row: { job_id: "job-1", log_session_id: "A", _source_id: 2 } },
      { range: "30d" },
      page,
    );
    expect(request).toEqual({
      key: "job:job-1:exact:A:2",
      params: {
        range: "30d",
        limit: 100,
        offset: 200,
        sortBy: "duration_seconds",
        sortDir: "asc",
        investigateKind: "job_id",
        investigateValue: "job-1",
        log_session_scope: "exact",
        log_session_id: "A",
        investigateSourceId: 2,
      },
    });
  });

  it("keeps NULL and exact empty sessions distinct for linked evidence", () => {
    expect(sameMonitoringJobScope({ job_id: "job-1", log_session_id: null }, { job_id: "job-1", log_session_id: null })).toBe(true);
    expect(sameMonitoringJobScope({ job_id: "job-1", log_session_id: null }, { job_id: "job-1", log_session_id: "" })).toBe(false);
    expect(sameMonitoringJobScope({ job_id: "job-1", log_session_id: "" }, { job_id: "job-1", log_session_id: "" })).toBe(true);
    expect(sameMonitoringJobScope({ job_id: "job-1", log_session_id: "" }, { job_id: "job-1" })).toBe(false);
    const request = monitoringDetailEvidenceRequest(
      { kind: "job", row: { job_id: "job-1", log_session_id: "", _source_id: 2 } },
      {},
      page,
    );
    expect(request?.key).toBe("job:job-1:exact::2");
    expect(request?.params).toMatchObject({ log_session_scope: "exact", log_session_id: "" });
  });

  it("preserves the selected operation scope for a maintenance destination page", () => {
    const request = monitoringDetailEvidenceRequest(
      { kind: "maintenance", row: { target: "  [\"source\",\"orders\"]  " } },
      { operationType: "maintenance", range: "7d" },
      page,
    );
    expect(request?.params).toMatchObject({
      operationType: "maintenance",
      destinationTarget: "  [\"source\",\"orders\"]  ",
      log_session_scope: "all",
      limit: 100,
      offset: 200,
    });
    expect(request?.params.investigateKind).toBeUndefined();
    expect(request?.params.investigateValue).toBeUndefined();
  });

  it("keeps aggregate registry session filters while using exact flow identity", () => {
    const request = monitoringDetailEvidenceRequest(
      { kind: "freshness", row: { dataflow_id: "", _source_id: 3 } },
      { range: "30d", log_session_scope: "exact", log_session_id: "" },
      page,
    );
    expect(request?.params).toMatchObject({
      investigateKind: "dataflow_id",
      investigateValue: "",
      log_session_scope: "exact",
      log_session_id: "",
      investigateSourceId: 3,
    });
    expect(request?.key).toContain(":exact::3");
  });

  it("narrows an unrestricted Freshness evidence request to ETL runs", () => {
    const base = { range: "30d", operationType: "all" };
    const request = monitoringDetailEvidenceRequest(
      { kind: "freshness", row: { dataflow_id: "df-42", _source_id: 3 } },
      base,
      page,
    );
    expect(request?.params).toMatchObject({ operationType: "etl", range: "30d" });

    const maintenance = monitoringDetailEvidenceRequest(
      { kind: "freshness", row: { dataflow_id: "df-42", _source_id: 3 } },
      { ...base, operationType: "maintenance" },
      page,
    );
    expect(maintenance?.params.operationType).toBe("maintenance");
  });

  it("does not request evidence for non-paged drawers or missing identity", () => {
    expect(monitoringDetailEvidenceRequest({ kind: "diagnostics", row: {} }, {}, page)).toBeNull();
    expect(monitoringDetailEvidenceRequest({ kind: "freshness", row: {} }, {}, page)).toBeNull();
  });

  it("keeps failure annotations while canonical run fields fill the drawer", () => {
    expect(mergeDataflowRunDetail(
      { dataflow_run_id: "run-1", failure_tags: ["timeout"], source_table: null },
      { dataflow_run_id: "run-1", source_table: "raw.orders", destination_table: "silver.orders" },
    )).toEqual({
      dataflow_run_id: "run-1",
      failure_tags: ["timeout"],
      source_table: "raw.orders",
      destination_table: "silver.orders",
    });
  });

  it("carries the exact registry physical reference and rejects stale or mismatched evidence", () => {
    const reference = monitoringRegistryRunReference({
      dataflow_id: "df-42",
      dataflow_run_id: "run-9",
      _source_id: 7,
      log_session_id: null,
      _file_uri: "logs/7.jsonl",
      _record_line: 19,
    }, "generation-2");
    expect(reference).toMatchObject({
      dataflow_id: "df-42",
      dataflow_run_id: "run-9",
      _source_id: 7,
      log_session_id: null,
      _file_uri: "logs/7.jsonl",
      _record_line: 19,
      analyticsGeneration: "generation-2",
      logSessionScope: "null",
    });
    expect(validateMonitoringRegistryRunEvidence(reference!, {
      row: {
        dataflow_id: "df-42",
        dataflow_run_id: "run-9",
        _source_id: 7,
        log_session_id: null,
        _file_uri: "logs/7.jsonl",
        _record_line: 19,
      },
      summary: { analytics_generation: "generation-2" },
    }).ok).toBe(true);
    expect(validateMonitoringRegistryRunEvidence(reference!, {
      row: {
        dataflow_id: "df-42",
        dataflow_run_id: "run-9",
        _source_id: 7,
        log_session_id: null,
        _file_uri: "logs/other.jsonl",
        _record_line: 19,
      },
      summary: { analytics_generation: "generation-2" },
    })).toMatchObject({ ok: false, reason: "file_mismatch" });
    expect(validateMonitoringRegistryRunEvidence(reference!, {
      row: {
        dataflow_id: "df-42",
        dataflow_run_id: "run-9",
        _source_id: 7,
        log_session_id: null,
        _file_uri: "logs/7.jsonl",
        _record_line: 19,
      },
      summary: {},
    })).toMatchObject({ ok: false, reason: "analytics_generation_mismatch" });
  });
});
