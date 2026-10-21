import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { JobConfiguration, JobDetailSections, jobCapturedFields, jobReconciliationNote, jobReconciliationScope } from "./JobDetails";

import { buildJobStory } from "./jobStoryModel";
import { StoryFields } from "./DataflowStoryValues";
import { readFileSync } from "node:fs";
import { jobMetricKeys, jobRunConfigKeys, jobRuntimeKeys, jobSummaryKeys } from "./jobStoryModel";

describe("Job reconciliation context", () => {
  it("keeps secondary evidence collapsed and unmounted", () => {
    const html = renderToStaticMarkup(<JobDetailSections row={{ job_id: "job-1", reconciliation_status: "matched", total_dataflows: 1, observed_at: "2026-10-09T10:00:00Z", log_records_dropped: 0 }} timezoneName="UTC" />);
    expect(html).toContain("Summary &amp; insights");
    expect(html).toContain("Run workload");
    expect(html).toContain("Configuration");
    expect(html).not.toContain("Comparison scope");
    expect(html).not.toContain("Records dropped");
    expect(html).not.toContain("2026-10-09 10:00:00 UTC");
    expect(html).not.toContain("Copy execution JSON");
    expect(html).toContain("No child runs recorded");
  });

  it.each([
    ["subset", "Filtered child dataflows", "Current filters"],
    ["stale_snapshot", "All child dataflows", "newer than the job snapshot"],
    ["unknown_snapshot", "All child dataflows", "snapshot time is missing"],
    ["unknown_total", "Expected child total unknown", "expected child total"],
    ["unknown_loss", "Child log coverage unknown", "completeness cannot be confirmed"],
    ["lost_detail", "Child log coverage incomplete", "records were dropped"],
  ])("explains %s without hiding unknown evidence", (scope, label, note) => {
    expect(jobReconciliationScope({ scope_coverage: scope })).toBe(label);
    expect(jobReconciliationNote({ scope_coverage: scope })).toContain(note);
  });

  it("explains an absent expected total with the repository's full child coverage", () => {
    expect(jobReconciliationNote({ total_dataflows: null, child_dataflow_count: 1, child_scope_coverage: "full", reconciliation_status: "matched" }))
      .toBe("The expected job total was not reported; available child totals are shown.");
  });
});

describe("Job content completeness", () => {
  it.each([6, 7])("routes every v%s captured Job field to summary, config or technical evidence", version => {
    const schema = JSON.parse(readFileSync(new URL(`../../../../../src/datacoolie_studio/domains/analytics/logging_v${version}.schema.json`, import.meta.url), "utf8"));
    const captured = Object.keys(schema.$defs.jobRecord.properties);
    const primary = new Set([...jobMetricKeys, ...jobRunConfigKeys, ...jobRuntimeKeys, ...jobSummaryKeys]);
    const technical = captured.filter(key => !primary.has(key));
    const fields = jobCapturedFields(Object.fromEntries(captured.map(key => [key, null])), technical);
    const technicalHtml = renderToStaticMarkup(<StoryFields expandStructured fields={fields} />);
    for (const key of technical) expect(technicalHtml).toContain(`data-field-path="/${key}"`);
    const fixture = JSON.parse(readFileSync(new URL("../../../../../tests/fixtures/datacoolie/logging/v6/job_snapshot.json", import.meta.url), "utf8"));
    const html = renderToStaticMarkup(<StoryFields expandStructured fields={jobCapturedFields(fixture, [...jobRunConfigKeys, ...jobRuntimeKeys])} />);
    for (const key of [...jobRunConfigKeys, ...jobRuntimeKeys]) expect(html).toContain(`data-field-path="/${key}"`);
    expect(html).toContain("false");
  });
  it("shows the native root message and independent Job/child status", () => {
    const html = renderToStaticMarkup(<JobDetailSections row={{ job_id: "job", message: "wrong child message", child_total_rows_read: 1000, raw_record_json: JSON.stringify({ _type: "job_run_log", job_id: "first-middle-last", status: "succeeded", message: "native Job message", error_message: "distinct error", total_failed: 1, total_rows_read: null }) }} />);
    expect(html).toContain("native Job message");
    expect(html).toContain("distinct error");
    expect(html).not.toContain("wrong child message");
    expect(html).toContain(">first-middle-last</button>");
    expect(html).toContain("unk");
    expect(html).not.toContain("1,000");
  });
  it("keeps configuration closed and renders nested values completely on expansion", () => {
    const model = buildJobStory({ job_id: "job", stop_on_error: false, retry_count: 0, run_attributes: '{"nested":{"all":"preserved"}}' });
    const closed = renderToStaticMarkup(<JobConfiguration model={model} />);
    expect(closed).not.toContain("preserved");
    const html = renderToStaticMarkup(<StoryFields expandStructured fields={jobCapturedFields(model.reported, ["stop_on_error", "retry_count", "run_attributes"])} />);
    expect(html).toContain("false");
    expect(html).toContain("preserved");
    expect(html).toContain("Copy JSON");
    expect(html).not.toContain(">JSON<");
  });
});
