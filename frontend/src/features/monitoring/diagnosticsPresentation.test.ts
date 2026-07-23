import { describe, expect, it } from "vitest";
import {
  diagnosticsCoverageSummary,
  diagnosticsCategoryLabel,
  diagnosticsEvidenceItems,
  diagnosticsInvestigationActions,
  diagnosticsLinkedJobRow,
  diagnosticsLinkagePresentation,
  diagnosticsRuleDescription,
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
    expect(diagnosticsCategoryLabel("field completeness")).toBe("Evidence coverage");
    expect(diagnosticsCategoryLabel("source_coverage")).toBe("Source Coverage");
  });

  it("keeps conditional evidence visible without treating it as an issue", () => {
    const summary = diagnosticsCoverageSummary([
      { group: "runtime duration", actionable: true, applicability: "universal", severity: "warning" },
      { group: "identity/linkage", actionable: true, applicability: "universal", severity: "good" },
      { group: "watermark evidence", actionable: false, applicability: "conditional", severity: "bad" },
      { group: "time/status", actionable: true, applicability: "universal", severity: "info" },
    ]);
    expect(summary.issues).toHaveLength(1);
    expect(summary.ready).toHaveLength(1);
    expect(summary.conditional).toHaveLength(1);
    expect(summary.unavailable).toHaveLength(1);
    expect(summary.visible.map((row) => row.group)).toEqual(["runtime duration", "watermark evidence", "identity/linkage", "time/status"]);
  });

  it("links only diagnostics backed by an existing job log", () => {
    expect(diagnosticsLinkedJobRow(
      { category: "reconciliation mismatch" },
      { job_id: "job-7", metric: "total_failed" },
    )).toMatchObject({ job_id: "job-7", metric: "total_failed" });
    expect(diagnosticsLinkedJobRow(
      { category: "job without dataflows" },
      { job_id: "job-8", job_status: "succeeded" },
    )).toMatchObject({ job_id: "job-8", status: "succeeded" });
    expect(diagnosticsLinkedJobRow(
      { category: "orphan dataflow job id" },
      { job_id: "job-missing" },
    )).toBeNull();
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
      "Completeness",
      "Applicability",
      "Record type",
      "Group",
      "Records",
      "Required fields",
      "Present values",
      "Missing values",
      "Fields",
    ]);
    expect(items[0]).toMatchObject({ value: "88.73%", intent: "warning", primary: true });
    expect(items[1]).toMatchObject({ value: "Required", intent: "neutral" });
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
      ["Expected", 120],
      ["Observed", 100],
      ["Difference", 20],
    ]);
    expect(items.at(-1)).toMatchObject({ intent: "bad", primary: true });
  });

  it.each([
    {
      category: "source coverage",
      row: { severity: "warning", target: "source:21" },
      evidence: { source: "source:21", file_kind: "job_jsonl", warning_count: 2 },
      labels: ["Source", "File kind", "Warnings"],
    },
    {
      category: "orphan dataflow job id",
      row: { severity: "bad", target: "job-9", latest_time: "2026-07-16T01:00:00Z" },
      evidence: { dataflow_records: 4, job_total_dataflows: 0 },
      labels: ["Job ID", "Dataflow records", "Job total dataflows", "Latest"],
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
      "Confirm the ETL log version emits this field group.",
      "This affects evidence coverage, not Core integrity.",
    ]);
  });
});
