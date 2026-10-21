import type { MonitoringRecord } from "../../../shared/api/domainTypes";
import { DataTable, StatusCell, formatSeconds, type TableSort } from "../../../shared/components/DataTable";
import { Help } from "../../../shared/components/Help";
import type { ReactNode } from "react";
import { formatTimestampForDisplay } from "../../../shared/time";
import type { MonitoringRegistryOrigin, MonitoringRegistryRunReference } from "../monitoringQueries";
import {
  CompactNumberValue,
  DataflowPhaseCell,
  TablePager,
  WatermarkBadge,
  metricValue,
} from "../components/monitoringPresentation";
import {
  DataflowRouteEndpoint,
  compactDrawerIdentifier,
  dataflowEndpointSummary,
  formatFreshnessAge,
  GroupedDetailCard,
  hasValue,
  IssueCell,
} from "./detailPrimitives";
import { DataflowCopyValue } from "./DataflowCopyValue";
import { DataflowReadWatermarkBody } from "./DataflowStoryWatermark";
import { DataflowConfigurationBody } from "./DataflowStoryConfiguration";
import { StoryBytes, StoryCount, StoryDisclosure, StoryDuration } from "./DataflowStoryPrimitives";
import { StoryExpandedValue } from "./DataflowStoryValues";
import { buildDataflowStory } from "./dataflowStoryModel";
import { monitoringRegistryRunReference } from "../monitoringDetailEvidence";
import { lifecycleStatusPresentation } from "../../../shared/statusPresentation";
import { RegistryDurationBoxPlot } from "./RegistryDurationBoxPlot";
import { registryVolumeObservation, registryVolumeTooltip, registryVolumeSignalTooltip, positiveVolumeMetric } from "../registryVolumeMetrics";

type RegistrySection = "read-watermark" | "configuration" | "technical";
type RegistrySectionState = { loading: boolean; ready?: boolean; empty?: boolean; error: string | null; retry: () => void };
type RegistryOverviewState = { loading: boolean; ready: boolean; preview?: boolean; error: string | null; retry: () => void };

export function DataflowRegistryDetailSections({
  row,
  timezoneName,
  overviewState,
  onSectionOpen,
  sectionStates,
  sectionRows,
  analyticsGeneration,
  registryOrigin,
  runtimeRows = [],
  runtimeTotal = 0,
  runtimeOffset = 0,
  runtimeLimit = 100,
  runtimeLoading = false,
  runtimeError,
  runtimeSort,
  onRuntimePageChange,
  onRuntimePageSizeChange,
  onRuntimeSort,
  onRetryRuntime,
  executionRows = [],
  executionTotal = 0,
  executionOffset = 0,
  executionLimit = 100,
  executionLoading = false,
  executionError,
  executionSort,
  onExecutionPageChange,
  onExecutionPageSizeChange,
  onExecutionSort,
  onRetryExecution,
  onOpenRun,
  onOpenDataflow,
}: {
  row: Record<string, unknown>;
  timezoneName?: string | null;
  overviewState?: RegistryOverviewState;
  onSectionOpen?: (section: RegistrySection) => void;
  sectionStates?: Partial<Record<RegistrySection, RegistrySectionState>>;
  sectionRows?: Partial<Record<RegistrySection, Record<string, unknown>>>;
  analyticsGeneration?: string | null;
  registryOrigin?: MonitoringRegistryOrigin | null;
  runtimeRows?: Record<string, unknown>[];
  runtimeTotal?: number;
  runtimeOffset?: number;
  runtimeLimit?: number;
  runtimeLoading?: boolean;
  runtimeError?: string | null;
  runtimeSort?: TableSort;
  onRuntimePageChange?: (offset: number) => void;
  onRuntimePageSizeChange?: (limit: number) => void;
  onRuntimeSort?: (sort: TableSort) => void;
  onRetryRuntime?: () => void;
  executionRows?: MonitoringRecord[];
  executionTotal?: number;
  executionOffset?: number;
  executionLimit?: number;
  executionLoading?: boolean;
  executionError?: string | null;
  executionSort?: TableSort;
  onExecutionPageChange?: (offset: number) => void;
  onExecutionPageSizeChange?: (limit: number) => void;
  onExecutionSort?: (sort: TableSort) => void;
  onRetryExecution?: () => void;
  onOpenRun?: (row: MonitoringRecord, reference?: MonitoringRegistryRunReference) => void;
  onOpenDataflow?: (row: MonitoringRecord) => void;
}) {
  const readRow = sectionRows?.["read-watermark"] ?? row;
  const configurationRow = sectionRows?.configuration ?? row;
  const technicalRow = sectionRows?.technical ?? row;
  const watermarkRow = { ...readRow, movement_state: readRow.snapshot_movement_state };
  const watermarkStory = buildDataflowStory(watermarkRow);
  const showOverview = !overviewState || overviewState.ready;

  return (
    <>
      <RegistryOverviewNotice state={overviewState} />
      {!showOverview && overviewState?.preview ? <RegistryRoute row={row} preview /> : null}
      {showOverview ? <>
        <RegistryRoute row={row} />
        <RegistrySummary row={row} timezoneName={timezoneName} analyticsGeneration={analyticsGeneration} registryOrigin={registryOrigin} onOpenRun={onOpenRun} />
        <RegistryFacets row={row} />
      </> : null}
      <RegistryRuntime
        timezoneName={timezoneName}
        analyticsGeneration={analyticsGeneration}
        registryOrigin={registryOrigin}
        rows={runtimeRows}
        total={runtimeTotal}
        offset={runtimeOffset}
        limit={runtimeLimit}
        loading={runtimeLoading}
        error={runtimeError ?? null}
        sort={runtimeSort ?? { sortBy: "run_count", sortDir: "desc" }}
        onRetry={onRetryRuntime}
        onSort={onRuntimeSort}
        onPageChange={onRuntimePageChange}
        onPageSizeChange={onRuntimePageSizeChange}
        onOpenRun={onOpenRun}
      />
      <RegistryExecution
        timezoneName={timezoneName}
        analyticsGeneration={analyticsGeneration}
        registryOrigin={registryOrigin}
        rows={executionRows}
        total={executionTotal}
        offset={executionOffset}
        limit={executionLimit}
        sort={executionSort ?? { sortBy: "event_time", sortDir: "desc" }}
        loading={executionLoading}
        error={executionError ?? null}
        onRetry={onRetryExecution}
        onSort={onExecutionSort}
        onPageChange={onExecutionPageChange}
        onPageSizeChange={onExecutionPageSizeChange}
        onOpenRun={onOpenRun}
        onOpenDataflow={onOpenDataflow}
      />
      <RegistryLazySection
        title="Read & watermark"
        section="read-watermark"
        state={sectionStates?.["read-watermark"]}
        onOpen={onSectionOpen}
      >
        <DataflowReadWatermarkBody model={watermarkStory} />
      </RegistryLazySection>
      <RegistryLazySection
        title="Configuration"
        section="configuration"
        state={sectionStates?.configuration}
        onOpen={onSectionOpen}
      >
        <RegistrySnapshotProvenance row={configurationRow} fallbackRow={row} timezoneName={timezoneName} />
        <DataflowConfigurationBody row={configurationRow} />
      </RegistryLazySection>
      <RegistryLazySection
        title="Technical details"
        section="technical"
        state={sectionStates?.technical}
        onOpen={onSectionOpen}
      >
        <div className="monitoring-registry-technical-summary">
          <GroupedDetailCard title="Physical references" rows={[
            ["Observed copies", technicalRow.technical_reference_total],
            ["Inspected", technicalRow.technical_reference_inspected],
            ["Scalar conflicts", technicalRow.technical_reference_conflicting_run_count],
            ["Comparison basis", technicalRow.technical_reference_comparison_basis],
            ["Coverage", technicalRow.technical_reference_complete === true ? "complete" : "sampled · more references exist"],
            ["Reference sample", technicalRow.technical_references, "technical_references"],
          ]} showEmpty />
        </div>
      </RegistryLazySection>
    </>
  );
}

function RegistryOverviewNotice({ state }: { state?: RegistryOverviewState }) {
  if (!state || state.ready && !state.loading && !state.error) return null;
  if (state.loading) return <section className="monitoring-registry-section-state" role="status">{state.ready ? "Refreshing the dataflow profile…" : "Loading the dataflow profile…"}</section>;
  if (state.error) return (
    <section className="monitoring-registry-section-state" role="alert">
      {state.ready ? "The displayed profile could not be refreshed." : "The dataflow profile could not be loaded."} <button type="button" className="small-button" onClick={state.retry}>Retry</button>
    </section>
  );
  return <section className="monitoring-registry-section-state" role="status">No dataflow runs match the current filters.</section>;
}

function RegistryRoute({ row, preview = false }: { row: Record<string, unknown>; preview?: boolean }) {
  const profile = preview ? row : profileSnapshot(row);
  return (
    <div className="monitoring-dataflow-route-card monitoring-registry-route">
      <DataflowRouteEndpoint direction="source" endpoint={dataflowEndpointSummary(profile, "source")} />
      <span className="monitoring-dataflow-route-arrow" aria-hidden="true">→</span>
      <DataflowRouteEndpoint direction="destination" endpoint={dataflowEndpointSummary(profile, "destination")} />
    </div>
  );
}

function RegistrySummary({ row, timezoneName, analyticsGeneration, registryOrigin, onOpenRun }: { row: Record<string, unknown>; timezoneName?: string | null; analyticsGeneration?: string | null; registryOrigin?: MonitoringRegistryOrigin | null; onOpenRun?: (row: MonitoringRecord, reference?: MonitoringRegistryRunReference) => void }) {
  const recentRuns = recentRunRows(row);
  const statusRows = registryStatusRows(row);
  return (
    <section className="monitoring-detail-section monitoring-registry-summary">
      <h3>Summary &amp; insights</h3>
      <div className="monitoring-freshness-summary-grid monitoring-registry-summary-grid">
        <GroupedDetailCard title="Dataflow summary" rows={[
          ["Dataflow ID", hasValue(row.dataflow_id) ? <DataflowCopyValue label="Dataflow ID" value={row.dataflow_id} title={String(row.dataflow_id)}><strong className="monitoring-registry-full-id">{compactDrawerIdentifier(row.dataflow_id) ?? "unk"}</strong></DataflowCopyValue> : "unk"],
          ["Latest run status", <StatusCell row={{ status: row.latest_run_status }} />],
          ["Latest run ID", <RegistryRunId value={row.latest_run_id} />],
          ["Latest run", formatTimestamp(row.latest_run_at, timezoneName)],
          ["Runs", metricValue(row, "run_count")],
          ...statusRows,
          ["Recent runs", <RecentRunStrip rows={recentRuns} timezoneName={timezoneName} analyticsGeneration={analyticsGeneration} registryOrigin={registryOrigin} onOpenRun={onOpenRun} />],
        ]} showEmpty />
        <RegistryWorkload row={row} />
        <RegistryInsight row={row} timezoneName={timezoneName} />
      </div>
    </section>
  );
}

function registryStatusRows(row: Record<string, unknown>): [string, ReactNode][] {
  const rows: [string, ReactNode][] = ([
    ["Succeeded", "succeeded"], ["Failed", "failed"], ["Skipped", "skipped"],
    ["Running", "running"], ["Pending", "pending"],
  ] as const).map(([label, status]) => [label,
    <span className="monitoring-registry-status-count" data-status={status} style={{ color: lifecycleStatusPresentation(status)?.textColor }}>
      <CompactNumberValue value={metricValue(row, `${status}_count`)} fallback="unk" />
    </span>,
  ]);
  if (isPositiveMetric(row.other_count)) {
    rows.push(["Other", <CompactNumberValue value={metricValue(row, "other_count")} fallback="unk" />]);
  }
  return rows;
}

type RegistryWorkloadField = {
  readonly label: string;
  readonly key: string;
  readonly action: string;
  readonly kind: "count" | "bytes";
};

const REGISTRY_WORKLOAD_GROUPS = [
  {
    title: "Rows",
    pairs: [
      [
        { label: "Read", key: "volume_rows_read", action: "read", kind: "count" },
        { label: "Written", key: "volume_rows_written", action: "written", kind: "count" },
      ],
      [
        { label: "Insert", key: "volume_rows_inserted", action: "inserted", kind: "count" },
        { label: "Update", key: "volume_rows_updated", action: "updated", kind: "count" },
        { label: "Delete", key: "volume_rows_deleted", action: "deleted", kind: "count" },
      ],
    ],
  },
  {
    title: "Files",
    pairs: [[
      { label: "Add", key: "volume_files_added", action: "added", kind: "count" },
      { label: "Remove", key: "volume_files_removed", action: "removed", kind: "count" },
    ]],
  },
  {
    title: "Bytes",
    pairs: [[
      { label: "Add", key: "volume_bytes_added", action: "added", kind: "bytes" },
      { label: "Remove", key: "volume_bytes_removed", action: "removed", kind: "bytes" },
    ]],
  },
] as const;

function RegistryWorkload({ row }: { row: Record<string, unknown> }) {
  return (
    <section className="dataflow-section-block dataflow-run-workload monitoring-registry-workload">
      <Help as="h4" title="Totals across canonical runs in the current filters">Workload</Help>
      {REGISTRY_WORKLOAD_GROUPS.map((group) => (
        <div className="dataflow-workload-group" key={group.title}>
          <h5>{group.title}</h5>
          {group.pairs.map((pair, index) => (
            <RegistryWorkloadPair key={`${group.title}-${index}`} row={row} fields={pair} />
          ))}
        </div>
      ))}
    </section>
  );
}

function RegistryWorkloadPair({ row, fields, average = false }: { row: Record<string, unknown>; fields: readonly RegistryWorkloadField[]; average?: boolean }) {
  return (
    <dl className="dataflow-story-fields dataflow-summary-pair">
      <div>
        <dt>{fields.map((field, index) => <span key={field.key}>{index ? <span className="dataflow-pair-divider" aria-hidden="true"> | </span> : null}{field.label}</span>)}</dt>
        <dd>{fields.map((field, index) => {
          const observation = registryVolumeObservation(row, field.key, average);
          const value = observation.value;
          return <span key={field.key}>{index ? <span className="dataflow-pair-divider" aria-hidden="true"> | </span> : null}
            <Help className={`dataflow-metric-action action-${field.action}`} data-field-path={`/metrics/${field.key}`} aria-label={field.label} title={registryVolumeTooltip(row, field.key, field.label, field.kind === "bytes", average)}>
              {field.kind === "bytes" ? <StoryBytes value={value} /> : <StoryCount value={value} />}
              {observation.partial && value != null ? <span className="monitor-observed-label"> observed</span> : null}
            </Help>
          </span>;
        })}</dd>
      </div>
    </dl>
  );
}

function RegistryInsight({ row, timezoneName }: { row: Record<string, unknown>; timezoneName?: string | null }) {
  return (
    <section className="dataflow-section-block dataflow-summary-insights monitoring-registry-insight">
      <h4>Insight</h4>
      <div className="monitoring-registry-insight-performance">
        <dl className="dataflow-story-fields">
          <div><dt>Bottleneck</dt><dd><RegistryBottleneck row={row} /></dd></div>
        </dl>
        <RegistryPhaseContribution row={row} />
      </div>
      <div className="monitoring-registry-insight-watermark">
        <dl className="dataflow-story-fields monitoring-registry-watermark-heading">
          <Help as="div" title="Last recorded watermark save in the current filters"><dt>Last watermark</dt><dd>{hasValue(row.last_saved_watermark_at) ? formatTimestamp(row.last_saved_watermark_at, timezoneName) : "time unk"}</dd></Help>
        </dl>
        <StoryExpandedValue value={row.last_saved_watermark ?? undefined} kind="json" showType={false} />
      </div>
      <dl className="dataflow-story-fields monitoring-registry-insight-volume">
        <div><dt>Volume signal</dt><dd><VolumeCandidateSummary row={row} /></dd></div>
      </dl>
    </section>
  );
}

function RegistryFacets({ row }: { row: Record<string, unknown> }) {
  return (
    <section className="monitoring-detail-section monitoring-registry-facets">
      <h3>Freshness, performance &amp; volume</h3>
      <div className="monitoring-dataflow-detail-grid monitoring-registry-facet-grid">
        <RegistryFreshness row={row} />
        <section className="monitoring-job-group-card monitoring-registry-performance">
          <span>Performance</span>
          <dl>{([
            ["Avg run duration", duration(row.performance_avg_duration_seconds)],
            ["P50 | P95", `${duration(row.performance_p50_duration_seconds)} | ${duration(row.performance_p95_duration_seconds)}`],
            ["Timing measured", `${metricText(row.performance_timing_measured ?? 0)} / ${metricText(row.performance_timing_eligible ?? 0)}`],
            ["Rows read / sec", <CompactNumberValue value={metricValue(row, "performance_rows_read_per_second")} fallback="unk" />],
            ["Performance candidates", <PerformanceCandidateSummary row={row} />],
          ] as [string, ReactNode][]).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
          <RegistryDurationBoxPlot row={row} />
          <RegistryPhaseContribution row={row} average />
        </section>
        <RegistryVolume row={row} />
      </div>
    </section>
  );
}

function RegistryVolume({ row }: { row: Record<string, unknown> }) {
  const net = registryVolumeObservation(row, "volume_net_bytes");
  return (
    <section className="monitoring-job-group-card monitoring-registry-volume">
      <span>Volume</span>
      <div className="monitoring-registry-volume-group">
        <Help as="h5" title="Runs with a recorded metric / all runs in the current filters. Recorded zero values count; this does not count only runs with a positive effect.">Coverage</Help>
        <dl>{([
          ["Rows read", coverage(row.volume_rows_read_measured, row.volume_rows_read_eligible)],
          ["Rows written", coverage(row.volume_rows_written_measured, row.volume_rows_written_eligible)],
          ["Rows insert | update | delete", <span>{coverage(row.volume_rows_inserted_measured, row.volume_rows_inserted_eligible)} · {coverage(row.volume_rows_updated_measured, row.volume_rows_updated_eligible)} · {coverage(row.volume_rows_deleted_measured, row.volume_rows_deleted_eligible)}</span>],
          ["Files add | remove", <span>{coverage(row.volume_files_added_measured, row.volume_files_added_eligible)} · {coverage(row.volume_files_removed_measured, row.volume_files_removed_eligible)}</span>],
          ["Bytes add | remove", <span>{coverage(row.volume_bytes_added_measured, row.volume_bytes_added_eligible)} · {coverage(row.volume_bytes_removed_measured, row.volume_bytes_removed_eligible)}</span>],
          ["Net bytes pairs", coverage(row.volume_net_bytes_measured, row.volume_net_bytes_eligible)],
        ] as [string, ReactNode][]).map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
      </div>
      <div className="monitoring-registry-volume-group">
        <Help as="h5" title="Average per run with a recorded value in the current filters, including zero. Missing values are excluded; each metric has its own recorded-run denominator. Averages may be unavailable when coverage is incomplete.">Avg per run</Help>
        {REGISTRY_WORKLOAD_GROUPS.map((group) => (
          <div className="monitoring-registry-volume-average" key={group.title}>
            <h6>{group.title}</h6>
            {group.pairs.map((pair, index) => <RegistryWorkloadPair key={`${group.title}-${index}`} row={row} fields={pair} average />)}
          </div>
        ))}
      </div>
      <div className="monitoring-registry-volume-group">
        <dl><div><dt>Net bytes</dt><dd><Help title={`Sum of per-run added minus removed bytes only where both values are recorded. Independent added/removed totals can cover different runs.\n${registryVolumeTooltip(row, "volume_net_bytes", "Paired net bytes", true)}`}>{positiveVolumeMetric(net.value) ? "+" : ""}<StoryBytes value={net.value} />{net.partial && net.value != null ? <span className="monitor-observed-label"> observed</span> : null}</Help></dd></div>
          <div><dt>Volume signals</dt><dd><Help title={registryVolumeSignalTooltip(row)}><VolumeCandidateSignals value={row.volume_candidate_signals} /></Help></dd></div></dl>
      </div>
    </section>
  );
}

function RegistryFreshness({ row }: { row: Record<string, unknown> }) {
  type FreshnessField = [string, ReactNode, string?];
  const latest: FreshnessField[] = [
    ["Check age", hasValue(row.age_seconds) || hasValue(row.age_days) ? formatFreshnessAge(row.age_seconds, row.age_days) : "Could not determine", "Time since the latest eligible ETL check in the current filters."],
    ["Watermark progress", <WatermarkBadge value={row.movement_state ?? row.save_status ?? row.load_status ?? row.coverage_state} />, "Watermark progress of the latest eligible ETL check: the run and its final source attempt succeeded or skipped. All current filters apply."],
    ["Read boundary", freshnessStateLabel(row.adjustment_state), "Whether the resolved lower read bound differs from the input lower bound, using the latest ETL run. Unchanged means the same bound; adjusted means a different bound; not applied means no lower-bound adjustment applies."],
    ["Watermark save", freshnessStateLabel(row.persistence_state, { save_error: "Save failed" }), "Watermark save outcome of the latest ETL run. Not attempted means no save was attempted; no candidate means no watermark value was available to save."],
    ["Watermark tracking", freshnessStateLabel(row.configuration_state, { configured: "Enabled" }), "Whether watermark configuration is present, absent or cannot be established from the latest ETL run's evidence."],
    ["Watermark issues", watermarkIssueLabel(row), "Watermark load/save errors or insufficient evidence in the latest ETL run. Not attempted and no candidate are not errors by themselves."],
  ];
  const aggregate: FreshnessField[] = [
    ["Eligible checks", hasValue(row.freshness_checks_eligible) ? metricText(row.freshness_checks_eligible) : "Could not determine"],
    ["Advanced rate", <span>{hasValue(row.watermark_advanced_rate) ? percent(row.watermark_advanced_rate) : "Could not determine"} · {metricText(row.watermark_advanced_runs)} / {metricText(row.watermark_rate_runs)} ETL runs</span>, "Advanced / total ETL runs in the current filters × 100%. Includes Initialized, Unknown and runs without watermark configuration, across all run statuses."],
    ["Movement counts", <MovementCounts row={row} />, "Movement counts describe ETL runs with watermark configuration. Advanced rate also includes runs without watermark configuration in its denominator."],
    ["Adjustment coverage", `${metricText(row.adjustment_comparable_runs)} / ${metricText(row.adjustment_total_runs)}`],
  ];
  if (hasValue(row.snapshot_movement_state)
    && String(row.snapshot_movement_state).trim().toLowerCase() !== String(row.movement_state ?? "unknown").trim().toLowerCase()) {
    latest.splice(2, 0,
      ["Latest run watermark", <WatermarkBadge value={row.snapshot_movement_state} />, "The latest run's watermark progress differs from the latest eligible check. See Dataflow summary for this run's time and status."],
    );
  }
  const groups: { title: string; scope: string; fields: FreshnessField[] }[] = [
    { title: "Latest", scope: "latest", fields: latest },
    { title: "Aggregate", scope: "aggregate", fields: aggregate },
  ];
  return (
    <section className="monitoring-job-group-card monitoring-registry-freshness">
      <span>Freshness</span>
      {groups.map(({ title, scope, fields }) => (
        <div className="monitoring-registry-freshness-group" data-evidence-scope={scope} key={scope}>
          <h5>{title}</h5>
          <dl>{fields.map(([label, value, provenance]) => <Help as="div" key={label} title={provenance}><dt>{label}</dt><dd>{value}</dd></Help>)}</dl>
        </div>
      ))}
    </section>
  );
}

function freshnessStateLabel(value: unknown, labels: Record<string, string> = {}) {
  const state = String(value ?? "").trim().toLowerCase();
  if (!state || state === "unknown" || state === "unk") return "Could not determine";
  return labels[state] ?? (state.charAt(0).toUpperCase() + state.slice(1).replace(/_/g, " "));
}

function watermarkIssueLabel(row: Record<string, unknown>) {
  if (row.watermark_issue_state === "error") {
    const failures = ["load_error", "save_error", "error", "failed", "failure", "cancelled", "canceled"];
    const loadFailed = failures.includes(String(row.load_status ?? "").trim().toLowerCase());
    const saveFailed = failures.includes(String(row.save_status ?? "").trim().toLowerCase());
    return loadFailed && saveFailed ? "Load and save failed" : loadFailed ? "Load failed" : saveFailed ? "Save failed" : "Load or save failed";
  }
  return freshnessStateLabel(row.watermark_issue_state, { none: "None detected" });
}

function RegistryRuntime({
  timezoneName,
  analyticsGeneration,
  registryOrigin,
  rows,
  total,
  offset,
  limit,
  loading,
  error,
  sort,
  onRetry,
  onSort,
  onPageChange,
  onPageSizeChange,
  onOpenRun,
}: {
  timezoneName?: string | null;
  analyticsGeneration?: string | null;
  registryOrigin?: MonitoringRegistryOrigin | null;
  rows: Record<string, unknown>[];
  total: number;
  offset: number;
  limit: number;
  loading: boolean;
  error: string | null;
  sort: TableSort;
  onRetry?: () => void;
  onSort?: (sort: TableSort) => void;
  onPageChange?: (offset: number) => void;
  onPageSizeChange?: (limit: number) => void;
  onOpenRun?: (row: MonitoringRecord, reference?: MonitoringRegistryRunReference) => void;
}) {
  const openRuntimeRun = (row: Record<string, unknown>) => {
    const runRow = {
      ...row,
      dataflow_run_id: firstRuntimeValue(row, "latest_run_id", "dataflow_run_id"),
      _source_id: firstRuntimeValue(row, "latest_source_id", "_source_id"),
      log_session_id: firstRuntimeValue(row, "latest_log_session_id", "log_session_id"),
      _file_uri: firstRuntimeValue(row, "latest_file_uri", "_file_uri"),
      _file_kind: firstRuntimeValue(row, "latest_file_kind", "_file_kind"),
      _record_line: firstRuntimeValue(row, "latest_record_line", "_record_line"),
    };
    const reference = monitoringRegistryRunReference(runRow, analyticsGeneration, registryOrigin);
    if (reference) onOpenRun?.(runRow as MonitoringRecord, reference);
  };
  return (
    <section className="monitoring-detail-section monitoring-registry-runtime">
      <div className="monitoring-detail-section-header">
        <h3>Runtime</h3>
        <small>Observed joint runtime tuples in the current filters</small>
      </div>
      {error ? <div className="monitoring-registry-section-state" role="alert">Runtime combinations could not be loaded. {onRetry ? <button type="button" className="small-button" onClick={onRetry}>Retry</button> : null}</div> : null}
      <div className="monitoring-registry-runtime-table">
        <TablePager limit={limit} offset={offset} loadedRows={rows.length} totalRows={total} loading={loading} onPageChange={(value) => onPageChange?.(value)} onPageSizeChange={(value) => onPageSizeChange?.(value)} />
        {rows.length ? <DataTable
          rows={rows}
          compactNumbers
          compactNumberStyle="infix"
          columns={[
            { key: "platform_name", label: "Platform", sortable: true, autoFit: true, fillPriority: "normal", minWidth: 120, render: (runtime) => <Help title={runtimeText(runtime, "platform_name")}>{runtimeText(runtime, "platform_name")}</Help> },
            { key: "engine_name", label: "Engine", sortable: true, autoFit: true, fillPriority: "normal", minWidth: 120, render: (runtime) => <Help title={runtimeText(runtime, "engine_name")}>{runtimeText(runtime, "engine_name")}</Help> },
            { key: "metadata_provider_name", label: "Metadata provider", sortKey: "metadata_provider_name", sortable: true, autoFit: true, fillPriority: "normal", minWidth: 150, render: (runtime) => <Help title={runtimeText(runtime, "metadata_provider_name")}>{runtimeText(runtime, "metadata_provider_name")}</Help> },
            { key: "run_count", label: "Runs", sortable: true, autoFit: true, minWidth: 70, maxWidth: 110, render: (runtime) => <CompactNumberValue value={metricValue(runtime, "run_count")} fallback="unk" /> },
            { key: "latest_run_at", label: "Last run", sortable: true, autoFit: true, minWidth: 170, maxWidth: 260, measureValue: (runtime, timezone) => formatTimestamp(runtimeValue(runtime, "latest_run_at"), timezone), render: (runtime) => {
              const runId = runtimeValue(runtime, "latest_run_id");
              const reference = monitoringRegistryRunReference({
                ...runtime,
                dataflow_run_id: firstRuntimeValue(runtime, "latest_run_id", "dataflow_run_id"),
                _source_id: firstRuntimeValue(runtime, "latest_source_id", "_source_id"),
                log_session_id: firstRuntimeValue(runtime, "latest_log_session_id", "log_session_id"),
                _file_uri: firstRuntimeValue(runtime, "latest_file_uri", "_file_uri"),
                _record_line: firstRuntimeValue(runtime, "latest_record_line", "_record_line"),
               }, analyticsGeneration, registryOrigin);
              return <Help as="button" type="button" className="monitoring-registry-last-run" disabled={!reference} title={reference ? (runId ? `Open ${String(runId)}` : "Open last run") : "Run provenance unavailable"} onClick={(event) => { event.stopPropagation(); openRuntimeRun(runtime); }}>
                <span>{formatTimestamp(runtimeValue(runtime, "latest_run_at"), timezoneName)}</span>
                <small>{runId ? compactDrawerIdentifier(runId) : "run unk"}</small>
              </Help>;
            } },
          ]}
          maxRows={limit}
          offset={0}
          onRowClick={openRuntimeRun}
          sort={sort}
          onSort={onSort}
          fixedLayout
          className="monitoring-child-dataflows-table monitoring-registry-runtime-table-grid"
          timezoneName={timezoneName}
        /> : !loading && !error ? <p className="monitor-muted">No runtime combinations recorded.</p> : null}
        {loading && !rows.length ? <p className="monitor-muted">Loading runtime…</p> : null}
      </div>
    </section>
  );
}

function RegistryExecution({
  timezoneName, analyticsGeneration, registryOrigin, rows, total, offset, limit, sort, loading, error, onRetry,
  onSort, onPageChange, onPageSizeChange, onOpenRun, onOpenDataflow,
}: {
  timezoneName?: string | null;
  analyticsGeneration?: string | null;
  registryOrigin?: MonitoringRegistryOrigin | null;
  rows: MonitoringRecord[];
  total: number;
  offset: number;
  limit: number;
  sort: TableSort;
  loading: boolean;
  error: string | null;
  onRetry?: () => void;
  onSort?: (sort: TableSort) => void;
  onPageChange?: (offset: number) => void;
  onPageSizeChange?: (limit: number) => void;
  onOpenRun?: (row: MonitoringRecord, reference?: MonitoringRegistryRunReference) => void;
  onOpenDataflow?: (row: MonitoringRecord) => void;
}) {
  const openExecutionRun = (row: MonitoringRecord) => {
    const reference = monitoringRegistryRunReference(row, analyticsGeneration, registryOrigin);
    if (onOpenRun && reference) {
      onOpenRun(row, reference);
      return;
    }
    if (!onOpenRun) onOpenDataflow?.(row);
  };
  return (
    <section className="monitoring-detail-section monitoring-registry-execution">
      <h3>Execution</h3>
      {error ? <div className="monitoring-registry-section-state" role="alert">Execution history could not be loaded. {onRetry ? <button type="button" className="small-button" onClick={onRetry}>Retry</button> : null}</div> : null}
      <div className="monitoring-registry-execution-table">
        <TablePager limit={limit} offset={offset} loadedRows={rows.length} totalRows={total} loading={loading} onPageChange={(value) => onPageChange?.(value)} onPageSizeChange={(value) => onPageSizeChange?.(value)} />
        {rows.length ? <DataTable
          rows={rows}
          compactNumbers
          compactNumberStyle="infix"
          columns={[
            { key: "event_time", label: "Time", sortable: true, width: 170, render: (run) => <Help title={String(run.event_time ?? run.start_time ?? "")}>{formatTimestamp(run.event_time ?? run.start_time ?? run.end_time, timezoneName)}</Help> },
            { key: "phase_health", label: "Phase", sortable: true, width: 100, render: (run) => <DataflowPhaseCell row={run} unknownLabel="unk" /> },
            { key: "duration_seconds", label: "Duration", sortable: true, autoFit: true, minWidth: 68, maxWidth: 104, render: (run) => <StoryDuration value={run.duration_seconds} /> },
            { key: "status", label: "Status", sortable: true, autoFit: true, minWidth: 68, maxWidth: 120, render: (run) => <StatusCell row={run} /> },
            { key: "source_rows_read", label: "Rows", sortable: true, autoFit: true, minWidth: 82, maxWidth: 140, render: (run) => <span className="monitoring-registry-run-rows"><CompactNumberValue value={metricValue(run, "source_rows_read")} fallback="unk" /> | <CompactNumberValue value={metricValue(run, "destination_rows_written")} fallback="unk" /></span> },
            { key: "message", label: "Message", sortable: true, width: 200, fillPriority: "last", className: "monitoring-child-issue-column", render: (run) => <IssueCell row={run} /> },
          ]}
          maxRows={limit}
          offset={0}
          onRowClick={openExecutionRun}
          sort={sort}
          onSort={onSort}
          fixedLayout
          className="monitoring-child-dataflows-table monitoring-registry-run-table"
          timezoneName={timezoneName}
        /> : !loading && !error ? <p className="monitor-muted">No canonical runs recorded.</p> : null}
        {loading && !rows.length ? <p className="monitor-muted">Loading execution…</p> : null}
      </div>
    </section>
  );
}

function RegistryLazySection({ title, section, state, onOpen, children }: {
  title: string;
  section: RegistrySection;
  state?: RegistrySectionState;
  onOpen?: (section: RegistrySection) => void;
  children: ReactNode;
}) {
  return (
    <StoryDisclosure title={title} className="monitoring-registry-collapsed-section" onOpen={() => onOpen?.(section)}>
      {() => <>
        {state?.error ? <div className="monitoring-registry-section-state" role="alert">{state.ready ? "This section could not be refreshed." : "This section could not be loaded."} {state.retry ? <button type="button" className="small-button" onClick={state.retry}>Retry</button> : null}</div>
          : state?.loading ? <div className="monitoring-registry-section-state" role="status">{state.ready ? "Refreshing" : "Loading"} {title.toLowerCase()}…</div> : null}
        {state?.empty ? <p className="monitor-muted">No {title.toLowerCase()} evidence recorded for this snapshot.</p> : null}
        {!state?.empty && (!state || state.ready || (!state.loading && !state.error)) ? children : null}
      </>}
    </StoryDisclosure>
  );
}

function RegistrySnapshotProvenance({ row, fallbackRow, timezoneName }: { row: Record<string, unknown>; fallbackRow: Record<string, unknown>; timezoneName?: string | null }) {
  const sectionProfile = profileSnapshot(row);
  const profile = Object.keys(sectionProfile).length ? sectionProfile : profileSnapshot(fallbackRow);
  const name = profile.dataflow_name;
  return (
    <section className="monitoring-registry-snapshot-provenance">
      <div>
        <span>Configuration snapshot</span>
        <strong>{hasValue(name) ? String(name) : "Dataflow profile"}</strong>
      </div>
      <small>{profileProvenance(profile, timezoneName)}</small>
    </section>
  );
}

function RegistryRunId({ value }: { value: unknown }) {
  return hasValue(value) ? (
    <DataflowCopyValue label="Run ID" value={value}>
      <Help title={String(value)}>{compactDrawerIdentifier(value)}</Help>
    </DataflowCopyValue>
  ) : <span>unk</span>;
}

function RegistryBottleneck({ row }: { row: Record<string, unknown> }) {
  const phase = String(row.performance_bottleneck_phase ?? "").trim().toLowerCase();
  if (!phase || phase === "unknown" || phase === "unk") return <span>unk</span>;
  return <span className={`phase-text-${phase}`}>{phase[0].toUpperCase() + phase.slice(1)}</span>;
}

function RegistryPhaseContribution({ row, average = false }: { row: Record<string, unknown>; average?: boolean }) {
  const title = average ? "Avg contribution" : "Contribution";
  const durationKey = average ? "avg_duration_seconds" : "duration_seconds";
  const explanation = average
    ? "Average phase seconds per run in the current filters. Bar widths reflect each phase's share; all phases use the same complete-timing run population."
    : "Shares of summed phase durations across filtered eligible runs; durations are totals.";
  const phases = [
    ["source", "S", row.performance_source_contribution, row[`performance_source_${durationKey}`]],
    ["transform", "T", row.performance_transform_contribution, row[`performance_transform_${durationKey}`]],
    ["destination", "D", row.performance_destination_contribution, row[`performance_destination_${durationKey}`]],
    ["overhead", "O", row.performance_overhead_contribution, row[`performance_overhead_${durationKey}`]],
  ] as const;
  const measured = phases.filter(([, , percentValue, durationValue]) => hasValue(percentValue) || hasValue(durationValue));
  if (!measured.length) return (
    <div className="dataflow-insight-phase monitoring-registry-insight-contribution monitoring-registry-phase-insight">
      <Help as="h5" title={explanation}>{title}</Help>
      <span>{average ? "Timing unavailable or incomplete" : "unk"}</span>
    </div>
  );
  return (
    <div className="dataflow-insight-phase monitoring-registry-insight-contribution monitoring-registry-phase-insight">
      <Help as="h5" title={explanation}>{title}</Help>
      <div className="dataflow-insight-phase-bar monitoring-registry-phase-bar" role="img" aria-label={`${title} by phase`}>
        {phases.map(([phase, , value]) => {
          const width = Number(value);
          return Number.isFinite(width) && width > 0 ? <i key={phase} className={`phase-${phase}`} style={{ flexBasis: `${Math.min(100, width)}%` }} /> : null;
        })}
      </div>
      <Help as="div" className="dataflow-insight-phase-legend monitoring-registry-phase-legend" title={explanation}>
        {phases.map(([phase, label, percentValue, durationValue]) => (
          <span key={phase} data-phase={phase}>
            <span className="dataflow-insight-phase-label"><i className={`phase-${phase}`} aria-hidden="true" /><span aria-label={phase}>{label}</span></span>
            <span className="dataflow-insight-phase-values">{!average ? <span className={`phase-text-${phase} dataflow-insight-phase-share`}>{percent(percentValue)}</span> : null}<span className="dataflow-insight-phase-duration">{durationValue == null ? "unk" : duration(durationValue)}</span></span>
          </span>
        ))}
      </Help>
    </div>
  );
}

function MovementCounts({ row }: { row: Record<string, unknown> }) {
  return <span className="monitoring-registry-movement-counts">
    {([["adv", row.watermark_advanced_runs], ["unch", row.watermark_unchanged_runs], ["chg", row.watermark_changed_runs], ["emp", row.watermark_empty_runs], ["init", row.watermark_initialized_runs], ["unk", row.watermark_unknown_runs]] as const)
      .map(([label, value]) => <span key={label}>{label} {metricText(value)}</span>)}
  </span>;
}

function PerformanceCandidateSummary({ row }: { row: Record<string, unknown> }) {
  const count = Number(row.performance_candidate_count ?? 0);
  if (!Number.isFinite(count) || count <= 0) return <span>None</span>;
  const reasons = Array.isArray(row.performance_candidate_reasons)
    ? row.performance_candidate_reasons.filter((value): value is string => typeof value === "string")
    : [];
  return <span>{count}{reasons.length ? ` · ${reasons.join(" · ")}` : " candidate runs"}</span>;
}

function VolumeCandidateSummary({ row }: { row: Record<string, unknown> }) {
  const priority = Number(row.volume_candidate_priority ?? 0);
  if (!Number.isFinite(priority) || priority <= 0) return <span>None</span>;
  return <span>{metricText(row.volume_candidate_reason)} · {priority.toFixed(3)}× P95</span>;
}

function VolumeCandidateSignals({ value }: { value: unknown }) {
  const signals = Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => item !== null && typeof item === "object" && !Array.isArray(item))
    : [];
  if (!signals.length) return <span>None</span>;
  return <span className="monitoring-registry-volume-signals">
    {signals.map((signal, index) => <span key={`${String(signal.kind ?? index)}-${index}`}>
      <b>{metricText(signal.label)}</b>
      <span><CompactNumberValue value={metricValue(signal, "value")} fallback="unk" /> / P95 <CompactNumberValue value={metricValue(signal, "threshold")} fallback="unk" /> · {metricText(signal.ratio)}×</span>
    </span>)}
  </span>;
}

function profileSnapshot(row: Record<string, unknown>) {
  const value = row.profile_snapshot;
  return isRecord(value) ? value : {};
}

function profileProvenance(profile: Record<string, unknown>, timezoneName?: string | null) {
  const runId = profile.dataflow_run_id;
  const recordedAt = profile.event_time;
  const sourceId = profile._source_id;
  const parts = [runId ? `run ${compactDrawerIdentifier(runId)}` : "run unk", recordedAt ? formatTimestamp(recordedAt, timezoneName) : "time unk"];
  if (sourceId != null) parts.push(`source ${String(sourceId)}`);
  return parts.join(" · ");
}

function recentRunRows(row: Record<string, unknown>) {
  const value = row.recent_runs;
  if (!Array.isArray(value)) return [];
  return value
    .filter(isRecord)
    .slice(0, 5)
    .sort(compareRecentRuns);
}

function RecentRunStrip({ rows, timezoneName, analyticsGeneration, registryOrigin, onOpenRun }: { rows: Record<string, unknown>[]; timezoneName?: string | null; analyticsGeneration?: string | null; registryOrigin?: MonitoringRegistryOrigin | null; onOpenRun?: (row: MonitoringRecord, reference?: MonitoringRegistryRunReference) => void }) {
  if (!rows.length) return <span className="monitor-muted">No recent runs</span>;
  return <span className="monitoring-registry-recent-runs" aria-label="Latest five runs">
    {rows.map((run, index) => {
      const runId = run.dataflow_run_id;
      const at = run.event_time ?? run.observed_at;
      const displayTime = formatTimestamp(at, timezoneName);
      const status = run.status ?? "unknown";
      const reference = monitoringRegistryRunReference(run, analyticsGeneration, registryOrigin);
      return <Help as="button"
        key={`${String(runId ?? "run")}-${index}`}
        type="button"
        className="monitoring-registry-recent-run"
        disabled={!reference}
        title={`${String(status)} · ${displayTime} · ${runId ? String(runId) : "run unknown"}`}
        aria-label={`${String(status)} run ${runId ? String(runId) : "unknown"} at ${displayTime}`}
        onClick={() => reference && onOpenRun?.(run as MonitoringRecord, reference)}
      >
        <RecentRunStatus status={status} title={`${String(status)} · ${displayTime}`} />
      </Help>;
    })}
  </span>;
}

function compareRecentRuns(left: Record<string, unknown>, right: Record<string, unknown>) {
  for (const field of ["event_time", "observed_at"] as const) {
    const leftTime = parseRecentRunTime(left[field]);
    const rightTime = parseRecentRunTime(right[field]);
    if (leftTime === null && rightTime !== null) return -1;
    if (leftTime !== null && rightTime === null) return 1;
    if (leftTime !== null && rightTime !== null && leftTime !== rightTime) return leftTime - rightTime;
  }
  return 0; // Preserve the backend's canonical order for timestamp ties.
}

function parseRecentRunTime(value: unknown) {
  if (!hasValue(value)) return null;
  const parsed = Date.parse(String(value));
  return Number.isFinite(parsed) ? parsed : null;
}

function RecentRunStatus({ status, title }: { status: unknown; title?: string }) {
  const fullStatus = hasValue(status) ? String(status) : "unknown";
  const normalized = fullStatus.trim().toLowerCase() || "unknown";
  const presentation = lifecycleStatusPresentation(normalized);
  return (
    <Help
      className={`status-pill status-${normalized} monitoring-registry-recent-status`}
      style={presentation ? { color: presentation.textColor, backgroundColor: presentation.pillBackground } : undefined}
      title={title ?? fullStatus}
    >
      {recentStatusLabel(normalized)}
    </Help>
  );
}

function recentStatusLabel(status: string) {
  switch (status) {
    case "succeeded": return "OK";
    case "failed": return "ERR";
    case "skipped": return "SKIP";
    case "running": return "RUN";
    case "pending": return "WAIT";
    default: return status.slice(0, 4).toUpperCase() || "UNK";
  }
}

function runtimeValue(row: Record<string, unknown>, key: string) {
  return row[key];
}

function firstRuntimeValue(row: Record<string, unknown>, primary: string, fallback: string) {
  return Object.prototype.hasOwnProperty.call(row, primary) ? row[primary] : row[fallback];
}

function runtimeText(row: Record<string, unknown>, key: string) {
  const value = runtimeValue(row, key);
  return hasValue(value) ? String(value) : "unk";
}

function isPositiveMetric(value: unknown) {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function formatTimestamp(value: unknown, timezoneName?: string | null) {
  return hasValue(value) ? formatTimestampForDisplay(String(value), timezoneName, "unk") : "unk";
}

function duration(value: unknown) {
  if (!hasValue(value)) return "unk";
  return formatSeconds(Number(value));
}

function percent(value: unknown) {
  return hasValue(value) ? `${metricText(value)}%` : "unk";
}

function coverage(measured: unknown, eligible: unknown) {
  return `${metricText(measured)} / ${metricText(eligible)}`;
}

function metricText(value: unknown) {
  return hasValue(value) ? String(value) : "unk";
}
