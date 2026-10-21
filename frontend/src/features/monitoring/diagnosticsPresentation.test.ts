import { describe, expect, it } from "vitest";
import {
  diagnosticsCoverageSummary,
  diagnosticsCategoryLabel,
  diagnosticsEvidenceItems,
  diagnosticsInvestigationActions,
  diagnosticsLinkedJobRow,
  diagnosticsLinkagePresentation,
  diagnosticsRuleDescription,
  reconciliationEvidenceBasis,
  diagnosticsSeverityPresentation,
  diagnosticsSourceLabel,
} from "./diagnosticsPresentation";

describe("Diagnostics presentation", () => {
  it("uses user-facing severity labels", () => {
    expect(diagnosticsSeverityPresentation("bad")).toEqual({ label: "Issue", tone: "bad" });
    expect(diagnosticsSeverityPresentation("warning")).toEqual({ label: "Warning", tone: "warning" });
    expect(diagnosticsSeverityPresentation("info")).toEqual({ label: "Info", tone: "info" });
  });

  it("shows zero-count linkage problems as clear", () => {
    expect(diagnosticsLinkagePresentation({ category: "orphan_dataflow_job_id", count: 0, severity: "bad" })).toEqual({ label: "Clear", tone: "good" });
    expect(diagnosticsLinkagePresentation({ category: "job_without_dataflow_records", count: 0, severity: "warning" })).toEqual({ label: "Clear", tone: "good" });
    expect(diagnosticsLinkagePresentation({ category: "orphan_dataflow_job_id", count: 2, severity: "bad" })).toEqual({ label: "Issue", tone: "bad" });
  });

  it("labels opaque source IDs as log sources", () => {
    expect(diagnosticsSourceLabel("source:21")).toBe("Log source 21");
    expect(diagnosticsSourceLabel("direct-reader")).toBe("Direct reader");
  });

  it("uses evidence coverage instead of the internal completeness category", () => {
    expect(diagnosticsCategoryLabel("field completeness")).toBe("Field coverage");
    expect(diagnosticsCategoryLabel("source_coverage")).toBe("Source readiness");
  });

  it("keeps conditional evidence visible without treating it as an issue", () => {
    const summary = diagnosticsCoverageSummary([
      { group: "runtime duration", actionable: true, applicability: "universal", severity: "warning" },
      { group: "identity/linkage", actionable: true, applicability: "universal", severity: "good" },
      { group: "watermark evidence", actionable: false, applicability: "conditional", severity: "bad", records: 1 },
      { group: "time/status", actionable: true, applicability: "universal", severity: "info" },
    ]);
    expect(summary.issues).toHaveLength(1);
    expect(summary.ready).toHaveLength(1);
    expect(summary.conditional).toHaveLength(1);
    expect(summary.notApplicable).toHaveLength(0);
    expect(summary.unavailable).toHaveLength(2);
    expect(summary.visible.map((row) => row.group)).toEqual(["runtime duration", "watermark evidence", "identity/linkage", "time/status"]);
  });

  it("links only diagnostics backed by an existing job log", () => {
    expect(diagnosticsLinkedJobRow(
      { category: "reconciliation mismatch" },
      { job_id: "job-7", metric: "total_failed" },
    )).toMatchObject({ job_id: "job-7", metric: "total_failed" });
    expect(diagnosticsLinkedJobRow(
      { category: "job without dataflows" },
      { job_id: "job-8", job_status: "succeeded", expected_children: 2 },
    )).toMatchObject({ job_id: "job-8", status: "succeeded" });
    expect(diagnosticsLinkedJobRow(
      { category: "orphan dataflow job id" },
      { job_id: "job-missing" },
    )).toBeNull();
  });

  it("preserves opaque linked-job identity and promoted source/session scope", () => {
    const linked = diagnosticsLinkedJobRow(
      { category: "reconciliation mismatch", job_id: "row job", log_session_scope: "null" },
      { job_id: "  ", _source_id: 7, log_session_id: "", log_session_scope: "exact", metric: "rows_written" },
    );
    expect(linked).toMatchObject({
      job_id: "  ",
      _source_id: 7,
      log_session_id: "",
      log_session_scope: "exact",
    });
  });

  it("projects evidence coverage into one category-aware metric list", () => {
    const items = diagnosticsEvidenceItems(
      "field completeness",
      { severity: "warning" },
      {
        record_type: "dataflow",
        group: "runtime duration",
        applicability: "universal",
        completeness_rate: 88.73,
        records: 14654,
        required_fields: 4,
        present_values: 52011,
        missing_values: 6605,
        fields: "duration_seconds, source_duration_seconds",
      },
    );

    expect(items.map((item) => item.label)).toEqual([
      "Presence",
      "Value availability",
      "Applicability",
      "Record type",
      "Group",
      "Records",
      "Applicable fields",
      "Present fields",
      "Missing fields",
      "Fields",
    ]);
    expect(items[0]).toMatchObject({ value: "88.73%", intent: "warning", primary: true });
    expect(items[1]).toMatchObject({ value: "Not measured", intent: "info" });
    expect(items.at(-1)).toMatchObject({ wide: true });
  });

  it("uses category-specific reconciliation evidence", () => {
    const items = diagnosticsEvidenceItems(
      "reconciliation mismatch",
      { target: "job-7", severity: "bad" },
      { metric: "rows_written", expected: 120, observed: 100, difference: 20 },
    );

    expect(items.map((item) => [item.label, item.value])).toEqual([
      ["Job ID", "job-7"],
      ["Metric", "rows_written"],
      ["Assessment", "Not assessed"],
      ["Evidence basis", "Snapshot time unknown · Full child scope · Loss counters unknown"],
      ["Expected", 120],
      ["Observed", 100],
      ["Difference", 20],
    ]);
    expect(items.at(-1)).toMatchObject({ intent: "bad", primary: true });
  });

  it("explains insufficient reconciliation coverage instead of presenting a delta", () => {
    const evidence = {
      status: "insufficient_coverage",
      scope_coverage: "unknown_snapshot",
      expected: "2",
      observed: "1",
      difference: null,
      observed_at: null,
      latest_child_time: "2026-10-07T01:00:00Z",
      log_records_dropped: "0",
      log_bytes_dropped: "0",
    };
    const items = diagnosticsEvidenceItems(
      "reconciliation mismatch",
      { severity: "warning" },
      evidence,
    );
    expect(reconciliationEvidenceBasis(evidence)).toContain("Snapshot time unknown");
    expect(items.map((item) => item.label)).toEqual([
      "Status",
      "Scope coverage",
      "Assessment",
      "Evidence basis",
      "Expected",
      "Observed",
      "Latest child",
      "Records dropped",
      "Bytes dropped",
    ]);
    expect(items.find((item) => item.label === "Evidence basis")?.value).toContain("Snapshot time unknown");
    expect(items.find((item) => item.label === "Difference")).toBeUndefined();
  });

  it.each([
    ["lost_detail", "Log detail was dropped"],
    ["unknown_loss", "Loss scope unknown"],
  ])("keeps the reconciliation %s scope visible", (scope, expected) => {
    const basis = reconciliationEvidenceBasis({
      status: "insufficient_coverage",
      scope_coverage: scope,
      observed_at: "2026-10-07T01:00:00Z",
      latest_child_time: "2026-10-07T01:00:00Z",
      log_records_dropped: null,
      log_bytes_dropped: null,
    });
    expect(basis).toContain(expected);
    expect(basis).not.toContain("Full child scope");
  });

  it.each([
    {
      category: "source coverage",
      row: { severity: "warning", target: "source:21" },
      evidence: { source: "source:21", file_kind: "job_json", warning_count: 2 },
      labels: ["Source", "File kind"],
    },
    {
      category: "orphan dataflow job id",
      row: { severity: "bad", target: "job-9", latest_time: "2026-07-16T01:00:00Z" },
      evidence: { dataflow_records: 4, job_total_dataflows: 0 },
      labels: ["Job ID", "Child records", "Expected children", "Latest"],
    },
    {
      category: "read/cache warning",
      row: { severity: "warning", target: "logs/analyst" },
      evidence: { status: "warning", message: "Cache is stale" },
      labels: ["Source / path", "Status", "Message"],
    },
  ])("projects $category evidence without generic duplicate cards", ({ category, row, evidence, labels }) => {
    expect(diagnosticsEvidenceItems(category, row, evidence).map((item) => item.label)).toEqual(labels);
  });

  it("keeps remediation only in the investigation path and uses Core integrity copy", () => {
    expect(diagnosticsRuleDescription("field completeness")).toContain("required Monitoring evidence-field group");
    expect(
      diagnosticsInvestigationActions(
        { category: "field completeness", action_hint: "Confirm the producer fields." },
        {},
      ),
    ).toEqual([
      "Confirm the producer fields.",
      "Confirm the admitted log version emits this field group.",
      "This affects evidence coverage; it is separate from execution health.",
    ]);
  });
});
