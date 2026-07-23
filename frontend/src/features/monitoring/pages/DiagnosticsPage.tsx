import type { EChartsOption } from "echarts";
import type { MonitoringReport } from "../../../shared/api/types";
import {
  DataTable,
  DetailMetric,
  HealthStripCard,
  ReportChart,
  ReportPanel,
  baseChartOption,
  bottomAnchoredValueXAxis,
  fixedHorizontalBarGrid,
  fixedHorizontalCategoryAxis,
  formatNumber,
  formatPercent,
  formatTimestampForDisplay,
  horizontalBarDataZoom,
  horizontalBarSeriesSizing,
  monitoringTimezone,
  num,
  reportChartPalette,
  reportTightChartGrid
} from "../monitoringShared";
import {
  diagnosticsCoverageSummary,
  diagnosticsCategoryLabel,
  diagnosticsLinkagePresentation,
  diagnosticsSeverityPresentation,
  diagnosticsSourceLabel,
} from "../diagnosticsPresentation";

type DiagnosticsRow = Record<string, unknown>;

export function DiagnosticsPage({
  report,
  onInspect
}: {
  report: MonitoringReport;
  onInspect?: (row: DiagnosticsRow) => void;
}) {
  const diagnostics = report.diagnostics ?? {};
  const kpis = diagnostics.kpis ?? {};
  const timezoneName = monitoringTimezone(report);
  const recordEvidence = (diagnostics.record_evidence_by_date ?? []) as DiagnosticsRow[];
  const linkageSummary = (diagnostics.job_linkage_summary ?? []) as DiagnosticsRow[];
  const reconciliationByMetric = (diagnostics.reconciliation_by_metric ?? []) as DiagnosticsRow[];
  const fieldCompleteness = (diagnostics.field_completeness ?? []) as DiagnosticsRow[];
  const sourceCoverage = (diagnostics.source_coverage ?? []) as DiagnosticsRow[];
  const investigationQueue = (diagnostics.investigation_queue ?? []) as DiagnosticsRow[];
  const coverageSummary = diagnosticsCoverageSummary(fieldCompleteness);
  const healthStatus = String(kpis.health_status ?? "no_evidence");
  const mismatchCount = Number(kpis.reconciliation_mismatches ?? report.reconciliation.mismatch_count ?? 0);
  const warningCount = Number(kpis.read_errors ?? report.errors.length ?? 0);
  const fieldIssues = Number(kpis.field_readiness_issues ?? 0);
  const cacheWarnings = Number(kpis.cache_warning_count ?? 0);
  const linkageGaps = Number(kpis.orphan_dataflow_job_ids ?? 0) + Number(kpis.jobs_without_dataflow_records ?? 0);

  return (
    <div className="monitoring-page monitoring-diagnostics-report">
      <section className={`overview-health-strip monitoring-diagnostics-health-strip health-${healthIntent(healthStatus)}`}>
        <HealthStripCard
          label="Core integrity"
          value={healthLabel(healthStatus)}
          detail={
            <span>
              <DetailMetric label="read/cache" value={formatNumber(warningCount + cacheWarnings)} tone={warningCount || cacheWarnings ? "warning" : "neutral"} labelFirst />
              <span className="separator"> · </span>
              <DetailMetric label="link" value={formatNumber(linkageGaps)} tone={linkageGaps ? "bad" : "neutral"} labelFirst />
              <span className="separator"> · </span>
              <DetailMetric label="recon" value={formatNumber(mismatchCount)} tone={mismatchCount ? "bad" : "neutral"} labelFirst />
            </span>
          }
          title="Core integrity is based on read/cache warnings, job_id linkage gaps, and reconciliation mismatches. Conditional field coverage is reported separately."
          intent={healthIntent(healthStatus)}
          accent="intent"
          className="diagnostics-kpi-core"
        />
        <HealthStripCard
          label="Job linkage"
          value={formatPercent(Number(kpis.job_linkage_rate ?? 0))}
          detail={
            <span>
              <DetailMetric label="matched" value={formatNumber(toNumber(kpis.matched_job_ids))} tone="good" />
              <span className="separator"> · </span>
              <DetailMetric label="orphan" value={formatNumber(toNumber(kpis.orphan_dataflow_job_ids))} tone={Number(kpis.orphan_dataflow_job_ids ?? 0) ? "bad" : "neutral"} />
            </span>
          }
          title="Matched job IDs divided by the union of job IDs found in job and dataflow logs."
          intent={Number(kpis.orphan_dataflow_job_ids ?? 0) ? "bad" : "good"}
          accent="intent"
        />
        <HealthStripCard
          label="Job-only IDs"
          value={formatNumber(toNumber(kpis.jobs_without_dataflow_records))}
          detail="job logs without child dataflow records"
          title="Job logs that have no dataflow_run_log records for the same job_id in the current filter."
          intent={Number(kpis.jobs_without_dataflow_records ?? 0) ? "warning" : "good"}
          accent="intent"
        />
        <HealthStripCard
          label="Reconciliation"
          value={formatNumber(mismatchCount)}
          detail={
            <span>
              <DetailMetric label="jobs" value={formatNumber(toNumber(kpis.affected_reconciliation_jobs))} tone={Number(kpis.affected_reconciliation_jobs ?? 0) ? "bad" : "neutral"} />
            </span>
          }
          title="Mismatch count between job totals and rollups from child dataflow records."
          intent={mismatchCount ? "bad" : "good"}
          accent="intent"
        />
        <HealthStripCard
          label="Evidence coverage"
          value={formatPercent(Number(kpis.field_readiness_rate ?? 0))}
          detail={
            <span>
              <DetailMetric label="issues" value={formatNumber(fieldIssues)} tone={fieldIssues ? "warning" : "neutral"} />
              <span className="separator"> · </span>
              <DetailMetric label="conditional" value={formatNumber(coverageSummary.conditional.length)} tone="neutral" />
            </span>
          }
          title="Coverage across required evidence fields used by Monitoring. Conditional watermark and maintenance groups are shown separately and do not reduce this rate."
          intent={fieldIssues ? "warning" : "good"}
          accent="intent"
        />
        <HealthStripCard
          label="Read/cache warnings"
          value={formatNumber(warningCount)}
          detail={
            <span>
              <DetailMetric label="sources" value={formatNumber(cacheWarnings)} tone={cacheWarnings ? "warning" : "neutral"} />
            </span>
          }
          title="Warnings emitted while reading or caching ETL log sources."
          intent={warningCount || cacheWarnings ? "warning" : "good"}
          accent="intent"
        />
      </section>

      <div className="monitoring-diagnostics-content report-layout-table-heavy-3">
        <section className="monitoring-diagnostics-primary-grid">
          <ReportPanel title="Record evidence trend" headerAction={<DiagnosticsRecordLegend />}>
            <ReportChart option={recordEvidenceTrendOption(recordEvidence)} height="100%" />
          </ReportPanel>
          <ReportPanel title="Job ID linkage health" subtitle="matched, orphan, and job-only IDs">
            <JobLinkageHealth rows={linkageSummary} />
          </ReportPanel>
        </section>

        <section className="monitoring-diagnostics-secondary-grid">
          <ReportPanel title="Reconciliation by metric" subtitle="job totals vs child rollups">
            <ReportChart option={reconciliationByMetricOption(reconciliationByMetric)} height="100%" wheelDataZoomStep={1} />
          </ReportPanel>
          <ReportPanel
            title="Evidence coverage"
            headerAction={<DiagnosticsCoverageHeader summary={coverageSummary} />}
          >
            <FieldCompleteness rows={coverageSummary.visible} />
          </ReportPanel>
          <ReportPanel title="Log source / cache coverage" subtitle="records, files, and source warnings">
            <SourceCoverageTable rows={sourceCoverage} timezoneName={timezoneName} />
          </ReportPanel>
        </section>

        <ReportPanel title="Diagnostics investigation queue" subtitle={`${formatNumber(investigationQueue.length)} evidence ${investigationQueue.length === 1 ? "item" : "items"}`}>
          <DiagnosticsQueueTable rows={investigationQueue} timezoneName={timezoneName} onInspect={onInspect} />
        </ReportPanel>
      </div>
    </div>
  );
}

function DiagnosticsRecordLegend() {
  return (
    <span className="monitoring-diagnostics-chart-legend" aria-label="Record evidence legend">
      <span><i className="is-job" />Job records</span>
      <span><i className="is-dataflow" />Dataflow records</span>
    </span>
  );
}

function DiagnosticsCoverageHeader({ summary }: { summary: ReturnType<typeof diagnosticsCoverageSummary> }) {
  return (
    <span className="monitoring-diagnostics-coverage-summary">
      <span className={summary.issues.length ? "is-warning" : "is-clear"}>{formatNumber(summary.issues.length)} {summary.issues.length === 1 ? "issue" : "issues"}</span>
      <span>{formatNumber(summary.ready.length)} ready</span>
      <span className="is-conditional">{formatNumber(summary.conditional.length)} conditional</span>
      {summary.unavailable.length ? <span className="is-unavailable">{formatNumber(summary.unavailable.length)} unavailable</span> : null}
    </span>
  );
}

function JobLinkageHealth({ rows }: { rows: DiagnosticsRow[] }) {
  const total = rows.reduce((sum, row) => sum + num(row, "count"), 0);
  if (!rows.length) return <div className="table-empty">No job linkage evidence</div>;
  return (
    <div className="diagnostics-linkage">
      <div className="diagnostics-linkage-bar" aria-hidden="true">
        {rows.map((row) => {
          const count = num(row, "count");
          const width = count > 0 && total ? Math.max(2, (count / total) * 100) : 0;
          return <span key={String(row.category)} className={`diagnostics-linkage-segment segment-${String(row.severity ?? "info")}`} style={{ width: `${width}%` }} />;
        })}
      </div>
      <DataTable
        rows={rows}
        columns={[
          { key: "label", label: "Linkage", sortable: true, minWidth: 140, fillPriority: "last" },
          { key: "count", label: "Count", sortable: true, autoFit: true, render: (row) => formatNumber(toNumber(row.count)) },
          { key: "share", label: "Share", sortable: true, autoFit: true, render: (row) => formatPercent(Number(row.share ?? 0)) },
          { key: "severity", label: "Status", sortable: true, autoFit: true, minWidth: 78, maxWidth: 104, render: (row) => <DiagnosticsLinkageStatus row={row} /> }
        ]}
        maxRows={8}
        className="diagnostics-compact-table monitoring-table-one-line"
      />
    </div>
  );
}

function DiagnosticsLinkageStatus({ row }: { row: DiagnosticsRow }) {
  const presentation = diagnosticsLinkagePresentation(row);
  return <span className={`diagnostics-severity diagnostics-${presentation.tone}`}>{presentation.label}</span>;
}

function FieldCompleteness({ rows }: { rows: DiagnosticsRow[] }) {
  if (!rows.length) return <div className="table-empty diagnostics-coverage-clear">No evidence groups available.</div>;
  return (
    <DataTable
      rows={rows}
      columns={[
        { key: "record_type", label: "Type", sortable: true, autoFit: true, render: (row) => humanLabel(row.record_type) },
        { key: "group", label: "Evidence", sortable: true, minWidth: 132, fillPriority: "last", render: (row) => <EvidenceGroupCell row={row} /> },
        { key: "applicability", label: "Scope", sortable: true, autoFit: true, minWidth: 82, maxWidth: 108, render: (row) => <EvidenceScope row={row} /> },
        { key: "completeness_rate", label: "Coverage", sortable: true, autoFit: true, render: (row) => <CompletenessValue row={row} /> },
        { key: "missing_values", label: "Missing", sortable: true, autoFit: true, render: (row) => formatNumber(toNumber(row.missing_values)) }
      ]}
      maxRows={12}
      className="diagnostics-compact-table monitoring-table-one-line"
    />
  );
}

function EvidenceGroupCell({ row }: { row: DiagnosticsRow }) {
  const fields = String(row.fields ?? "-");
  const present = formatNumber(toNumber(row.present_values));
  const expected = formatNumber(toNumber(row.records) * toNumber(row.required_fields));
  return (
    <span
      className="monitoring-ellipsis"
      title={`Fields: ${fields}\nPresent / expected values: ${present} / ${expected}\nMissing values: ${formatNumber(toNumber(row.missing_values))}`}
    >
      {humanLabel(row.group)}
    </span>
  );
}

function CompletenessValue({ row }: { row: DiagnosticsRow }) {
  if (row.applicability === "conditional") {
    return <span className="diagnostics-completeness-value completeness-conditional">{formatPercent(Number(row.completeness_rate ?? 0))}</span>;
  }
  const severity = String(row.severity ?? "info");
  return <span className={`diagnostics-completeness-value completeness-${severity}`}>{formatPercent(Number(row.completeness_rate ?? 0))}</span>;
}

function EvidenceScope({ row }: { row: DiagnosticsRow }) {
  const conditional = row.applicability === "conditional";
  return <span className={`diagnostics-scope-chip ${conditional ? "is-conditional" : "is-required"}`}>{conditional ? "Conditional" : "Required"}</span>;
}

function SourceCoverageTable({ rows, timezoneName }: { rows: DiagnosticsRow[]; timezoneName: string | null }) {
  return (
    <DataTable
      rows={rows}
      columns={[
        { key: "source", label: "Source", sortable: true, minWidth: 118, fillPriority: "last", render: (row) => <SourceCell row={row} /> },
        { key: "records", label: "Recs", sortable: true, autoFit: true, render: (row) => formatNumber(toNumber(row.records)) },
        { key: "latest_log_at", label: "Latest", sortable: true, width: 170, render: (row) => formatTime(row.latest_log_at, timezoneName) },
        { key: "warning_count", label: "Warn", sortable: true, autoFit: true, render: (row) => <WarningValue value={Number(row.warning_count ?? 0)} /> }
      ]}
      maxRows={10}
      timezoneName={timezoneName}
      className="diagnostics-compact-table diagnostics-source-coverage-table"
    />
  );
}

function DiagnosticsQueueTable({
  rows,
  timezoneName,
  onInspect
}: {
  rows: DiagnosticsRow[];
  timezoneName: string | null;
  onInspect?: (row: DiagnosticsRow) => void;
}) {
  return (
    <DataTable
      rows={rows}
      columns={[
        { key: "severity", label: "Severity", sortable: true, autoFit: true, minWidth: 72, maxWidth: 92, render: (row) => <DiagnosticsSeverityCell value={row.severity} /> },
        { key: "category", label: "Category", sortable: true, autoFit: true, minWidth: 132, maxWidth: 184, render: (row) => diagnosticsCategoryLabel(row.category) },
        { key: "issue", label: "Issue", sortable: true, minWidth: 360, fillPriority: "last", render: (row) => <span className="diagnostics-issue-cell" title={String(row.issue ?? "")}>{String(row.issue ?? "-")}</span> },
        { key: "target", label: "Target", sortable: true, minWidth: 210, maxWidth: 260, render: (row) => <span className="monitoring-ellipsis" title={String(row.target ?? "")}>{String(row.target ?? "-")}</span> },
        { key: "latest_time", label: "Latest", sortable: true, autoFit: true, minWidth: 156, maxWidth: 184, render: (row) => formatTime(row.latest_time, timezoneName) },
        { key: "action_hint", label: "Action", sortable: true, minWidth: 360, fillPriority: "last", render: (row) => <span className="diagnostics-issue-cell" title={String(row.action_hint ?? "")}>{String(row.action_hint ?? "-")}</span> }
      ]}
      maxRows={50}
      timezoneName={timezoneName}
      onRowClick={onInspect}
      className="diagnostics-queue-table monitoring-table-one-line"
    />
  );
}

function DiagnosticsSeverityCell({ value }: { value: unknown }) {
  const presentation = diagnosticsSeverityPresentation(value);
  return <span className={`diagnostics-severity diagnostics-${presentation.tone}`}>{presentation.label}</span>;
}

function recordEvidenceTrendOption(rows: DiagnosticsRow[]): EChartsOption {
  if (!rows.length) return emptyChartOption("No evidence records");
  const labels = rows.map((row) => String(row.bucket ?? row.date ?? "-"));
  return baseChartOption({
    grid: reportTightChartGrid({ top: 6, right: 6, left: 6 }),
    tooltip: {
      trigger: "axis",
      confine: true,
      formatter: (params) => {
        const items = Array.isArray(params) ? params : [params];
        const index = Number((items[0] as { dataIndex?: number } | undefined)?.dataIndex ?? 0);
        const row = rows[index] ?? {};
        return [
          `<strong>${labels[index]}</strong>`,
          `Job records: ${formatNumber(toNumber(row.job_records))}`,
          `Dataflow records: ${formatNumber(toNumber(row.dataflow_records))}`,
          `Matched job IDs: ${formatNumber(toNumber(row.matched_job_ids))}`,
          `Orphan dataflow IDs: ${formatNumber(toNumber(row.orphan_dataflow_job_ids))}`,
          `Job-only IDs: ${formatNumber(toNumber(row.jobs_without_dataflow_records))}`,
          `Linkage rate: ${formatPercent(Number(row.linkage_rate ?? 0))}`
        ].join("<br/>");
      }
    },
    legend: { show: false },
    xAxis: {
      type: "category",
      data: labels,
      axisLabel: { fontSize: 10, hideOverlap: true, margin: 2 },
      axisTick: { show: false }
    },
    yAxis: [
      {
        type: "value",
        axisLabel: { fontSize: 10, margin: 2 },
        splitLine: { lineStyle: { color: reportChartPalette.grid } }
      },
      {
        type: "value",
        axisLabel: { fontSize: 10, margin: 2 },
        splitLine: { show: false }
      }
    ],
    series: [
      {
        name: "Job records",
        type: "bar",
        yAxisIndex: 0,
        data: rows.map((row) => num(row, "job_records")),
        barMaxWidth: 18,
        itemStyle: { color: reportChartPalette.teal, borderRadius: [2, 2, 0, 0] }
      },
      {
        name: "Dataflow records",
        type: "line",
        yAxisIndex: 1,
        data: rows.map((row) => num(row, "dataflow_records")),
        connectNulls: true,
        showSymbol: false,
        showAllSymbol: false,
        symbol: "circle",
        symbolSize: 5,
        smooth: 0.2,
        clip: false,
        z: 6,
        lineStyle: { color: reportChartPalette.blue, width: 1.6 },
        itemStyle: { color: "#ffffff", borderColor: reportChartPalette.blue, borderWidth: 1.5 },
        emphasis: { focus: "series" }
      }
    ]
  });
}

function reconciliationByMetricOption(rows: DiagnosticsRow[]): EChartsOption {
  const chartRows = [...rows].sort((a, b) => num(b, "mismatch_count") - num(a, "mismatch_count"));
  if (!chartRows.length) return emptyChartOption("No reconciliation mismatches");
  const labels = chartRows.map((row) => humanLabel(row.metric));
  const zoomConfig = horizontalBarDataZoom(chartRows.length);
  const hasZoom = Boolean(zoomConfig);
  const barSizing = horizontalBarSeriesSizing(chartRows.length);
  const labelWidth = Math.min(132, Math.max(86, ...labels.map((label) => Math.min(132, label.length * 6 + 24))));
  return baseChartOption({
    animation: false,
    animationDurationUpdate: 0,
    grid: fixedHorizontalBarGrid(labelWidth, hasZoom, { top: 12 }),
    tooltip: {
      trigger: "item",
      triggerOn: "mousemove",
      confine: true,
      axisPointer: { type: "none" },
      formatter: (params) => {
        const item = params as { dataIndex?: number };
        const row = chartRows[Number(item.dataIndex ?? 0)] ?? {};
        return [
          `<strong>${humanLabel(row.metric)}</strong>`,
          `Mismatches: ${formatNumber(toNumber(row.mismatch_count))}`,
          `Affected jobs: ${formatNumber(toNumber(row.affected_jobs))}`,
          `Absolute difference: ${formatNumber(toNumber(row.absolute_difference))}`
        ].join("<br/>");
      }
    },
    xAxis: bottomAnchoredValueXAxis(),
    yAxis: fixedHorizontalCategoryAxis(labels, labelWidth),
    dataZoom: zoomConfig,
    series: [
      {
        name: "Mismatches",
        type: "bar",
        ...barSizing,
        data: chartRows.map((row) => num(row, "mismatch_count")),
        label: { show: true, position: "right", fontSize: 10, color: reportChartPalette.muted },
        itemStyle: { color: reportChartPalette.failed, borderRadius: [0, 3, 3, 0] }
      }
    ]
  });
}

function emptyChartOption(message: string): EChartsOption {
  return baseChartOption({
    title: {
      text: message,
      left: "center",
      top: "middle",
      textStyle: { fontSize: 11, color: reportChartPalette.muted, fontWeight: 500 }
    },
    xAxis: { show: false },
    yAxis: { show: false },
    series: []
  });
}

function SourceCell({ row }: { row: DiagnosticsRow }) {
  const source = String(row.source ?? "-");
  const fileKind = String(row.file_kind ?? "unknown");
  return (
    <span className="monitoring-stack-cell diagnostics-source-cell">
      <strong className="monitoring-ellipsis" title={source}>{diagnosticsSourceLabel(source)}</strong>
      <small>{fileKind} · {formatNumber(toNumber(row.file_count))} files</small>
    </span>
  );
}

function WarningValue({ value }: { value: number }) {
  return <span className={value ? "diagnostics-warning-value" : "monitor-muted"}>{formatNumber(value)}</span>;
}

function toNumber(value: unknown) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }
  return 0;
}

function formatTime(value: unknown, timezoneName: string | null) {
  if (value === null || value === undefined || value === "") return "-";
  return formatTimestampForDisplay(value, timezoneName);
}

function humanLabel(value: unknown) {
  const text = String(value ?? "unknown");
  if (!text || text === "null" || text === "undefined") return "Unknown";
  return text.replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function healthLabel(value: string) {
  return humanLabel(value);
}

function healthIntent(value: string): "neutral" | "bad" | "good" | "warning" {
  if (value === "healthy") return "good";
  if (value === "has_issues") return "bad";
  if (value === "warning") return "warning";
  return "neutral";
}
