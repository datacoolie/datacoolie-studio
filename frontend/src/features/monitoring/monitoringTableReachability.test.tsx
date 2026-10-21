import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { normalizeMonitoringPage } from "./monitoringQueries";
import { DEFAULT_MONITORING_FILTERS } from "./monitoringFilters";
import { DiagnosticsPage } from "./pages/DiagnosticsPage";
import { CaptureSummaryLine } from "./pages/DiagnosticsRegistry";
import { FailurePage } from "./pages/FailurePage";
import { FailureQueueTable } from "./pages/FailurePageSupport";

const EMPTY_MONITORING_REPORT = normalizeMonitoringPage({ summary: {} } as never);

describe("Monitoring table reachability", () => {
  it("uses concise failure activity types and neutral attempt context", () => {
    const markup = renderToStaticMarkup(
      <FailureQueueTable
        rows={[
          {
            activity_type: "failed_run",
            activity_context: "source",
            failure_phase: "source",
            dataflow_name: "Source import",
            activity_message: "Source connection failed"
          },
          {
            activity_type: "recovered_attempt",
            activity_context: "Attempt 2",
            attempt_number: 2,
            dataflow_name: "Customer sync",
            activity_message: "Temporary timeout"
          }
        ]}
        maxRows={10}
      />
    );

    expect(markup).toContain('aria-description="Terminally failed dataflow run"');
    expect(markup).toContain('<span class="ds-tag ds-tag-danger">Failed</span>');
    expect(markup).toContain('aria-description="Failed attempt within a dataflow run that later succeeded"');
    expect(markup).toContain('<span class="ds-tag ds-tag-warning">Recovered</span>');
    expect(markup).toContain('<span class="failure-phase-badge is-attempt">Attempt 2</span>');
  });

  it("exposes Diagnostics rows past the old hard-coded table limits", () => {
    const report = {
      ...EMPTY_MONITORING_REPORT,
      diagnostics: {
        ...EMPTY_MONITORING_REPORT.diagnostics,
        source_coverage: Array.from({ length: 60 }, (_, index) => ({
          source: `source-${index + 1}`,
          file_kind: "json",
          file_count: 1,
          records: index + 1,
          warning_count: 0,
          latest_log_at: null
        })),
        finding_groups: Array.from({ length: 50 }, (_, index) => ({
          group_key: `diagnostic_capture|truncated|/field-${index + 1}`,
          kind: "warning",
          rule: "diagnostic_capture",
          reason: "truncated",
          section: `/field-${index + 1}`,
          subject_kind: "dataflow_record",
          affected_objects: String(index + 1),
          latest_time: null
        })) as never,
        findings_summary: {
          scope: "full",
          issue_groups: "0",
          warning_groups: "60",
          limitation_groups: "0",
          groups_total: "60",
          groups_loaded: "50",
          group_limit: 50,
          affected_objects: [{ kind: "warning", subject_kind: "dataflow_record", count: "60" }],
          restrictions: [],
          analytics_generation: "generation-1",
          as_of: "2026-10-19T00:00:00+00:00"
        } as never,
        diagnostic_capture: {
          summary: { affected_records: 30, known_markers: 30, omitted_marker_records: 0 },
          by_reason: Array.from({ length: 9 }, (_, index) => ({ reason: `reason-${index + 1}`, count: index + 1 })),
          evidence: Array.from({ length: 30 }, (_, index) => ({
            source_id: `capture-source-${index + 1}`,
            record_type: "job",
            log_session_id: `session-${index + 1}`,
            job_id: `capture-job-${index + 1}`,
            dataflow_id: null,
            dataflow_run_id: null,
            field: "status",
            reason: `capture-evidence-${index + 1}`,
            latest_time: null
          }))
        }
      }
    };

    const markup = renderToStaticMarkup(
      <QueryClientProvider client={new QueryClient()}>
        <DiagnosticsPage report={report} environmentId={1} filters={DEFAULT_MONITORING_FILTERS} />
      </QueryClientProvider>,
    );

    expect(markup.match(/class="table-pager-range">1-50 of 60/g)).toHaveLength(2);
    expect(markup).toContain("source-50");
    expect(markup).not.toContain("source-51");
    expect(markup).toContain("/field-50");
    expect(markup).not.toContain("/field-51");
    expect(markup).toContain("capture-evidence-30");
    // The old collapsed Reasons table is replaced by per-reason counts in the caption.
    expect(markup).not.toContain("diagnostics-capture-reasons");
  });

  it("shows capture counts per reason on the caption line, largest first", () => {
    const markup = renderToStaticMarkup(
      <CaptureSummaryLine capture={{
        summary: { affected_records: "61", known_markers: "61", omitted_marker_records: "1" },
        by_reason: [{ reason: "omitted", count: "1" }, { reason: "truncated", count: "60" }],
        evidence: [],
      }} />,
    );
    expect(markup).toContain("Current filters");
    expect(markup.indexOf("truncated")).toBeGreaterThan(-1);
    expect(markup.indexOf("truncated")).toBeLessThan(markup.indexOf("omitted"));
    expect(markup).toContain("1 omitted marker-list records");
  });

  it("labels compact Failure tables as top-six views of the backend top-thirty", () => {
    const report = {
      ...EMPTY_MONITORING_REPORT,
      failures: {
        ...EMPTY_MONITORING_REPORT.failures,
        repeated_signatures: Array.from({ length: 30 }, (_, index) => ({
          failure_category: "source",
          failure_phase: "source",
          latest_error: `signature-error-${index + 1}`,
          failed_runs: 30 - index,
          affected_jobs: 1,
          affected_dataflows: 1,
          latest_time: null
        })),
        endpoint_impact: Array.from({ length: 30 }, (_, index) => ({
          source_name: `source-${index + 1}`,
          destination_name: `destination-${index + 1}`,
          failed_runs: 30 - index,
          affected_jobs: 1,
          latest_time: null
        }))
      }
    };

    const markup = renderToStaticMarkup(<FailurePage report={report} filters={DEFAULT_MONITORING_FILTERS} />);

    expect(markup).toContain("Top 6 by failed runs · from up to 30 signatures");
    expect(markup).toContain("Top 6 by failed runs · from up to 30 source-to-destination pairs");
  });
});
