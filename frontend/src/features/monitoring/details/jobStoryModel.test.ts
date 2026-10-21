import { describe, expect, it } from "vitest";
import { buildJobStory, jobComparisonConclusion, jobComparisonRows, jobIdentitySeed, mergeJobReconciliationEvidence } from "./jobStoryModel";

describe("Job reported evidence", () => {
  it("takes native Job values over conflicting projections and never fills native null/missing from children", () => {
    const model = buildJobStory({ job_id: "job", status: "failed", total_rows_read: 999, child_total_rows_read: 888,
      raw_record_json: JSON.stringify({ _type: "job_run_log", job_id: "job", status: "succeeded", total_rows_read: null, total_failed: 1 }) });
    expect(model.reported.status).toBe("succeeded");
    expect(model.reported.total_rows_read).toBeNull();
    expect(model.reported.total_rows_written).toBeUndefined();
    expect(model.reported.total_failed).toMatchObject({ value: "1" });
  });
  it.each(["{broken", '{"_type":"dataflow_run_log","status":"failed"}', "null"])("does not fall back to projections for invalid native payload %s", payload => {
    const model = buildJobStory({ job_id: "job", status: "succeeded", raw_record_json: payload });
    expect(model.invalid).toBe(true);
    expect(model.reported.status).toBeUndefined();
  });
  it.each(["loading", "error", "not-found"] as const)("does not advertise unloaded Job metrics for %s", availability => {
    const model = buildJobStory({ job_id: "job", status: "succeeded", total_dataflows: 3 }, availability);
    expect(model.reported).toEqual({ job_id: "job" });
    expect(jobComparisonConclusion(model)).toBe("unk");
  });
  it("strips linked Dataflow content while preserving exact empty/null identity", () => {
    expect(jobIdentitySeed({ job_id: " job ", log_session_id: "", _source_id: 2, dataflow_name: "child", message: "child", raw_record_json: "child", status: "failed" }))
      .toEqual({ job_id: " job ", log_session_id: "", _source_id: 2 });
    expect(jobIdentitySeed({ job_id: "job", log_session_id: null })).toEqual({ job_id: "job", log_session_id: null });
  });
  it("does not promote a backend matched status with missing expected total", () => {
    expect(jobComparisonConclusion(buildJobStory({ total_dataflows: null, child_dataflow_count: 2, child_scope_coverage: "full", reconciliation_status: "matched" }))).toBe("Expected total unknown");
    expect(jobComparisonConclusion(buildJobStory({ total_dataflows: 2, child_scope_coverage: "stale_snapshot", reconciliation_status: "matched" }))).toContain("older");
    expect(jobComparisonConclusion(buildJobStory({ total_dataflows: 0, child_scope_coverage: "full", reconciliation_status: "matched" }))).toBe("Child count matched");
  });
  it("compares supplied aggregates with exact differences and explicit populations", () => {
    const rows = jobComparisonRows(buildJobStory({ child_scope_coverage: "full", child_metric_eligible_count: 2, total_rows_read: "9007199254740993", child_total_rows_read: "9007199254740995", child_rows_read_measured_count: 2,
      total_rows_inserted: 100, child_total_rows_inserted: 100, total_bytes_added: null, child_total_bytes_added: 3, child_failed_count: 0 }));
    expect(rows.find(row => row.metric === "Rows read")).toMatchObject({ difference: "2", population: "Excludes maintenance", measured: 2 });
    expect(rows.find(row => row.metric === "Bytes added")).toMatchObject({ difference: null, population: "All child operations" });
    expect(rows.map(row => row.metric)).not.toContain("Inserted");
    expect(rows.find(row => row.metric === "Failed")?.reported).toBeUndefined();
  });
  it.each(["subset", "stale_snapshot", "unknown_snapshot", "lost_detail", "unknown_loss", "unknown"])("withholds differences under %s coverage while retaining operands", scope => {
    const row = jobComparisonRows(buildJobStory({ child_scope_coverage: scope, total_dataflows: 2, child_dataflow_count: 1 }))[0];
    expect(row).toMatchObject({ reported: 2, observed: 1, difference: null, differenceNote: "Comparison coverage incomplete" });
  });
  it("requires complete per-metric coverage for v7 partial sums and uses separate row/byte populations", () => {
    const rows = jobComparisonRows(buildJobStory({ child_scope_coverage: "full", child_dataflow_count: 3, child_metric_eligible_count: 2,
      total_rows_read: 100, child_total_rows_read: 95, child_rows_read_measured_count: 2,
      total_rows_written: 100, child_total_rows_written: 50, child_rows_written_measured_count: 1,
      total_bytes_added: 30, child_total_bytes_added: 20, child_bytes_added_measured_count: 2,
      total_bytes_removed: 30, child_total_bytes_removed: 40, child_bytes_removed_measured_count: 3 }));
    expect(rows.find(row => row.metric === "Rows read")).toMatchObject({ difference: "-5", eligible: 2, group: "Rows" });
    expect(rows.find(row => row.metric === "Rows written")).toMatchObject({ difference: null, observed: 50, differenceNote: "Measurements incomplete" });
    expect(rows.find(row => row.metric === "Bytes added")).toMatchObject({ difference: null, eligible: 3 });
    expect(rows.find(row => row.metric === "Bytes removed")).toMatchObject({ difference: "10", eligible: 3 });
  });
  it("does not infer missing measurement coverage or lose measured zeros", () => {
    const missing = jobComparisonRows(buildJobStory({ child_scope_coverage: "full", total_rows_read: 0, child_total_rows_read: 0, child_rows_read_measured_count: 1 }))[0];
    expect(missing).toMatchObject({ difference: null, differenceNote: "Measurement coverage unknown" });
    const zero = jobComparisonRows(buildJobStory({ child_scope_coverage: "full", total_dataflows: 0, child_dataflow_count: 0 }))[0];
    expect(zero.difference).toBe("0");
  });
  it("keeps selected expected/observed totals in one snapshot while loading newer native Job values", () => {
    const model = buildJobStory(mergeJobReconciliationEvidence({ total_dataflows: 3, total_rows_read: 999, child_dataflow_count: 3,
      child_metric_eligible_count: 3, raw_record_json: '{"_type":"job_run_log","total_dataflows":3,"total_rows_read":999}' },
    { reconciliation_status: "matched", child_scope_coverage: "full", total_dataflows: 2, child_dataflow_count: 2, observed_at: "old", child_metric_eligible_count: 2 }));
    expect(jobComparisonRows(model).find(row => row.metric === "Dataflows")).toMatchObject({ reported: 2, observed: 2, difference: "0" });
    expect(model.reported.total_dataflows).toMatchObject({ value: "3" });
    expect(model.comparisonReported.total_rows_read).toBeUndefined();
    expect(model.evidence.observed_at).toBe("old");
    expect(model.evidence.child_metric_eligible_count).toBe(2);
    expect(model.evidence.child_rows_read_measured_count).toBeUndefined();
    expect(jobComparisonConclusion(model)).toBe("Child count matched");
  });
  it("uses fresh Job evidence for linked Dataflow identity and rejects invalid selected payload totals", () => {
    const detail = { total_dataflows: 3, child_dataflow_count: 3, child_scope_coverage: "full", reconciliation_status: "matched" };
    const linked = buildJobStory(mergeJobReconciliationEvidence(detail, { job_id: "job" }));
    expect(jobComparisonRows(linked)[0].difference).toBe("0");
    const invalid = buildJobStory(mergeJobReconciliationEvidence(detail, { reconciliation_status: "matched", total_dataflows: 2, raw_record_json: "{bad" }));
    expect(invalid.comparisonReported).toEqual({});
    expect(jobComparisonConclusion(invalid)).toBe("Expected total unknown");
  });
});
