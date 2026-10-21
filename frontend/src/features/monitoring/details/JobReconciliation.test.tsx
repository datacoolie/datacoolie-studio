import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { JobReconciliation, JobReconciliationContent } from "./JobReconciliation";
import { buildJobStory } from "./jobStoryModel";

describe("Job reconciliation evidence", () => {
  const evidence = { child_scope_coverage: "full", reconciliation_status: "matched", total_dataflows: 2, child_dataflow_count: 2,
    total_succeeded: 2, child_succeeded_count: 1, total_rows_read: 100, child_total_rows_read: 100,
    child_rows_read_measured_count: 1, child_metric_eligible_count: 1, total_bytes_added: 20,
    child_total_bytes_added: 20, child_bytes_added_measured_count: 2, log_records_dropped: 0, log_bytes_dropped: 0 };
  it("keeps the evidence body unmounted until expanded", () => {
    const html = renderToStaticMarkup(<JobReconciliation model={buildJobStory(evidence)} />);
    expect(html).toContain("Reconciliation");
    expect(html).not.toContain("Comparison basis");
  });
  it("explains the main check separately from supplementary comparisons with the shared status", () => {
    const html = renderToStaticMarkup(<JobReconciliationContent model={buildJobStory(evidence)} />);
    for (const title of ["Result", "Comparison basis", "Dataflow count", "Status counts", "Rows", "Bytes", "Job log", "Child logs", "Measured / eligible"]) expect(html).toContain(title);
    expect(html).toContain("Matched");
    expect(html).toContain("is-different");
    expect(html).not.toContain("Current-filter");
    expect(html).not.toContain("Report issues");
  });
  it("does not highlight incomplete comparisons and explains missing differences", () => {
    const html = renderToStaticMarkup(<JobReconciliationContent model={buildJobStory({ ...evidence, child_scope_coverage: "stale_snapshot", reconciliation_status: "insufficient_coverage" })} />);
    expect(html).toContain("Insufficient Coverage");
    expect(html).toContain("Snapshot is older");
    expect(html).toContain('aria-description="Comparison coverage incomplete"');
    expect(html).not.toContain("is-different");
  });
});
