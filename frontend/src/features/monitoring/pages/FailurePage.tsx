import { useEffect, useState } from "react";
import type { MonitoringRecord, MonitoringReport } from "../../../shared/api/domainTypes";
import type { MonitoringFilters } from "../monitoringFilters";import { CompactNumberValue, DetailMetric, HealthStripCard, ReportChart, ReportPanel, TablePager, WindowPairDetail, failureCategoriesRuleTooltip, formatPercent, metricNumber, metricValue, monitoringTimezone } from "../components/monitoringPresentation";
import { failureCategoryPhaseMatrixOption, failureHorizontalBarOption, failureTrendOption } from "./FailurePagePresentation";
import { EndpointImpactTable, FailurePhaseLegend, FailureQueueTable, FailureTrendLegend, RepeatedFailureTable } from "./FailurePageSupport";

const FAILURE_QUEUE_PAGE_SIZE = 50;

export function FailurePage({
  report,
  rows = report.failures.failed_records,
  filters,
  onInspect
}: {
  report: MonitoringReport;
  rows?: MonitoringRecord[];
  filters: MonitoringFilters;
  onInspect?: (row: Record<string, unknown>) => void;
}) {
  const timezoneName = monitoringTimezone(report);
  const kpis = (report.failures.kpis ?? {}) as Record<string, string | number | null>;
  const latestActivity = (report.failures.latest_queue ?? rows) as Array<Record<string, string | number | null>>;
  const repeatedSignatures = (report.failures.repeated_signatures ?? []) as Array<Record<string, string | number | null>>;
  const endpointImpact = (report.failures.endpoint_impact ?? []) as Array<Record<string, string | number | null>>;
  const stageRows = report.failures.failed_by_stage ?? [];
  const topFailingDataflows = (report.failures.top_failing_dataflows ?? []) as Array<Record<string, string | number>>;
  const categoryPhaseRows = (report.failures.failure_category_phase_matrix ?? []) as Array<Record<string, string | number>>;
  const windows = report.operations.windows ?? {};
  const last24Window = (windows.last_24_hours ?? {}) as Record<string, unknown>;
  const last7Window = (windows.last_7_days ?? {}) as Record<string, unknown>;
  const [queueOffset, setQueueOffset] = useState(0);
  const [queueLimit, setQueueLimit] = useState(FAILURE_QUEUE_PAGE_SIZE);

  useEffect(() => {
    setQueueOffset(0);
  }, [report]);

  const queueLoadedRows = Math.min(queueLimit, Math.max(0, latestActivity.length - queueOffset));
  const failureRateTitle = [
    "Job failure rate = jobs where job.status = failed / (jobs where job.status = succeeded + jobs where job.status = failed).",
    "Job failure status comes from job.status. Child dataflow counters are independent evidence.",
    "Dataflow failure rate = failed dataflow runs / (succeeded dataflow runs + failed dataflow runs).",
    "Skipped is excluded from failure rate."
  ].join("\n");
  const blastRadiusTitle = [
    "Blast radius measures how wide the current failures spread.",
    "Job shapes = distinct normalized job.stages + job.operation_types where job.status = failed.",
    "Job shape = normalized job.stages bundle + job.operation_types bundle.",
    "Stages and operation types are treated as bundle/config values, not split into individual items.",
    "Dataflows = distinct dataflow_id values from failed dataflow runs.",
    "Routes = distinct source connection -> destination connection pairs from failed dataflow runs."
  ].join("\n");
  const repeatedSignatureTitle = [
    "Repeated signatures group failed dataflow runs by phase and normalized message.",
    "Category is a rule-based hint and may be imperfect.",
    "A repeated signature is a signature with >= 2 failed runs.",
    "Repeat share = failed runs that belong to repeated signatures / all failed dataflow runs.",
    "Unique = distinct signatures across failed dataflow runs."
  ].join("\n");
  const topCauseTitle = [
    "Top cause share = failed dataflow runs from the most repeated signature / all failed dataflow runs.",
    "Signature = rule-based category + phase + normalized error message.",
    "Category is a hint, not a guaranteed root-cause label.",
    "Use this to decide whether one fix can reduce most failures."
  ].join("\n");
  return (
    <div className="monitoring-page monitoring-failure-report">
      <section className="overview-health-strip monitoring-failure-health-strip">
        <HealthStripCard
          label="Failed jobs"
          value={<CompactNumberValue value={metricValue(kpis, "failed_jobs")} />}
          detail={
            <WindowPairDetail
              firstLabel="24h"
              firstValue={<CompactNumberValue value={metricValue(last24Window, "job_failed")} />}
              firstTone={metricNumber(metricValue(last24Window, "job_failed")) > 0 ? "bad" : "neutral"}
              secondLabel="7d"
              secondValue={<CompactNumberValue value={metricValue(last7Window, "job_failed")} />}
              secondTone={metricNumber(metricValue(last7Window, "job_failed")) > 0 ? "bad" : "neutral"}
            />
          }
          intent={metricNumber(metricValue(kpis, "failed_jobs")) ? "bad" : "neutral"}
          accent="intent"
          title="Failed job runs in current filters, counted from job.status = failed. Child dataflow counters are shown independently."
        />
        <HealthStripCard
          label="Failed dataflows"
          value={<CompactNumberValue value={metricValue(kpis, "failed_dataflows") ?? rows.length} />}
          detail={
            <WindowPairDetail
              firstLabel="24h"
              firstValue={<CompactNumberValue value={metricValue(last24Window, "dataflow_failed")} />}
              firstTone={metricNumber(metricValue(last24Window, "dataflow_failed")) > 0 ? "bad" : "neutral"}
              secondLabel="7d"
              secondValue={<CompactNumberValue value={metricValue(last7Window, "dataflow_failed")} />}
              secondTone={metricNumber(metricValue(last7Window, "dataflow_failed")) > 0 ? "bad" : "neutral"}
            />
          }
          intent={metricNumber(metricValue(kpis, "failed_dataflows")) ? "bad" : "neutral"}
          accent="intent"
          title="Failed dataflow runs in current filters. Skipped is not counted as failure."
        />
        <HealthStripCard
          label="Failure rate"
          value={`${formatPercent(metricValue(report.operations.kpis, "job_failure_rate"))} job`}
          detail={<DetailMetric label="dataflow" value={formatPercent(metricValue(report.operations.dataflow_kpis, "failure_rate"))} tone={metricNumber(metricValue(report.operations.dataflow_kpis, "failure_rate")) > 0 ? "bad" : "neutral"} />}
          intent={metricNumber(metricValue(report.operations.kpis, "job_failure_rate")) || metricNumber(metricValue(report.operations.dataflow_kpis, "failure_rate")) ? "bad" : "neutral"}
          accent="intent"
          title={failureRateTitle}
        />
        <HealthStripCard
          label="Blast radius"
          value={<><CompactNumberValue value={metricValue(kpis, "affected_job_shapes") ?? metricValue(kpis, "affected_job_contexts") ?? metricValue(kpis, "affected_stages")} /> shapes</>}
          detail={
            <>
              <DetailMetric label="dataflows" value={<CompactNumberValue value={metricValue(kpis, "affected_dataflows")} />} tone={metricNumber(metricValue(kpis, "affected_dataflows")) ? "warning" : "neutral"} />
              <span className="separator"> · </span>
              <DetailMetric label="routes" value={<CompactNumberValue value={metricValue(kpis, "affected_routes")} />} tone={metricNumber(metricValue(kpis, "affected_routes")) ? "warning" : "neutral"} />
            </>
          }
          intent={metricNumber(metricValue(kpis, "affected_job_shapes") ?? metricValue(kpis, "affected_job_contexts") ?? metricValue(kpis, "affected_stages")) || metricNumber(metricValue(kpis, "affected_dataflows")) ? "warning" : "neutral"}
          accent="intent"
          title={blastRadiusTitle}
        />
        <HealthStripCard
          label="Repeated signatures"
          value={<CompactNumberValue value={metricValue(kpis, "repeated_signatures")} />}
          detail={
            <>
              <DetailMetric label="repeat runs" value={formatPercent(metricValue(kpis, "repeated_failure_share"))} tone={metricNumber(metricValue(kpis, "repeated_failure_share")) ? "warning" : "neutral"} />
              <span className="separator"> · </span>
              <DetailMetric label="unique" value={<CompactNumberValue value={metricValue(kpis, "unique_signatures")} />} tone="neutral" />
            </>
          }
          intent={metricNumber(metricValue(kpis, "repeated_signatures")) ? "warning" : "neutral"}
          accent="intent"
          title={repeatedSignatureTitle}
        />
        <HealthStripCard
          label="Top cause share"
          value={formatPercent(metricValue(kpis, "top_cause_share"))}
          detail={<DetailMetric label={`${String(kpis.top_cause_category ?? "-")} / ${String(kpis.top_cause_phase ?? "-")}`} value={<CompactNumberValue value={metricValue(kpis, "top_cause_runs")} />} tone={metricNumber(metricValue(kpis, "top_cause_runs")) ? "bad" : "neutral"} />}
          intent={metricNumber(metricValue(kpis, "top_cause_share")) >= 50 ? "bad" : metricNumber(metricValue(kpis, "top_cause_share")) > 0 ? "warning" : "neutral"}
          accent="intent"
          title={topCauseTitle}
        />
      </section>

      <div className="monitoring-failure-content monitoring-equal-rows-3">
        <section className="monitoring-failure-top-grid">
          <ReportPanel
            title="Latest failure activity"
            subtitle={
              <>
                <CompactNumberValue value={metricValue(kpis, "recovered_attempt_count")} /> recovered attempts ·{" "}
                <CompactNumberValue value={metricValue(kpis, "recovered_run_count")} /> successful runs
              </>
            }
            titleTooltip="Failed dataflow runs and failed attempts from runs that later succeeded. Recovered attempts use their end time, then start time, when recorded; otherwise they use the parent dataflow run time. Recovered attempts remain excluded from terminal failure KPIs."
            headerAction={
              <TablePager
                limit={queueLimit}
                offset={queueOffset}
                loadedRows={queueLoadedRows}
                totalRows={latestActivity.length}
                loading={false}
                onPageChange={setQueueOffset}
                onPageSizeChange={(nextLimit) => {
                  setQueueLimit(nextLimit);
                  setQueueOffset(0);
                }}
              />
            }
          >
            <FailureQueueTable
              rows={latestActivity}
              maxRows={queueLimit}
              offset={queueOffset}
              onInspect={onInspect}
              timezoneName={timezoneName}
            />
          </ReportPanel>
          <ReportPanel
            title="Failure trend"
            titleTooltip="Failure trend by selected grain. Failed dataflows are root-cause events; failed jobs are counted from job.status = failed, with child dataflow counters shown independently."
            headerAction={<FailureTrendLegend />}
          >
            <ReportChart
              option={failureTrendOption(
                report.failures.failure_trend_by_date,
                filters,
                report.summary.date_range,
                timezoneName,
                report.summary.effective_grain ?? undefined
              )}
              height="100%"
            />
          </ReportPanel>
        </section>
        <section className="monitoring-failure-middle-grid">
          <ReportPanel
            title="Repeated dataflow failure signatures"
            subtitle="Top 6 by failed runs · from up to 30 signatures · category / phase / error"
            titleTooltip={repeatedSignatureTitle}
          >
            <RepeatedFailureTable rows={repeatedSignatures} maxRows={6} timezoneName={timezoneName} />
          </ReportPanel>
          <ReportPanel
            title="Error hint x phase"
            titleTooltip={failureCategoriesRuleTooltip()}
            headerAction={<FailurePhaseLegend />}
          >
            <ReportChart option={failureCategoryPhaseMatrixOption(categoryPhaseRows)} height="100%" wheelDataZoomStep={1} />
          </ReportPanel>
        </section>
        <section className="monitoring-failure-bottom-grid">
          <ReportPanel
            title="Endpoint impact"
            subtitle="Top 6 by failed runs · from up to 30 source-to-destination pairs"
            titleTooltip="Groups failed dataflow runs by source and destination connection names to identify concentrated endpoint issues."
          >
            <EndpointImpactTable rows={endpointImpact} maxRows={6} timezoneName={timezoneName} />
          </ReportPanel>
          <ReportPanel
            title="Top failing dataflows"
            titleTooltip="Ranks dataflows by failed run count in the current filters. Tooltip includes latest stage and error context."
            className="monitoring-failure-records-panel"
            headerAction={<FailurePhaseLegend />}
          >
            <ReportChart option={failureHorizontalBarOption(topFailingDataflows, "dataflow_name", "error_count", "Failed runs")} height="100%" wheelDataZoomStep={1} />
          </ReportPanel>
          <ReportPanel
            title="Failure by stage"
            titleTooltip="Counts failed dataflow runs by stage. Job-level failures without a dataflow stage are not included here."
            headerAction={<FailurePhaseLegend />}
          >
            <ReportChart option={failureHorizontalBarOption(stageRows as Array<Record<string, string | number>>, "name", "count", "Failed runs")} height="100%" wheelDataZoomStep={1} />
          </ReportPanel>
        </section>
      </div>
    </div>
  );
}
