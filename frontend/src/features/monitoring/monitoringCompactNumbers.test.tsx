import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CompactNumberValue, DurationHeadline, MeasurementCoverage, RuntimePhaseContribution, RuntimePhaseMatrixTooltip, formatCompact, formatNumber } from "./components/monitoringPresentation";
import { scatterObservations } from "./components/monitoringObservation";
import { RepeatedFailureTable } from "./pages/FailurePageSupport";

describe("Monitoring compact numbers", () => {
  it("uses execution-scope metrics directly in the final table row", () => {
    const html = renderToStaticMarkup(<RuntimePhaseMatrixTooltip labelKey="operation_type" row={{operation_type:"etl",source_duration_seconds:30,source_duration_percent:25,source_run_count:6,source_failed:2,source_avg_duration_seconds:10,source_p95_duration_seconds:20,destination_duration_seconds:90,destination_duration_percent:75,destination_run_count:3,destination_failed:1,destination_avg_duration_seconds:30,destination_p95_duration_seconds:50,run_scope_duration_seconds:180,run_scope_avg_duration_seconds:60,run_scope_p95_duration_seconds:80,run_scope_run_count:"9007199254740993",run_scope_failed_count:"9007199254740992"}} />);
    const footer = html.slice(html.indexOf("<tfoot>"));
    expect(footer).toContain('data-label="Total">3m</td>');
    expect(footer).toContain('data-label="Runs">9,007,199,254,740,993</td>');
    expect(footer).toContain('data-label="Failed" class="help-tone-failed">9,007,199,254,740,992</td>');
    expect(footer).toContain('data-label="AVG">1m</td>');
    expect(footer).toContain('data-label="P95">1.33m</td>');
    expect(footer).toContain('data-label="%">100%</td>');
  });
  it("preserves unavailable total evidence and observed zero without phase fallback", () => {
    for (const value of [null, 0]) {
      const html = renderToStaticMarkup(<RuntimePhaseMatrixTooltip labelKey="stage" row={{stage:"bronze",source_duration_seconds:30,source_run_count:1,run_scope_duration_seconds:value,run_scope_avg_duration_seconds:value,run_scope_p95_duration_seconds:value,run_scope_run_count:2,run_scope_failed_count:0}} />);
      const footer = html.slice(html.indexOf("<tfoot>"));
      expect(footer).toContain(`data-label="Total">${value === null ? "Unavailable" : "0s"}</td>`);
      expect(footer).toContain(`data-label="AVG">${value === null ? "Unavailable" : "0s"}</td>`);
      expect(footer).toContain(`data-label="P95">${value === null ? "Unavailable" : "0s"}</td>`);
      expect(footer).toContain(`data-label="%">${value === null ? "—" : "0%"}</td>`);
      expect(footer).toContain('data-label="Runs">2</td>');
    }
  });
  it("rounds duration tooltip metrics with grouping without losing integer counts", () => {
    const duration = renderToStaticMarkup(<DurationHeadline avgSeconds={1234.56789} p50Seconds={1.378974} />);
    expect(duration).toContain('aria-description="1,234.57 seconds"');
    expect(duration).toContain('aria-description="1.38 seconds"');
    const phase = renderToStaticMarkup(<RuntimePhaseMatrixTooltip labelKey="operation_type" row={{operation_type:"etl",total_duration_seconds:18911.482,source_duration_seconds:4858.132,source_duration_percent:25.7,source_avg_duration_seconds:0.27653,source_p95_duration_seconds:0.79004,source_run_count:"9007199254740993"}} />);
    expect(phase).not.toContain("help-title");
    expect(phase).not.toContain("help-body");
    expect(phase).toContain('data-label="Total"><span class="phase-text-source">80.97m</span></td>');
    expect(phase).not.toContain("seconds");
    expect(phase).toContain("<tfoot");
    expect(phase).toContain("9,007,199,254,740,993");
  });
  it("compacts visible coverage captions while retaining exact evidence", () => {
    const html = renderToStaticMarkup(<MeasurementCoverage row={{ rows_read_measured: "1825", rows_read_eligible: "2000" }} field="rows_read" />);
    expect(html).toContain('aria-description="1,825 / 2,000 runs measured"');
    expect(html).toContain('>1k8 / 2k runs measured</span>');
    const observations = scatterObservations(Array.from({ length: 2000 }, (_, index) => index), (index) => index < 1825 ? 1 : null, () => 2);
    expect(observations.coverage).toBe("1825 / 2000 points measured; 175 missing measurements");
    expect(observations.compactCoverage).toBe("1k8 / 2k point · 175 missing");
    expect(scatterObservations(Array.from({ length: 200 }, (_, index) => ({ x: index, y: index })), (row) => row.x, (row) => row.y).compactCoverage)
      .toBe("200 / 200 point");
    expect(scatterObservations(Array.from({ length: 2000 }, (_, index) => ({ x: index, y: index })), (row) => row.x, (row) => row.y).compactCoverage)
      .toBe("2k / 2k point");
  });

  it("uses infix display and grouped exact title/aria without changing exact tooltips", () => {
    const html = renderToStaticMarkup(<CompactNumberValue value="1825" />);
    expect(html).toContain('aria-label="1,825"');
    expect(html).toContain('aria-description="1,825"');
    expect(html).toContain('>1k8</span>');
    expect(html).not.toContain(' title=');
    expect(formatCompact(1825)).toBe("1k8");
    expect(formatNumber("1825")).toBe("1,825");
  });

  it("uses the same compact run count in Runtime phase contribution", () => {
    const html = renderToStaticMarkup(<RuntimePhaseContribution rows={[
      { operation_type: "etl", phase_runs_eligible: "1825", phase_runs_measured: "1824", total_duration_seconds: 2,
        source_duration_seconds: 2, source_duration_percent: 100, source_run_count: "1824" }
    ]} />);
    expect(html).toContain('aria-label="1,825"');
    expect(html).toContain('>1k8</span> runs');
  });

  it("compacts explicit string metrics in tables while preserving identities", () => {
    const html = renderToStaticMarkup(<RepeatedFailureTable rows={[
      { failure_category: "1825", failed_runs: "1825", affected_jobs: "1250", affected_dataflows: "1000" }
    ]} maxRows={12} />);
    expect(html).toContain(">1825</td>");
    expect(html).toContain('aria-label="1,825"');
    expect(html).toContain('>1k8</span>');
    expect(html).toContain('aria-label="1,250"');
    expect(html).toContain('>1k3</span>');
  });
});
