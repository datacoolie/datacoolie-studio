import { describe, expect, it } from "vitest";
import type { DiagnosticsFindingObject } from "../../shared/api/domainTypes";
import { diagnosticsEvidenceItems, diagnosticsLinkedJobRow } from "./diagnosticsPresentation";
import { findingGroupSummary, findingObjectToDrawerRow, findingUnits, scopeNoticeText } from "./diagnosticsFindings";
import { DEFAULT_MONITORING_FILTERS } from "./monitoringFilters";
import { monitoringDiagnosticsEvidenceOptions } from "./monitoringQueries";

const OBJECT_BASE = { group_key: "k", section: "", subject_kind: "job" as const };

describe("Diagnostics findings presentation", () => {
  it("keeps exact nullable identity when adapting an object to the drawer", () => {
    const empty = findingObjectToDrawerRow({
      ...OBJECT_BASE, rule: "reconciliation_mismatch", reason: "count_mismatch",
      source_id: 7, log_session_id: "", job_id: "00123",
      metrics: [{ metric: "total_dataflows", expected: "9007199254740993", observed: "1", difference: "-9007199254740992" }],
    } satisfies DiagnosticsFindingObject, "issue");
    const linked = diagnosticsLinkedJobRow(empty, empty.evidence as Record<string, unknown>);
    expect(linked).toMatchObject({ _source_id: 7, log_session_id: "", job_id: "00123" });
    const items = diagnosticsEvidenceItems(empty.category, empty, empty.evidence as Record<string, unknown>);
    expect(items.find((item) => item.label === "Total Dataflows")?.value).toContain("9007199254740993");

    const nullSession = findingObjectToDrawerRow({
      ...OBJECT_BASE, rule: "log_loss", reason: "records", source_id: 7, log_session_id: null, job_id: "job",
    } satisfies DiagnosticsFindingObject, "warning");
    expect(diagnosticsLinkedJobRow(nullSession, nullSession.evidence as Record<string, unknown>)).toMatchObject({ log_session_id: null });
  });

  it("does not assert snapshot or loss conclusions for a filtered child scope", () => {
    const row = findingObjectToDrawerRow({
      ...OBJECT_BASE, rule: "scope_child_subset", reason: "filtered_child_scope",
      source_id: 1, log_session_id: "s", job_id: "j", checks: "4",
    } satisfies DiagnosticsFindingObject, "notice");
    const items = diagnosticsEvidenceItems(row.category, row, row.evidence as Record<string, unknown>);
    const labels = items.map((item) => item.label);
    expect(labels).not.toContain("Records dropped");
    expect(labels).toContain("Reconciliation checks");
    expect(row.severity).toBe("info");
  });

  it("names the counted unit and never adds heterogeneous units", () => {
    expect(findingUnits({ subject_kind: "job", rule: "reconciliation_unassessed" })).toEqual({ objects: "jobs", detail: "checks" });
    expect(findingUnits({ subject_kind: "dataflow_record", rule: "diagnostic_capture" }).detail).toBe("markers");
    expect(findingGroupSummary({ rule: "diagnostic_capture", reason: "omitted", section: "" })).toContain("Diagnostic marker list");
    expect(scopeNoticeText({ code: "filtered_child_scope", group_key: "x", jobs: "108", checks: "432", restrictions: ["status"] }))
      .toContain("108 jobs · 432 reconciliation checks");
  });
});

describe("Diagnostics evidence query", () => {
  it("carries anchor, generation and selector in the query identity", () => {
    const base = { limit: 50, offset: 0, asOf: "2026-10-19T00:00:00+00:00", generation: "g1" };
    const registry = monitoringDiagnosticsEvidenceOptions(1, DEFAULT_MONITORING_FILTERS, { ...base, view: "registry" });
    const detail = monitoringDiagnosticsEvidenceOptions(1, DEFAULT_MONITORING_FILTERS, { ...base, view: "detail", findingGroup: "log_loss|records|" });
    const other = monitoringDiagnosticsEvidenceOptions(1, DEFAULT_MONITORING_FILTERS, { ...base, generation: "g2", view: "registry" });
    expect(registry.queryKey.at(-1)).toMatchObject({ view: "registry", findingKind: "all", asOf: base.asOf, expectedGeneration: "g1" });
    expect(registry.queryKey.at(-1)).not.toHaveProperty("findingGroup");
    expect(detail.queryKey.at(-1)).toMatchObject({ view: "detail", findingGroup: "log_loss|records|" });
    expect(detail.queryKey.at(-1)).not.toHaveProperty("findingKind");
    expect(other.queryKey).not.toEqual(registry.queryKey);
    expect(monitoringDiagnosticsEvidenceOptions(1, DEFAULT_MONITORING_FILTERS, { ...base, view: "detail", findingGroup: null }).enabled).toBe(false);
  });
});
