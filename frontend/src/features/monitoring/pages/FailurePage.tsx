import type { MonitoringRecord, MonitoringReport } from "../../../shared/api/types";
import type { MonitoringFilters } from "../monitoringFilters";
import {
  DetailMetric,
  EndpointImpactTable,
  FailureQueueTable,
  HealthStripCard,
  RepeatedFailureTable,
  ReportChart,
  ReportPanel,
  WindowPairDetail,
  failureCategoryPhaseMatrixOption,
  failureCategoriesRuleTooltip,
  failureHorizontalBarOption,
  failureTrendOption,
  formatNumber,
  formatPercent,
  monitoringTimezone
} from "../monitoringShared";

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
  const latestQueue = (report.failures.latest_queue ?? rows) as Array<Record<string, string | number | null>>;
  const repeatedSignatures = (report.failures.repeated_signatures ?? []) as Array<Record<string, string | number | null>>;
  const endpointImpact = (report.failures.endpoint_impact ?? []) as Array<Record<string, string | number | null>>;
  const stageRows = report.failures.failed_by_stage ?? [];
  const topFailingDataflows = (report.failures.top_failing_dataflows ?? []) as Array<Record<string, string | number>>;
  const categoryPhaseRows = (report.failures.failure_category_phase_matrix ?? []) as Array<Record<string, string | number>>;
  const windows = report.operations.windows ?? {};
  const last24Window = (windows.last_24_hours ?? {}) as Record<string, number>;
  const last7Window = (windows.last_7_days ?? {}) as Record<string, number>;
  const failureRateTitle = [
    "Job failure rate = failed jobs / (succeeded jobs + failed jobs).",
    "A job is failed when at least one child dataflow failed.",
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
          value={formatNumber(Number(kpis.failed_jobs ?? 0))}
          detail={
            <WindowPairDetail
              firstLabel="24h"
              firstValue={formatNumber(last24Window.job_failed ?? 0)}
              firstTone={(last24Window.job_failed ?? 0) > 0 ? "bad" : "neutral"}
              secondLabel="7d"
              secondValue={formatNumber(last7Window.job_failed ?? 0)}
              secondTone={(last7Window.job_failed ?? 0) > 0 ? "bad" : "neutral"}
            />
          }
          intent={Number(kpis.failed_jobs ?? 0) ? "bad" : "neutral"}
          title="Failed job runs in current filters. Job failed is a rollup signal: at least one child dataflow failed."
        />
        <HealthStripCard
          label="Failed dataflows"
          value={formatNumber(Number(kpis.failed_dataflows ?? rows.length ?? 0))}
          detail={
            <WindowPairDetail
              firstLabel="24h"
              firstValue={formatNumber(last24Window.dataflow_failed ?? 0)}
              firstTone={(last24Window.dataflow_failed ?? 0) > 0 ? "bad" : "neutral"}
              secondLabel="7d"
              secondValue={formatNumber(last7Window.dataflow_failed ?? 0)}
              secondTone={(last7Window.dataflow_failed ?? 0) > 0 ? "bad" : "neutral"}
            />
          }
          intent={Number(kpis.failed_dataflows ?? 0) ? "bad" : "neutral"}
          title="Failed dataflow runs in current filters. Skipped is not counted as failure."
        />
        <HealthStripCard
          label="Failure rate"
          value={`${formatPercent(Number(report.operations.kpis.job_failure_rate ?? 0))} job`}
          detail={<DetailMetric label="dataflow" value={formatPercent(Number(report.operations.dataflow_kpis.failure_rate ?? 0))} tone={Number(report.operations.dataflow_kpis.failure_rate ?? 0) > 0 ? "bad" : "neutral"} />}
          intent={Number(report.operations.kpis.job_failure_rate ?? 0) || Number(report.operations.dataflow_kpis.failure_rate ?? 0) ? "bad" : "neutral"}
          title={failureRateTitle}
        />
        <HealthStripCard
          label="Blast radius"
          value={`${formatNumber(Number(kpis.affected_job_shapes ?? kpis.affected_job_contexts ?? kpis.affected_stages ?? 0))} shapes`}
          detail={
            <>
              <DetailMetric label="dataflows" value={formatNumber(Number(kpis.affected_dataflows ?? 0))} tone={Number(kpis.affected_dataflows ?? 0) ? "warning" : "neutral"} />
              <span className="separator"> · </span>
              <DetailMetric label="routes" value={formatNumber(Number(kpis.affected_routes ?? 0))} tone={Number(kpis.affected_routes ?? 0) ? "warning" : "neutral"} />
            </>
          }
          intent={Number(kpis.affected_job_shapes ?? kpis.affected_job_contexts ?? kpis.affected_stages ?? 0) || Number(kpis.affected_dataflows ?? 0) ? "warning" : "neutral"}
          title={blastRadiusTitle}
        />
        <HealthStripCard
          label="Repeated signatures"
          value={formatNumber(Number(kpis.repeated_signatures ?? 0))}
          detail={
            <>
              <DetailMetric label="repeat runs" value={formatPercent(Number(kpis.repeated_failure_share ?? 0))} tone={Number(kpis.repeated_failure_share ?? 0) ? "warning" : "neutral"} />
              <span className="separator"> · </span>
              <DetailMetric label="unique" value={formatNumber(Number(kpis.unique_signatures ?? 0))} tone="neutral" />
            </>
          }
          intent={Number(kpis.repeated_signatures ?? 0) ? "warning" : "neutral"}
          title={repeatedSignatureTitle}
        />
        <HealthStripCard
          label="Top cause share"
          value={formatPercent(Number(kpis.top_cause_share ?? 0))}
          detail={<DetailMetric label={`${String(kpis.top_cause_category ?? "-")} / ${String(kpis.top_cause_phase ?? "-")}`} value={formatNumber(Number(kpis.top_cause_runs ?? 0))} tone={Number(kpis.top_cause_runs ?? 0) ? "bad" : "neutral"} />}
          intent={Number(kpis.top_cause_share ?? 0) >= 50 ? "bad" : Number(kpis.top_cause_share ?? 0) > 0 ? "warning" : "neutral"}
          title={topCauseTitle}
        />
      </section>

      <div className="monitoring-failure-content">
        <section className="monitoring-failure-top-grid">
          <ReportPanel
            title="Latest failed dataflow queue"
            subtitle="newest incidents first"
            titleTooltip="Operational queue of failed dataflow runs. Job failures are rollup context and are not listed as separate incidents."
          >
            <FailureQueueTable rows={latestQueue} maxRows={7} onInspect={onInspect} timezoneName={timezoneName} />
          </ReportPanel>
          <ReportPanel
            title="Failure trend"
            subtitle="failed jobs and dataflows"
            titleTooltip="Failure trend by selected grain. Failed dataflows are root-cause events; failed jobs are rollup counts where at least one child dataflow failed."
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
            subtitle="category / phase / error"
            titleTooltip={repeatedSignatureTitle}
          >
            <RepeatedFailureTable rows={repeatedSignatures} maxRows={6} timezoneName={timezoneName} />
          </ReportPanel>
          <ReportPanel
            title="Error hint x phase"
            subtitle="rule-based, not guaranteed"
            titleTooltip={failureCategoriesRuleTooltip()}
          >
            <ReportChart option={failureCategoryPhaseMatrixOption(categoryPhaseRows)} height="100%" wheelDataZoomStep={1} />
          </ReportPanel>
        </section>
        <section className="monitoring-failure-bottom-grid">
          <ReportPanel
            title="Endpoint impact"
            subtitle="source to destination pairs"
            titleTooltip="Groups failed dataflow runs by source and destination connection names to identify concentrated endpoint issues."
          >
            <EndpointImpactTable rows={endpointImpact} maxRows={6} timezoneName={timezoneName} />
          </ReportPanel>
          <ReportPanel
            title="Top failing dataflows"
            subtitle="failed run count"
            titleTooltip="Ranks dataflows by failed run count in the current filters. Tooltip includes latest stage and error context."
            className="monitoring-failure-records-panel"
          >
            <ReportChart option={failureHorizontalBarOption(topFailingDataflows, "dataflow_name", "error_count", "Failed runs")} height="100%" wheelDataZoomStep={1} />
          </ReportPanel>
          <ReportPanel
            title="Failure by stage"
            subtitle="failed dataflow runs"
            titleTooltip="Counts failed dataflow runs by stage. Job-level failures without a dataflow stage are not included here."
          >
            <ReportChart option={failureHorizontalBarOption(stageRows as Array<Record<string, string | number>>, "name", "count", "Failed runs")} height="100%" wheelDataZoomStep={1} />
          </ReportPanel>
        </section>
      </div>
    </div>
  );
}
