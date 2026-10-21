import { Help } from "../../../shared/components/Help";
import type { EChartsOption } from "echarts";
import { useEffect, useState } from "react";
import type { MonitoringReport } from "../../../shared/api/domainTypes";
import type { MonitoringFilters } from "../monitoringFilters";
import type { EnvironmentSourcesData } from "../../sources/environmentSourcesResource";
import { checkLabel, syncLabel, validationLabel } from "../../sources/sourceStatusModel";
import { CompactNumberValue, DataTable, DetailMetric, HealthStripCard, ReportChart, ReportPanel, TablePager, baseChartOption, bottomAnchoredValueXAxis, fixedHorizontalBarGrid, fixedHorizontalCategoryAxis, formatCompact, formatNumber, formatPercent, formatTimestampForDisplay, horizontalBarDataZoom, horizontalBarSeriesSizing, metricValue, monitoringTimezone, num, reportChartPalette, type MetricValue } from "../components/monitoringPresentation";
import { DiagnosticsRegistry } from "./DiagnosticsRegistry";
import { reportTightChartGrid } from "./DiagnosticsPageSupport";
import {
  diagnosticsAssessment,
  diagnosticsCoverageSummary,
  diagnosticsLinkagePresentation,
  diagnosticsSourceLabel,
  firstMetric,
  numberValue,
} from "../diagnosticsPresentation";
import { chartMetric, chartTooltip, firstChartParam } from "../components/chartTooltip";

type DiagnosticsRow = Record<string, unknown>;
const DIAGNOSTICS_DEFAULT_PAGE_SIZE = 50;

export interface DiagnosticsSourcesState {
  data?: EnvironmentSourcesData | null;
  loading: boolean;
  error: string | null;
  onRetry?: () => void;
  onOpenSources?: () => void;
}

export function DiagnosticsPage({
  report,
  environmentId,
  filters,
  onInspect,
  sourcesState,
}: {
  report: MonitoringReport;
  environmentId: number;
  filters: MonitoringFilters;
  onInspect?: (row: DiagnosticsRow) => void;
  sourcesState?: DiagnosticsSourcesState;
}) {
  const diagnostics = (report.diagnostics ?? {}) as unknown as DiagnosticsRow;
  const kpis = (diagnostics.kpis ?? {}) as DiagnosticsRow;
  const timezoneName = monitoringTimezone(report);
  const recordEvidence = arrayRows(diagnostics.record_evidence_by_date);
  const linkageSummary = arrayRows(diagnostics.job_linkage_summary);
  const reconciliationByMetric = arrayRows(diagnostics.reconciliation_by_metric);
  const fieldCompleteness = arrayRows(diagnostics.field_completeness);
  const sourceCoverage = arrayRows(diagnostics.source_coverage);
  const sourceRows = sourcesState?.data ? sourceRowsFromSources(sourcesState.data, sourceCoverage) : sourceCoverage;
  const capture = asRecord(diagnostics.diagnostic_capture);
  const captureSummary = asRecord(capture?.summary);
  const assessment = asRecord(diagnostics.assessment);
  const reconciliationSummary = asRecord(asRecord(report.reconciliation)?.summary);
  const [sourceLimit, setSourceLimit] = useState(DIAGNOSTICS_DEFAULT_PAGE_SIZE);
  const [sourceOffset, setSourceOffset] = useState(0);
  useEffect(() => {
    setSourceOffset(0);
  }, [report]);
  const sourceLoadedRows = Math.min(sourceLimit, Math.max(0, sourceRows.length - sourceOffset));
  const coverageSummary = diagnosticsCoverageSummary(fieldCompleteness);
  const healthStatus = String(firstMetric(kpis, "health_status") ?? "no_evidence");
  const coreAssessment = diagnosticsAssessment(assessment?.status, healthStatus);
  const mismatchValue = firstMetric(reconciliationSummary, "mismatch_checks_count") ?? report.reconciliation.mismatch_count;
  const linkageRate = firstMetric(kpis, "job_linkage_rate");
  const matchedValue = firstMetric(kpis, "matched_job_ids");
  const orphanLinkageValue = firstMetric(kpis, "orphan_dataflow_job_ids");
  const jobOnlyLinkageValue = firstMetric(kpis, "jobs_without_dataflow_records");
  const expectedZeroValue = linkageValue(linkageSummary, "job_zero_children_expected");
  const unassessedLinkageValue = linkageValue(linkageSummary, "job_children_unassessed");
  const comparableValue = firstMetric(reconciliationSummary, "comparable_jobs_count");
  const unassessedReconValue = firstMetric(reconciliationSummary, "unassessed_jobs_count");
  const reconciliationScope = firstMetric(reconciliationSummary, "scope_coverage") ?? report.reconciliation.scope_coverage;
  const reconciliationNotAssessed = numberValue(comparableValue) === 0 && numberValue(mismatchValue) === 0;
  const fieldRate = firstMetric(kpis, "field_readiness_rate");
  const fieldIssuesValue = firstMetric(kpis, "field_readiness_issues");
  const availabilityRate = firstMetric(kpis, "field_value_availability_rate");
  const affectedCaptureValue = firstMetric(captureSummary, "affected_records") ?? firstMetric(kpis, "diagnostic_affected_records");
  const markerValue = firstMetric(captureSummary, "known_markers") ?? firstMetric(kpis, "diagnostic_known_markers");
  const omittedMarkerValue = firstMetric(captureSummary, "omitted_marker_records") ?? firstMetric(kpis, "diagnostic_omitted_marker_records");
  // Complete totals deliberately remain null when only a subset of jobs
  // reports loss counters. Keep the observed subtotal separate so a partial
  // snapshot is never presented as a complete report-wide total.
  const droppedRecords = firstMetric(kpis, "log_records_dropped");
  const droppedBytes = firstMetric(kpis, "log_bytes_dropped");
  const observedDroppedRecords = firstMetric(kpis, "log_records_dropped_observed_subtotal");
  const observedDroppedBytes = firstMetric(kpis, "log_bytes_dropped_observed_subtotal");
  const lossJobCount = firstMetric(kpis, "log_loss_job_count");
  const reportingRecordJobs = firstMetric(kpis, "log_records_dropped_reporting_jobs");
  const reportingByteJobs = firstMetric(kpis, "log_bytes_dropped_reporting_jobs");
  const unknownLossJobs = firstMetric(kpis, "log_loss_unknown_jobs");
  const lossCoverage = firstMetric(kpis, "log_loss_coverage");
  const hasDataflowRecords = numberValue(report.summary.dataflow_records) > 0;
  const captureValue = !hasDataflowRecords && numberValue(affectedCaptureValue) === 0 ? null : affectedCaptureValue;
  const lossScope = nullableNumber(lossJobCount);
  const lossUnknown = nullableNumber(unknownLossJobs);
  const lossCoverageNumber = nullableNumber(lossCoverage);
  const lossHasScope = lossScope !== null && lossScope > 0;
  const lossIsPartial = lossHasScope && (
    (lossUnknown !== null && lossUnknown > 0)
    || (lossCoverageNumber !== null && lossCoverageNumber < 100)
    || (lossCoverageNumber === null && (droppedRecords === null || droppedRecords === undefined) && (droppedBytes === null || droppedBytes === undefined))
  );
  const lossObservedValue = observedDroppedRecords;
  const lossValue = !hasDataflowRecords && (lossScope === null || lossScope === 0) && !hasKnownPositiveLoss(droppedRecords, droppedBytes, observedDroppedRecords, observedDroppedBytes)
    ? null
    : (lossIsPartial ? lossObservedValue : droppedRecords);
  const lossHasPositiveValue = hasKnownPositiveLoss(droppedRecords, droppedBytes, observedDroppedRecords, observedDroppedBytes);
  const lossIntent = lossHasPositiveValue ? "warning" : lossIsPartial || !lossHasScope ? "neutral" : "good";
  const lossValueIsObserved = Boolean(lossIsPartial);
  const displayLossCoverage = lossHasScope ? lossCoverage : null;
  const coreReasons = assessment?.reasons;
  const coreDetail = Array.isArray(coreReasons) && coreReasons.length ? coreReasons.map(reasonLabel).join("\n") : null;
  const linkageGaps = sumMetrics(orphanLinkageValue, jobOnlyLinkageValue);
  const linkagePopulation = sumMetrics(matchedValue, orphanLinkageValue, jobOnlyLinkageValue, expectedZeroValue, unassessedLinkageValue);

  return (
    <div className="monitoring-page monitoring-diagnostics-report">
      <section className={`overview-health-strip monitoring-diagnostics-health-strip health-${coreAssessment.tone}`}>
        <HealthStripCard
          label="Core integrity"
          value={coreAssessment.label}
          detail={
            <span>
              <DetailMetric label="link gaps" value={<CompactNumberValue value={linkageGaps} />} tone={positiveIntent(linkageGaps, "bad")} labelFirst />
              <span className="separator"> · </span>
              <DetailMetric label="mismatched checks" value={<CompactNumberValue value={mismatchValue as MetricValue} />} tone={positiveIntent(mismatchValue as MetricValue, "bad")} labelFirst />
              <span className="separator"> · </span>
              <DetailMetric label="loss" value={<CompactNumberValue value={lossValue as MetricValue} fallback="Unknown" />} tone={lossIntent} labelFirst />
            </span>
          }
          title={coreDetail ? `Core integrity:\n${coreDetail}\nCoverage: ${String(assessment?.coverage ?? "Unknown")}` : "Core integrity combines confirmed linkage, comparable reconciliation, and recorded log loss. Partial or unavailable evidence remains neutral."}
          intent={coreAssessment.tone === "info" ? "neutral" : coreAssessment.tone}
          accent="intent"
          className="diagnostics-kpi-core"
        />
        <HealthStripCard
          label="Job linkage"
          value={linkagePopulation === null || numberValue(linkagePopulation) === 0
            ? "No evidence"
            : numberValue(linkageGaps) > 0
              ? <span><CompactNumberValue value={linkageGaps} /> job gap{numberValue(linkageGaps) === 1 ? "" : "s"}</span>
              : "No gaps"}
          detail={
            <span>
              <DetailMetric label="matched jobs" value={<CompactNumberValue value={matchedValue as MetricValue} />} tone="good" />
              <span className="separator"> · </span>
              <DetailMetric label="missing job log" value={<CompactNumberValue value={orphanLinkageValue as MetricValue} />} tone={positiveIntent(orphanLinkageValue, "bad")} />
              <span className="separator"> · </span>
              <DetailMetric label="expected zero" value={<CompactNumberValue value={expectedZeroValue as MetricValue} />} tone="neutral" />
              <span className="separator"> · </span>
              <DetailMetric label="unassessed" value={<CompactNumberValue value={unassessedLinkageValue as MetricValue} />} tone="neutral" />
              {linkagePopulation !== null && numberValue(linkagePopulation) > 0 ? <><span className="separator"> · </span><DetailMetric label="matched share" value={formatPercent(linkageRate as MetricValue)} tone="neutral" /></> : null}
            </span>
          }
          title="Linkage gaps are jobs whose exact source, session and job identity has no matching record, or terminal jobs whose expected children are missing. The matched share is descriptive, not an integrity threshold. Expected-zero and unassessed jobs are shown separately."
          intent={linkagePopulation === null || numberValue(linkagePopulation) === 0 ? "neutral" : numberValue(linkageGaps) > 0 ? "bad" : numberValue(unassessedLinkageValue) > 0 ? "neutral" : "good"}
          accent="intent"
        />
        <HealthStripCard
          label="Reconciliation"
          value={<CompactNumberValue value={(reconciliationNotAssessed ? null : mismatchValue) as MetricValue} fallback="Not assessed" />}
          detail={
            <span>
              <DetailMetric label="mismatched checks" value={<CompactNumberValue value={mismatchValue as MetricValue} />} tone={positiveIntent(mismatchValue as MetricValue, "bad")} />
              <span className="separator"> · </span>
              <DetailMetric label="comparable jobs" value={<CompactNumberValue value={comparableValue as MetricValue} />} tone="neutral" />
              <span className="separator"> · </span>
              <DetailMetric label="unassessed jobs" value={<CompactNumberValue value={unassessedReconValue as MetricValue} />} tone="neutral" />
            </span>
          }
          title="The headline counts mismatched checks (four per job) among comparable terminal jobs; jobs and checks are distinct units. A filtered child scope is a scope notice, not a failure. Zero mismatches with no comparable jobs is shown as not assessed."
          intent={reconciliationIntent(mismatchValue, comparableValue, unassessedReconValue, reconciliationScope)}
          accent="intent"
        />
        <HealthStripCard
          label="Field coverage"
          value={formatPercent(fieldRate as MetricValue)}
          detail={
            <span>
              <DetailMetric label="groups below threshold" value={<CompactNumberValue value={fieldIssuesValue as MetricValue} />} tone={positiveIntent(fieldIssuesValue, "warning")} />
              <span className="separator"> · </span>
              <DetailMetric label="assessed groups" value={<CompactNumberValue value={coverageSummary.ready.length + coverageSummary.issues.length} />} tone="neutral" />
              {availabilityRate !== undefined ? <><span className="separator"> · </span><DetailMetric label="available" value={formatPercent(availabilityRate as MetricValue)} tone="neutral" /></> : null}
            </span>
          }
          title="Presence is measured across applicable schema-field slots. Value availability is shown separately when the admitted evidence can establish it; null does not automatically mean invalid."
          intent={fieldCoverageIntent(fieldRate, fieldIssuesValue)}
          accent="intent"
        />
        <HealthStripCard
          label="Captured detail issues"
          value={<CompactNumberValue value={captureValue as MetricValue} fallback="Not assessed" />}
          detail={
            <span>
              <DetailMetric label="markers" value={<CompactNumberValue value={markerValue as MetricValue} />} tone="neutral" />
              <span className="separator"> · </span>
              <DetailMetric label="omitted" value={<CompactNumberValue value={omittedMarkerValue as MetricValue} />} tone={positiveIntent(omittedMarkerValue as MetricValue, "warning")} />
            </span>
          }
          title="Dataflow records with captured diagnostic detail are counted separately from diagnostic field/reason markers. These values describe the current filter and may be unknown when the source does not expose counters."
          intent={captureValue === null ? "neutral" : positiveIntent(captureValue as MetricValue, "warning", "good")}
          accent="intent"
        />
        <HealthStripCard
          label="Log loss"
          value={
            lossValue === null
              ? <CompactNumberValue value={null} fallback="Not assessed" />
              : <span>{lossValueIsObserved ? <span className="diagnostics-loss-observed-label">Observed </span> : null}<CompactNumberValue value={lossValue as MetricValue} fallback="Unknown" /></span>
          }
          detail={
            <span>
              <DetailMetric label={lossValueIsObserved ? "Observed bytes" : "bytes"} value={<CompactNumberValue value={(lossIsPartial ? observedDroppedBytes : droppedBytes) as MetricValue} fallback="Unknown" />} tone={lossIntent} />
              <span className="separator"> · </span>
              <DetailMetric
                label="reported jobs"
                value={
                  <Help as="span" title={`Record counters: ${formatNumber(reportingRecordJobs as MetricValue)} / ${formatNumber(lossJobCount as MetricValue)} jobs\nByte counters: ${formatNumber(reportingByteJobs as MetricValue)} / ${formatNumber(lossJobCount as MetricValue)} jobs`}>
                    <CompactNumberValue value={reportingRecordJobs as MetricValue} fallback="Unknown" />
                    <span className="separator"> / </span>
                    <CompactNumberValue value={lossJobCount as MetricValue} fallback="Unknown" />
                  </Help>
                }
                tone="neutral"
              />
              <span className="separator"> · </span>
              <DetailMetric label="coverage" value={displayLossCoverage === null || displayLossCoverage === undefined ? "Unknown" : formatPercent(displayLossCoverage as MetricValue)} tone="neutral" />
            </span>
          }
          title="Complete totals are shown when every evaluated job reports loss counters. Observed values are subtotals from reporting jobs when coverage is partial; reported jobs and coverage show how much of the scope is measured. Partial coverage stays neutral unless a positive loss is observed."
          intent={lossHasPositiveValue ? "warning" : lossValue === null ? "neutral" : lossIntent}
          accent="intent"
        />
      </section>

      <div className="monitoring-diagnostics-content monitoring-equal-rows-3">
        <section className="monitoring-diagnostics-primary-grid">
          <ReportPanel title="Log records trend" headerAction={<DiagnosticsRecordLegend />}>
            <ReportChart option={recordEvidenceTrendOption(recordEvidence)} height="100%" />
          </ReportPanel>
          <ReportPanel title="Job log linkage" headerAction={<DiagnosticsLinkageLegend />}>
            <JobLinkageHealth rows={linkageSummary} />
          </ReportPanel>
        </section>

        <section className="monitoring-diagnostics-secondary-grid">
          <ReportPanel title="Reconciliation by metric" subtitle="comparable, mismatch, and unassessed checks">
            <ReportChart option={reconciliationByMetricOption(reconciliationByMetric)} height="100%" wheelDataZoomStep={1} />
          </ReportPanel>
          <ReportPanel
            title="Field coverage"
            headerAction={<DiagnosticsCoverageHeader summary={coverageSummary} />}
          >
            <FieldCompleteness rows={coverageSummary.visible} />
          </ReportPanel>
        </section>

        <section className="monitoring-diagnostics-tertiary-grid">
          <DiagnosticsRegistry report={report} environmentId={environmentId} filters={filters} timezoneName={timezoneName} onInspect={onInspect} />
          <ReportPanel
            title="Log sources"
            subtitle={sourcesState ? "configured sources and current readiness" : "filtered records and latest log"}
            className={sourceRows.length > sourceLimit ? "monitoring-diagnostics-source-coverage-paged" : undefined}
            headerAction={sourceRows.length > sourceLimit ? (
              <TablePager
                limit={sourceLimit}
                offset={sourceOffset}
                loadedRows={sourceLoadedRows}
                totalRows={sourceRows.length}
                loading={false}
                onPageChange={setSourceOffset}
                onPageSizeChange={(nextLimit) => {
                  setSourceLimit(nextLimit);
                  setSourceOffset(0);
                }}
              />
            ) : undefined}
          >
            <SourceCoverageTable rows={sourceRows} maxRows={sourceLimit} offset={sourceOffset} timezoneName={timezoneName} sourcesState={sourcesState} />
          </ReportPanel>
        </section>
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

function DiagnosticsLinkageLegend() {
  return (
    <span className="monitoring-diagnostics-chart-legend" aria-label="Job linkage legend">
      <Help as="span" title="Job has both its job log and its child dataflow records"><i className="is-linkage-good" />Matched</Help>
      <Help as="span" title="Dataflow records without a job log, or a terminal job whose expected children are missing"><i className="is-linkage-bad" />Missing</Help>
      <Help as="span" title="Job expects no children, or the evidence is not sufficient to assess them"><i className="is-linkage-info" />Zero / unassessed</Help>
    </span>
  );
}

function DiagnosticsCoverageHeader({ summary }: { summary: ReturnType<typeof diagnosticsCoverageSummary> }) {
  return (
    <span className="monitoring-diagnostics-coverage-summary">
      <span className={summary.issues.length ? "is-warning" : "is-clear"}><CompactNumberValue value={summary.issues.length} /> {summary.issues.length === 1 ? "issue" : "issues"}</span>
      <span><CompactNumberValue value={summary.ready.length} /> ready</span>
      <span className="is-conditional"><CompactNumberValue value={summary.conditional.length} /> conditional</span>
      <span className="is-optional"><CompactNumberValue value={summary.optional.length} /> optional</span>
      {summary.notApplicable.length ? <span className="is-unavailable"><CompactNumberValue value={summary.notApplicable.length} /> n/a</span> : null}
      {summary.unavailable.length ? <span className="is-unavailable"><CompactNumberValue value={summary.unavailable.length} /> unavailable</span> : null}
    </span>
  );
}

function JobLinkageHealth({ rows }: { rows: DiagnosticsRow[] }) {
  const total = rows.reduce((sum, row) => sum + num(row, "count"), 0);
  if (!rows.length || total === 0) return <div className="table-empty">No job linkage evidence</div>;
  return (
    <div className="diagnostics-linkage">
      <div className="diagnostics-linkage-bar" role="img" aria-label={rows.filter((row) => num(row, "count") > 0).map((row) => `${String(row.label)}: ${formatNumber(metricValue(row, "count"))}`).join(", ")}>
        {rows.map((row) => {
          const count = num(row, "count");
          const width = count > 0 && total ? (count / total) * 100 : 0;
          return <Help as="span" key={String(row.category)} title={`${String(row.label)}: ${formatNumber(metricValue(row, "count"))} · ${formatPercent(metricValue(row, "share"))}`} className={`diagnostics-linkage-segment segment-${String(row.severity ?? "info")}`} style={{ width: `${width}%`, minWidth: count > 0 ? 3 : 0 }} />
        })}
      </div>
      <DataTable
        rows={rows}
        compactNumbers
        compactNumberStyle="infix"
        columns={[
          { key: "label", label: "Linkage", sortable: true, minWidth: 120, fillPriority: "last" },
          { key: "count", label: "Count", sortable: true, autoFit: true, render: (row) => <CompactNumberValue value={metricValue(row, "count")} />, measureValue: (row) => formatCompact(metricValue(row, "count")) },
          { key: "share", label: "Share", sortable: true, autoFit: true, render: (row) => formatPercent(metricValue(row, "share")), measureValue: (row) => formatPercent(metricValue(row, "share")) },
          { key: "severity", label: "Status", sortable: true, autoFit: true, render: (row) => <DiagnosticsLinkageStatus row={row} />, measureValue: (row) => diagnosticsLinkagePresentation(row).label, measureSelectors: [".diagnostics-severity"] }
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
      compactNumbers
      compactNumberStyle="infix"
      columns={[
        { key: "record_type", label: "Type", sortable: true, autoFit: true, render: (row) => humanLabel(row.record_type), measureValue: (row) => humanLabel(row.record_type) },
        { key: "group", label: "Evidence", sortable: true, autoFit: true, fillPriority: "last", render: (row) => <EvidenceGroupCell row={row} />, measureValue: (row) => evidenceGroupLabel(row) },
        { key: "applicability", label: "Scope", sortable: true, autoFit: true, render: (row) => <EvidenceScope row={row} />, measureValue: (row) => evidenceScopeLabel(row), measureSelectors: [".diagnostics-scope-chip"] },
        { key: "status", label: "Status", sortable: true, autoFit: true, render: (row) => <EvidenceStatus row={row} />, measureValue: (row) => evidenceStatusPresentation(row).label, measureSelectors: [".diagnostics-severity"] },
        { key: "presence_rate", label: "Presence", sortable: true, autoFit: true, render: (row) => <CompletenessValue row={row} />, measureValue: (row) => formatPercent(firstMetric(row, "presence_rate", "completeness_rate") as MetricValue) },
        { key: "value_availability_rate", label: "Available", sortable: true, autoFit: true, render: (row) => <AvailabilityValue row={row} />, measureValue: (row) => availabilityText(row) },
        { key: "missing_values", label: "Missing", sortable: true, autoFit: true, render: (row) => <CompactNumberValue value={firstMetric(row, "missing_fields", "missing_values") as MetricValue} />, measureValue: (row) => formatCompact(firstMetric(row, "missing_fields", "missing_values") as MetricValue) }
      ]}
      maxRows={12}
      className="diagnostics-compact-table monitoring-table-one-line"
    />
  );
}

function EvidenceGroupCell({ row }: { row: DiagnosticsRow }) {
  const fields = String(row.fields ?? "-");
  const present = formatNumber(firstMetric(row, "present_fields", "present_values") as MetricValue);
  const expected = formatNumber(firstMetric(row, "applicable_fields", "required_fields") as MetricValue ?? toNumber(row.records) * toNumber(row.required_fields));
  const available = formatNumber(firstMetric(row, "available_values", "available_fields") as MetricValue);
  return (
    <Help as="span"
      className="monitoring-ellipsis"
      title={`Fields: ${fields}\nPresent / applicable fields: ${present} / ${expected}\nAvailable values: ${available}\nMissing fields: ${formatNumber(firstMetric(row, "missing_fields", "missing_values") as MetricValue)}`}
    >
      {evidenceGroupLabel(row)}
    </Help>
  );
}

function availabilityText(row: DiagnosticsRow) {
  const value = firstMetric(row, "value_availability_rate", "availability_rate") as MetricValue;
  return value === null || value === undefined ? "Unknown" : formatPercent(value);
}

function CompletenessValue({ row }: { row: DiagnosticsRow }) {
  if (row.applicability === "conditional") return <span className="diagnostics-completeness-value completeness-conditional">{formatPercent(firstMetric(row, "presence_rate", "completeness_rate") as MetricValue)}</span>;
  const severity = String(row.severity ?? row.status ?? "info");
  return <span className={`diagnostics-completeness-value completeness-${severity}`}>{formatPercent(metricValue(row, "completeness_rate"))}</span>;
}

function AvailabilityValue({ row }: { row: DiagnosticsRow }) {
  const value = firstMetric(row, "value_availability_rate", "availability_rate") as MetricValue;
  return value === null || value === undefined ? <span className="monitor-muted">Unknown</span> : <span className="diagnostics-completeness-value completeness-info">{formatPercent(value)}</span>;
}

function evidenceGroupLabel(row: DiagnosticsRow) {
  return humanLabel(String(row.group ?? "").replace(/\s+evidence$/i, ""));
}

function evidenceScopeLabel(row: DiagnosticsRow) {
  const applicability = String(row.applicability ?? "required").toLowerCase();
  if (applicability === "not_applicable" || applicability === "not applicable") return "N/A";
  return applicability === "optional" ? "Optional" : applicability === "conditional" ? "Conditional" : "Required";
}

function EvidenceScope({ row }: { row: DiagnosticsRow }) {
  const applicability = String(row.applicability ?? "required").toLowerCase();
  const conditional = applicability === "conditional";
  const optional = applicability === "optional";
  const notApplicable = applicability === "not_applicable" || applicability === "not applicable";
  return <span className={`diagnostics-scope-chip ${conditional ? "is-conditional" : optional ? "is-optional" : notApplicable ? "is-unavailable" : "is-required"}`}>{evidenceScopeLabel(row)}</span>;
}

function evidenceStatusPresentation(row: DiagnosticsRow): { label: string; tone: string } {
  if (row.applicability === "not_applicable") return { label: "Not applicable", tone: "info" };
  if (row.actionable === false || String(row.severity ?? row.status ?? "info").toLowerCase() === "info") return { label: "Not assessed", tone: "info" };
  return diagnosticsAssessment(row.severity ?? row.status, "not_assessed");
}

function EvidenceStatus({ row }: { row: DiagnosticsRow }) {
  const presentation = evidenceStatusPresentation(row);
  return <span className={`diagnostics-severity diagnostics-${presentation.tone}`}>{presentation.label}</span>;
}

function SourceCoverageTable({ rows, maxRows, offset, timezoneName, sourcesState }: { rows: DiagnosticsRow[]; maxRows: number; offset: number; timezoneName: string | null; sourcesState?: DiagnosticsSourcesState }) {
  if (sourcesState?.loading && !rows.length) return <div className="table-empty" role="status">Loading log source status…</div>;
  if (sourcesState?.error && !rows.length) {
    return <div className="diagnostics-source-state" role="status"><span>Log source status unavailable: {sourcesState.error}</span>{sourcesState.onRetry ? <button type="button" className="text-action" onClick={sourcesState.onRetry}>Retry</button> : null}</div>;
  }
  if (!rows.length) return <div className="table-empty">No configured log sources</div>;
  return (
    <>
      {sourcesState?.error ? <div className="diagnostics-source-state" role="status"><span>Source readiness unavailable: {sourcesState.error}</span>{sourcesState.onRetry ? <button type="button" className="text-action" onClick={sourcesState.onRetry}>Retry</button> : null}</div> : null}
      <DataTable
        rows={rows}
        compactNumbers
        compactNumberStyle="infix"
        columns={[
        { key: "source", label: "Source", sortable: true, minWidth: 132, fillPriority: "last", render: (row) => <SourceCell row={row} timezoneName={timezoneName} /> },
        { key: "records", label: "Records", sortable: true, autoFit: true, render: (row) => <SourceRecordsValue row={row} /> },
        { key: "readiness", label: "Readiness", sortable: true, autoFit: true, render: (row) => <SourceReadinessValue row={row} timezoneName={timezoneName} />, measureValue: (row) => [sourceReadinessLabelText(row), formatTime(firstMetric(row, "latest_observed_at", "checked_at"), timezoneName)] }
        ]}
        maxRows={maxRows}
        offset={offset}
        timezoneName={timezoneName}
        className="diagnostics-compact-table diagnostics-source-coverage-table"
      />
    </>
  );
}

function recordEvidenceTrendOption(rows: DiagnosticsRow[]): EChartsOption {
  if (!rows.length) return emptyChartOption("No evidence records");
  const labels = rows.map((row) => String(row.bucket ?? row.date ?? "-"));
  return baseChartOption({
    grid: reportTightChartGrid({ top: 6, right: 6, left: 6 }),
    tooltip: {
      trigger: "axis",
      formatter: chartTooltip((params) => {
        const items = Array.isArray(params) ? params : [params];
        const index = Number((items[0] as { dataIndex?: number } | undefined)?.dataIndex ?? 0);
        const row = rows[index] ?? {};
        return {
          title: labels[index],
          metrics: [
            chartMetric("Job records", formatNumber(metricValue(row, "job_records"))),
            chartMetric("Dataflow records", formatNumber(metricValue(row, "dataflow_records"))),
            chartMetric("Matched job IDs", formatNumber(metricValue(row, "matched_job_ids")), "success"),
            chartMetric("Orphan dataflow IDs", formatNumber(metricValue(row, "orphan_dataflow_job_ids")), "failed"),
            chartMetric("Job-only IDs", formatNumber(metricValue(row, "jobs_without_dataflow_records"))),
            chartMetric("Linkage rate", formatPercent(metricValue(row, "linkage_rate"))),
          ]
        };
      })
    },
    legend: { show: false },
    xAxis: {
      type: "category",
      data: labels,
      axisLabel: { fontSize: 10, hideOverlap: true, margin: 2 },
      axisTick: { show: false }
    },
    yAxis: {
      type: "value",
      axisLabel: { fontSize: 10, margin: 2, formatter: (value: number) => formatCompact(value) },
      splitLine: { lineStyle: { color: reportChartPalette.grid } }
    },
    series: [
      {
        name: "Job records",
        type: "bar",
        data: rows.map((row) => num(row, "job_records")),
        barMaxWidth: 18,
        itemStyle: { color: reportChartPalette.teal, borderRadius: [2, 2, 0, 0] }
      },
      {
        name: "Dataflow records",
        type: "line",
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
  const chartRows = [...rows].sort((a, b) => reconciliationTotal(b) - reconciliationTotal(a));
  if (!chartRows.length) return emptyChartOption("No reconciliation evidence");
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
      axisPointer: { type: "none" },
      formatter: chartTooltip((params) => {
        const row = chartRows[Number(firstChartParam(params)?.dataIndex ?? 0)] ?? {};
        const parts = reconciliationParts(row);
        return {
          title: humanLabel(row.metric),
          metrics: [
            chartMetric("Total checks", formatNumber(firstMetric(row, "total_checks_count") as MetricValue)),
            chartMetric("Comparable", formatNumber(firstMetric(row, "comparable_checks_count") as MetricValue)),
            chartMetric("Affected jobs", formatNumber(firstMetric(row, "affected_jobs") as MetricValue)),
            chartMetric("Matched", formatNumber(parts.matched as MetricValue), "success"),
            chartMetric("Mismatches", formatNumber(parts.mismatch as MetricValue), "failed"),
            chartMetric("Not assessed", formatNumber(parts.unassessed as MetricValue)),
            chartMetric("Absolute difference", formatNumber(firstMetric(row, "absolute_difference") as MetricValue)),
          ]
        };
      })
    },
    xAxis: bottomAnchoredValueXAxis(),
    yAxis: fixedHorizontalCategoryAxis(labels, labelWidth),
    dataZoom: zoomConfig,
    series: [
      {
        name: "Matched",
        type: "bar",
        stack: "checks",
        ...barSizing,
        data: chartRows.map((row) => reconciliationParts(row).matched),
        itemStyle: { color: reportChartPalette.teal }
      },
      {
        name: "Mismatches",
        type: "bar",
        stack: "checks",
        ...barSizing,
        data: chartRows.map((row) => reconciliationParts(row).mismatch),
        itemStyle: { color: reportChartPalette.failed }
      },
      {
        name: "Not assessed",
        type: "bar",
        stack: "checks",
        ...barSizing,
        data: chartRows.map((row) => reconciliationParts(row).unassessed),
        itemStyle: { color: reportChartPalette.grid }
      }
    ]
  });
}

function reconciliationParts(row: DiagnosticsRow) {
  const mismatch = numberValue(firstMetric(row, "mismatch_count"));
  const comparable = numberValue(firstMetric(row, "comparable_checks_count"));
  const matched = Math.max(0, comparable - mismatch);
  const unassessed = numberValue(firstMetric(row, "unassessed_checks_count"));
  return { matched, mismatch, unassessed };
}

function reconciliationTotal(row: DiagnosticsRow) {
  const parts = reconciliationParts(row);
  return parts.matched + parts.mismatch + parts.unassessed;
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

function SourceCell({ row, timezoneName }: { row: DiagnosticsRow; timezoneName: string | null }) {
  const source = String(row.source ?? "-");
  const fileKind = String(row.file_kind ?? row.kind ?? "log");
  const configured = row.configured === false ? "disabled" : "enabled";
  const latest = firstMetric(row, "latest_log_at", "latest_observed_at");
  const latestText = latest === null || latest === undefined || latest === "" ? "no log" : formatTimestampForDisplay(latest, timezoneName);
  return (
    <span className="monitoring-stack-cell diagnostics-source-cell">
      <Help as="strong" className="monitoring-ellipsis" title={source}>{diagnosticsSourceLabel(source)}</Help>
      <Help as="small" className="monitoring-ellipsis" title={`Files: ${formatNumber(metricValue(row, "file_count"))}\nLatest log: ${latestText}`}>{fileKind} · {configured} · {latestText}</Help>
    </span>
  );
}

function SourceRecordsValue({ row }: { row: DiagnosticsRow }) {
  const jobRecords = firstMetric(row, "job_records");
  const dataflowRecords = firstMetric(row, "dataflow_records");
  if ((jobRecords === undefined || jobRecords === null) && (dataflowRecords === undefined || dataflowRecords === null)) return <CompactNumberValue value={metricValue(row, "records")} />;
  return (
    <span className="monitoring-stack-cell diagnostics-source-records-cell">
      <strong>Jobs <CompactNumberValue value={jobRecords as MetricValue} /></strong>
      <small>Dataflows <CompactNumberValue value={dataflowRecords as MetricValue} /></small>
    </span>
  );
}

function sourceReadinessLabelText(row: DiagnosticsRow) {
  const label = sourceReadinessLabel(String(firstMetric(row, "readiness") ?? "not_assessed"));
  return typeof row.readiness_display === "string" && row.readiness_display.trim() ? row.readiness_display : label;
}

function SourceReadinessValue({ row, timezoneName }: { row: DiagnosticsRow; timezoneName: string | null }) {
  const value = String(firstMetric(row, "readiness") ?? "not_assessed");
  const toneValue = String(firstMetric(row, "readiness_tone") ?? value);
  const tone = toneValue === "bad" ? "bad" : toneValue === "warning" ? "warning" : toneValue === "good" ? "good" : "info";
  const displayLabel = sourceReadinessLabelText(row);
  const checkedAt = firstMetric(row, "latest_observed_at", "checked_at");
  return (
    <Help as="span" className="monitoring-stack-cell diagnostics-source-readiness-cell" title={String(row.readiness_detail ?? "")}>
      <strong className={`diagnostics-severity diagnostics-${tone}`}>{displayLabel}</strong>
      <small>{formatTime(checkedAt, timezoneName)}</small>
    </Help>
  );
}

function sourceRowsFromSources(data: EnvironmentSourcesData, filteredRows: DiagnosticsRow[]): DiagnosticsRow[] {
  const configuredSources = data.logBasePaths;
  const filteredById = new Map(filteredRows.map((row) => [String(firstMetric(row, "source_id", "_source_id") ?? ""), row]));
  const configuredRows = configuredSources.map((source) => {
    const sourceRecord = source as unknown as DiagnosticsRow;
    const id = firstMetric(sourceRecord, "id", "source_id");
    const filtered = filteredById.get(String(id ?? "")) ?? {};
    const key = `logs:${String(id ?? "")}`;
    const status = (data.statuses as Record<string, unknown>)[key] as DiagnosticsRow | undefined;
    const validation = asRecord(status?.validation);
    const observation = asRecord(status?.observation);
    const execution = asRecord(status?.sync_execution);
    const readiness = sourceReadiness(status ?? null);
    return {
      ...filtered,
      source: sourceDisplayName(sourceRecord, id),
      source_id: id,
      file_kind: "logs",
      configured: sourceRecord.enabled !== false,
      records: firstMetric(filtered, "records") ?? null,
      file_count: firstMetric(filtered, "file_count") ?? null,
      latest_log_at: firstMetric(filtered, "latest_log_at") ?? null,
      job_records: firstMetric(filtered, "job_records") ?? null,
      dataflow_records: firstMetric(filtered, "dataflow_records") ?? null,
      readiness: readiness.label,
      readiness_display: readiness.display,
      readiness_tone: readiness.tone,
      readiness_detail: readiness.detail,
      latest_observed_at: firstMetric(observation, "checked_at") ?? firstMetric(validation, "completed_at") ?? firstMetric(execution, "completed_at"),
      status,
    };
  });
  const configuredIds = new Set(configuredRows.map((row) => String(firstMetric(row, "source_id") ?? "")));
  return [...configuredRows, ...filteredRows.filter((row) => !configuredIds.has(String(firstMetric(row, "source_id", "_source_id") ?? "")))];
}

function sourceReadiness(status: DiagnosticsRow | null) {
  if (!status) return { label: "not_assessed", display: "Not assessed", tone: "info", detail: "No source status observation" };
  const validation = asRecord(status.validation);
  const observation = asRecord(status.observation);
  const execution = asRecord(status.sync_execution);
  const freshness = asRecord(status.freshness);
  const validationState = String(validation?.state ?? "not_validated");
  const observationState = String(observation?.state ?? "never");
  const executionState = String(execution?.state ?? "never");
  const freshnessState = String(freshness?.state ?? "");
  const detail = [
    `Validation: ${validationLabel(validationState)}`,
    `Check: ${checkLabel(observationState)}`,
    `Sync: ${syncLabel(executionState)}`,
  ].join(" · ");
  if (["invalid", "error"].includes(validationState) || ["error"].includes(observationState) || executionState === "failed" || ["missing", "sync_failed"].includes(freshnessState) || status.status === "error") return { label: "issue", display: "Issue", tone: "bad", detail };
  if (freshnessState === "not_synced") return { label: "not_synced", display: "Not synced", tone: "warning", detail };
  if (validationState === "warning") return { label: "validation_warning", display: validationLabel(validationState), tone: "warning", detail };
  if (validationState === "validating") return { label: "validating", display: validationLabel(validationState), tone: "warning", detail };
  if (observationState === "changed") return { label: "changes_found", display: checkLabel(observationState), tone: "warning", detail };
  if (observationState === "paused") return { label: "paused", display: checkLabel(observationState), tone: "warning", detail };
  if (observationState === "checking") return { label: "checking", display: checkLabel(observationState), tone: "warning", detail };
  if (executionState === "running") return { label: "syncing", display: syncLabel(executionState), tone: "warning", detail };
  if (validationState === "ready" || observationState === "unchanged" || executionState === "succeeded" || freshnessState === "ok") return { label: "ready", display: "Ready", tone: "good", detail };
  return { label: "not_assessed", display: "Not assessed", tone: "info", detail };
}

function sourceDisplayName(source: DiagnosticsRow, id: unknown) {
  const candidate = [source.label, source.name, source.uri, source.path].find((value): value is string => typeof value === "string" && value.trim().length > 0);
  return candidate?.trim() || `source:${String(id ?? "unknown")}`;
}

function sourceReadinessLabel(value: unknown) {
  const normalized = String(value ?? "not_assessed").trim().toLowerCase().replace(/[-\s]+/g, "_");
  if (normalized === "ready" || normalized === "clear") return "Ready";
  if (normalized === "not_synced") return "Not synced";
  if (normalized === "validation_warning" || normalized === "warning") return "Warning";
  if (normalized === "validating") return "Validating";
  if (normalized === "changes_found") return "Changes found";
  if (normalized === "paused") return "Paused";
  if (normalized === "syncing" || normalized === "running") return "Running";
  if (normalized === "checking") return "Checking";
  if (["issue", "error"].includes(normalized)) return "Issue";
  return "Not assessed";
}

function asRecord(value: unknown): DiagnosticsRow | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as DiagnosticsRow : null;
}

function arrayRows(value: unknown): DiagnosticsRow[] {
  return Array.isArray(value) ? value.filter((row): row is DiagnosticsRow => Boolean(row && typeof row === "object" && !Array.isArray(row))) : [];
}

function linkageValue(rows: DiagnosticsRow[], category: string) {
  const row = rows.find((item) => String(item.category ?? "") === category);
  return row ? firstMetric(row, "count") : undefined;
}

function reasonLabel(value: unknown) {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const reason = value as DiagnosticsRow;
    const code = String(reason.code ?? "diagnostics").replace(/_/g, " ");
    const count = reason.count === undefined ? "" : ` (${String(reason.count)})`;
    return `${humanLabel(code)}${count}`;
  }
  return String(value ?? "Diagnostics evidence needs review.");
}

function nullableNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function sumMetrics(...values: unknown[]): MetricValue {
  if (!values.length || values.some((value) => value === null || value === undefined || value === "")) return null;
  const texts = values.map((value) => String(value));
  if (texts.every((value) => /^-?\d+$/u.test(value))) {
    return texts.reduce((sum, value) => sum + BigInt(value), 0n).toString();
  }
  const numbers = values.map((value) => Number(value));
  return numbers.every(Number.isFinite) ? numbers.reduce((sum, value) => sum + value, 0) : null;
}

function positiveIntent(value: unknown, positive: "warning" | "bad", zero: "neutral" | "good" = "neutral"): "neutral" | "good" | "warning" | "bad" {
  const number = nullableNumber(value);
  if (number === null) return "neutral";
  return number > 0 ? positive : zero;
}

function hasKnownPositiveLoss(...values: unknown[]) {
  return values.some((value) => {
    const number = nullableNumber(value);
    return number !== null && number > 0;
  });
}

function reconciliationIntent(mismatch: unknown, comparable: unknown, unassessed: unknown, scope: unknown): "neutral" | "good" | "bad" {
  const mismatchCount = nullableNumber(mismatch);
  if (mismatchCount === null) return "neutral";
  if (mismatchCount > 0) return "bad";
  if (["none", "no_evidence", "not_assessed", "subset"].includes(String(scope ?? "").toLowerCase()) && nullableNumber(comparable) === 0) return "neutral";
  if (nullableNumber(comparable) === 0 && (nullableNumber(unassessed) ?? 0) > 0) return "neutral";
  if (nullableNumber(comparable) === null) return "neutral";
  return "good";
}

function fieldCoverageIntent(rate: unknown, issues: unknown): "neutral" | "good" | "warning" {
  if (nullableNumber(rate) === null) return "neutral";
  return (nullableNumber(issues) ?? 0) > 0 ? "warning" : "good";
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
