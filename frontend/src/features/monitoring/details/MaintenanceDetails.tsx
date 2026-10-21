import { useEffect, useState, type ReactNode } from "react";
import { DestinationRecordedState, DestinationStateProvenance } from "./DestinationRecordedState";
import { DestinationSignalList } from "../destinationSignalsPresentation";
import { monitoringDimensionLabel } from "../monitoringDimensions";
import type { MonitoringRecord } from "../../../shared/api/domainTypes";
import { DataTable, formatSeconds, num, type TableSort } from "../../../shared/components/DataTable";
import { StatusPill } from "../../../shared/components/StatusPill";
import { Help } from "../../../shared/components/Help";
import { formatTimestampForDisplay } from "../../../shared/time";
import { CompactBytesValue, CompactNumberValue, TablePager, formatCompact, formatTooltipBytes, formatTooltipNumber, formatPercent, metricValue, toApproximateNumber } from "../components/monitoringPresentation";
import { monitoringConnectionStageClass } from "../monitoringStagePresentation";
import { GroupedDetailCard, IssueCell, hasValue, type DetailRow } from "./detailPrimitives";
import { compareMetricValues } from "../../../shared/lib/numberFormat";
import {
  destinationFormatLabel,
  destinationFormatUnknownRuns,
  destinationFormatVariants,
  destinationLatestFormat,
  DESTINATION_WORKLOAD_SCOPE,
  destinationLayoutFieldTooltip,
  destinationLayoutInvalidReason,
  maintenanceEvidenceLabel,
  maintenanceEvidenceState,
  presentDestination,
  presentDestinationLayout,
  type DestinationLayoutPresentation,
  type MaintenanceEvidenceState,
} from "../destinationPresentation";
import { DataflowCopyValue } from "./DataflowCopyValue";

export function MaintenanceDetailSections({
  row,
  relatedDataflows,
  total,
  loading,
  offset,
  limit,
  sort,
  onSort,
  onPageChange,
  onPageSizeChange,
  onOpenDataflow,
  timezoneName,
  detailAvailability = "ready",
  onRetryDetail,
  historyError,
  onRetryHistory,
}: {
  row: Record<string, unknown>;
  relatedDataflows: MonitoringRecord[];
  total: number;
  loading: boolean;
  offset: number;
  limit: number;
  sort: TableSort;
  onSort: (sort: TableSort) => void;
  onPageChange: (offset: number) => void;
  onPageSizeChange: (limit: number) => void;
  onOpenDataflow?: (row: MonitoringRecord) => void;
  timezoneName?: string | null;
  detailAvailability?: "loading" | "error" | "not-found" | "ready";
  onRetryDetail?: () => void;
  historyError?: string | null;
  onRetryHistory?: () => void;
}) {
  const contributingDataflows = Array.isArray(row.upstream_dataflows) ? row.upstream_dataflows as MonitoringRecord[] : [];
  const identity = presentDestination(row);
  const layout = presentDestinationLayout(row);
  const formatVariants = destinationFormatVariants(row);
  const formatUnknownRuns = destinationFormatUnknownRuns(row);
  const maintenanceState = maintenanceEvidenceState(row);
  const latestFormat = destinationLatestFormat(row);
  const layoutInvalidReason = destinationLayoutInvalidReason(layout);
  const layoutConflict = conflictLabel(layout.conflicts);

  const [activityTab, setActivityTab] = useState<"dataflows" | "runs">("dataflows");
  useEffect(() => { setActivityTab("dataflows"); }, [row.target]);
  return (
    <>
      <section className="monitoring-detail-section destination-drawer-overview">
        <h3>Overview &amp; signals</h3>
        <div className="destination-drawer-overview-grid">
          <GroupedDetailCard title="Activity summary" rows={[
            ["Runs", <CompactNumberValue value={firstMetric(row, "run_count")} fallback="—" />],
            ...(["succeeded", "failed", "skipped", "running", "pending", "unknown"] as const).map(status => [status.charAt(0).toUpperCase()+status.slice(1), <span className={`destination-drawer-status is-${status}`}><CompactNumberValue value={firstMetric(row,status)} fallback="—" /></span>] as DetailRow),
            ["Dataflows", <CompactNumberValue value={firstMetric(row,"contributing_dataflow_count")} fallback="—" />],
            ["Latest operation", latestCellValue(row, timezoneName)],
            ["Operation", operationName(firstText(row, "root_operation_type", "operation_type"))],
            ["Destination phase", <DestinationPhaseValue row={row} />],
          ]} timezoneName={timezoneName} showEmpty />
          <div className="destination-drawer-signals"><h4>Signals</h4><DestinationSignalList row={row} /></div>
        </div>
      </section>

      <section className="monitoring-detail-section destination-drawer-object">
        <h3>Latest recorded state</h3>
        {detailAvailability !== "ready" ? <DestinationDetailState availability={detailAvailability} onRetry={onRetryDetail} /> : <DestinationRecordedState row={row} timezoneName={timezoneName} />}
      </section>

      <section className="monitoring-detail-section destination-drawer-workload">
        <h3>Workload &amp; changes</h3>
        <Help as="small" className="destination-drawer-caption" title={DESTINATION_WORKLOAD_SCOPE}>Totals within the filters. Read is source rows consumed by these runs; Written is reported workload.</Help>
        <DestinationWorkload row={row} availability={detailAvailability} />
        <details className="destination-drawer-disclosure"><summary>Row changes &amp; measurement details</summary>
          {detailAvailability !== "ready" ? <DestinationDetailState availability={detailAvailability} onRetry={onRetryDetail} /> : <GroupedDetailCard title="Qualified physical changes" rows={[
            ["Rows written", <SummaryMetricValue row={row} metric="rows_written" />],
            ["Inserted", <SummaryMetricValue row={row} metric="rows_inserted" />],
            ["Updated", <SummaryMetricValue row={row} metric="rows_updated" />],
            ["Deleted", <SummaryMetricValue row={row} metric="rows_deleted" />],
            ["Net bytes (paired reports)", <SummaryMetricValue row={row} metric="net_bytes" bytes signed measuredKey="net_bytes_paired_runs" />],
          ]} showEmpty />}
        </details>
      </section>

      <section className="monitoring-detail-section destination-drawer-runtime">
        <h3>Runtime</h3>
        {detailAvailability !== "ready" ? <DestinationDetailState availability={detailAvailability} onRetry={onRetryDetail} /> : <>
          <GroupedDetailCard title="Root operation runtime" rows={[
            ["Total", runtimeSummary(row)],
            ["Avg", durationValue(row,"avg_duration_seconds")],
            ["P95", durationValue(row,"p95_duration_seconds")],
          ]} showEmpty />
          <details className="destination-drawer-disclosure"><summary>Destination phase runtime</summary>
            <GroupedDetailCard title="Recorded destination phase" rows={[
              ["Total", durationValue(row,"destination_duration_seconds")],
              ["Avg", durationValue(row,"avg_destination_duration_seconds")],
              ["Measured reports", <span>{formatTooltipNumber(firstMetric(row,"destination_duration_measured_runs"))} / {formatTooltipNumber(firstMetric(row,"run_count"))}</span>],
            ]} showEmpty />
          </details>
        </>}
      </section>

      <section className="monitoring-detail-section monitoring-destination-layout-section">
        <div className="monitoring-detail-section-header"><h3>Write layout</h3><small>{layoutOriginSummary(layout)}</small></div>
        <div className="monitoring-destination-layout-grid">
          <GroupedDetailCard title="Latest recorded configuration" rows={[
            ["Format", layoutFieldValue(layout,"format",latestFormat ?? "—")],
            ["Write mode", layoutFieldValue(layout,"load_type",operationName(layout.load_type ?? ""))],
            ["Modes in filter", Array.isArray(row.load_type_variants) ? row.load_type_variants.join(", ") || "—" : "—"],
            ["Formats in filter", formatVariants.length ? formatVariants.map(destinationFormatLabel).join(", ") : "—"],
            ["Recorded at", layout.observation_time,"layout_observation_time"],
            ...(layoutInvalidReason ? [["Configuration note",layoutInvalidReason] as DetailRow] : []),
            ...(layoutConflict ? [["Conflicts",layoutConflict] as DetailRow] : []),
          ]} timezoneName={timezoneName} showEmpty />
          <PartitionLayoutBox layout={layout} />
        </div>
        <small className="destination-drawer-caption">Configured partitions; physical folders and partition inventory have not been checked.</small>
      </section>

      <section className="monitoring-detail-section destination-drawer-maintenance">
        <h3>Maintenance</h3>
        {maintenanceState === "recorded" ? <>
          <GroupedDetailCard title="Recorded attempts" rows={[
            ["Latest attempt", timestampValue(row,"latest_maintenance_time",timezoneName)],
            ["Latest outcome", <StatusPill status={statusName(firstText(row,"latest_maintenance_outcome"))} />],
            ["Attempts", <CompactNumberValue value={firstMetric(row,"maintenance_run_count")} fallback="—" />],
            ["Confirmed successes", detailAvailability === "ready" ? <CompactNumberValue value={firstMetric(row,"maintenance_qualified_success_runs")} fallback="—" /> : detailAvailability === "loading" ? "Loading…" : "Unavailable"],
            ["Last confirmed success", detailAvailability === "ready" ? timestampValue(row,"latest_qualified_successful_maintenance_time",timezoneName) : detailAvailability === "loading" ? "Loading…" : "Unavailable"],
            ["Files removed", detailAvailability === "ready" ? <SummaryMetricValue row={row} metric="maintenance_files_removed" denominatorKey="maintenance_run_count" /> : detailAvailability === "loading" ? "Loading…" : "Unavailable"],
            ["Bytes removed", detailAvailability === "ready" ? <SummaryMetricValue row={row} metric="maintenance_bytes_reclaimed" bytes denominatorKey="maintenance_run_count" /> : detailAvailability === "loading" ? "Loading…" : "Unavailable"],
          ]} showEmpty />
          <details className="destination-drawer-disclosure"><summary>Outcomes &amp; efficiency</summary>
            {detailAvailability !== "ready" ? <DestinationDetailState availability={detailAvailability} onRetry={onRetryDetail} /> : <GroupedDetailCard title="Maintenance details" rows={maintenanceRows(row)} timezoneName={timezoneName} showEmpty />}
          </details>
        </> : <Help as="p" className="destination-drawer-empty" title={maintenanceAbsenceNote(maintenanceState)}>{maintenanceState === "not_recorded" ? "No maintenance recorded in the current filters." : maintenanceEvidenceLabel(maintenanceState)}</Help>}
      </section>

      <section className="monitoring-detail-section destination-drawer-activity">
        <div className="monitoring-detail-section-header"><h3>Activity</h3><div className="destination-drawer-tabs" role="tablist" aria-label="Destination activity" onKeyDown={event => {
          if (["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) { event.preventDefault();const next = event.key === "Home" ? "dataflows" : event.key === "End" ? "runs" : activityTab === "dataflows" ? "runs" : "dataflows";setActivityTab(next);document.getElementById(`destination-${next}-tab`)?.focus(); }
        }}>
          <button type="button" role="tab" id="destination-dataflows-tab" aria-controls="destination-dataflows-panel" tabIndex={activityTab === "dataflows" ? 0 : -1} aria-selected={activityTab === "dataflows"} onClick={() => setActivityTab("dataflows")}>Dataflows</button>
          <button type="button" role="tab" id="destination-runs-tab" aria-controls="destination-runs-panel" tabIndex={activityTab === "runs" ? 0 : -1} aria-selected={activityTab === "runs"} onClick={() => setActivityTab("runs")}>Runs</button>
        </div></div>
        {activityTab === "dataflows" ? <div id="destination-dataflows-panel" role="tabpanel" aria-labelledby="destination-dataflows-tab">      <section className="monitoring-detail-section">
        <div className="monitoring-detail-section-header monitoring-child-dataflows-header">
          <h3>Contributing dataflows</h3>
          <small><CompactNumberValue value={firstMetric(row, "contributing_dataflow_count")} /> dataflows · <CompactNumberValue value={firstMetric(row, "run_count")} /> filtered runs{detailAvailability === "ready" && contributingDataflows.length < (toApproximateNumber(firstMetric(row, "contributing_dataflow_count")) ?? contributingDataflows.length) ? " · first 100 shown" : ""}</small>
        </div>
        {detailAvailability !== "ready" ? <DestinationDetailState availability={detailAvailability} onRetry={onRetryDetail} /> : (
          <DataTable
            rows={contributingDataflows}
            compactNumbers
            compactNumberStyle="infix"
            columns={[
              { key: "dataflow_name", label: "Dataflow", sortable: true, minWidth: 180, fillPriority: "normal", render: (item) => <MaintenanceDataflowCell row={item} /> },
              { key: "source_name", label: "Source", sortable: true, minWidth: 150, fillPriority: "last", render: (item) => <MaintenanceSourceCell row={item} /> },
              { key: "operation_type", label: "Operation", sortable: true, autoFit: true, minWidth: 48, render: item => operationName(firstText(item,"operation_type")) },
              { key: "latest_status", label: "Latest", sortable: true, sortKey: "latest_time", minWidth: 164, maxWidth: 210, render: (item) => <MaintenanceLatestCell row={item} timezoneName={timezoneName} /> },
              { key: "run_count", label: "Runs", sortable: true, autoFit: true, minWidth: 80, maxWidth: 120, render: (item) => <CompactNumberValue value={firstMetric(item, "run_count")} /> },
            ]}
            maxRows={Math.max(1, contributingDataflows.length)}
            fixedLayout
            timezoneName={timezoneName}
            className="monitoring-child-dataflows-table monitoring-maintenance-upstream-table"
          />
        )}
      </section>

</div> : <div id="destination-runs-panel" role="tabpanel" aria-labelledby="destination-runs-tab">      <section className="monitoring-detail-section">
        <div className="monitoring-detail-section-header monitoring-freshness-runs-header monitoring-child-dataflows-section-header">
          <div className="monitoring-child-dataflows-header"><h3>Runs</h3><small>{historyError ? "Run history unavailable" : loading ? "Loading destination activity…" : `${total} filtered operations`}</small></div>
          {!loading && !historyError ? <TablePager limit={limit} offset={offset} loadedRows={relatedDataflows.length} totalRows={total} loading={loading} onPageChange={onPageChange} onPageSizeChange={onPageSizeChange} /> : null}
        </div>
        {historyError ? <div className="destination-drawer-empty" role="alert">Unable to load destination runs. <button type="button" onClick={onRetryHistory}>Retry</button></div> : loading ? <MaintenanceRelatedLoading /> : (
          <DataTable
            rows={relatedDataflows}
            compactNumbers
            compactNumberStyle="infix"
            columns={[
              { key: "dataflow_name", label: "Dataflow", sortable: true, minWidth: 180, fillPriority: "normal", render: (item) => <MaintenanceDataflowCell row={item} /> },
              { key: "source_name", label: "Source", sortable: true, autoFit: true, minWidth: 48, maxWidth: Number.POSITIVE_INFINITY, render: (item) => <MaintenanceSourceCell row={item} />, measureSelectors: [".maintenance-source-cell > strong", ".maintenance-source-cell > small"], measureValue: item => { const source = maintenanceSourceParts(item); return [source.connection, source.object]; } },
              { key: "event_time", label: "Run", sortable: true, sortKey: "event_time", autoFit: true, minWidth: 48, maxWidth: Number.POSITIVE_INFINITY, render: (item) => <MaintenanceLatestCell row={item} timezoneName={timezoneName} />, measureSelectors: [".status-pill", ".freshness-run-stack-cell > small"], measureValue: item => [statusName(firstText(item,"status","latest_status")), formatTimestampForDisplay(firstText(item,"event_time","start_time"),timezoneName,"—")] },
              { key: "duration_seconds", label: "Duration", sortable: true, autoFit: true, minWidth: 48, maxWidth: Number.POSITIVE_INFINITY, render: (item) => formatSeconds(num(item, "duration_seconds")) },
              { key: "destination_rows_written", label: "Workload / changes", sortable: true, sortKey: "destination_rows_written", autoFit: true, minWidth: 48, maxWidth: Number.POSITIVE_INFINITY, render: (item) => <MaintenanceRunVolumeCell row={item} />, measureValue: item => [`${formatCompact(metricValue(item,"destination_rows_written"))} rows`, `${formatCompact(metricValue(item,"destination_files_added"))} / ${formatCompact(metricValue(item,"destination_files_removed"))} files`] },
              { key: "message", label: "Message", sortable: true, minWidth: 140, fillPriority: "last", className: "monitoring-child-issue-column", render: (item) => <IssueCell row={item} /> },
            ]}
            maxRows={limit}
            offset={0}
            onRowClick={onOpenDataflow}
            sort={sort}
            onSort={onSort}
            sortMode="server"
            fixedLayout
            timezoneName={timezoneName}
            className="monitoring-child-dataflows-table monitoring-maintenance-run-history-table"
          />
        )}
      </section>
</div>}
      </section>

      <details className="monitoring-detail-section destination-drawer-disclosure destination-drawer-technical"><summary>Technical details</summary>
        <GroupedDetailCard title="Identity & location" rows={[
          ["Asset ID", <DataflowCopyValue label="destination asset ID" value={identity.id} />],
          [identity.locatorLabel,<DataflowCopyValue label={`destination ${identity.locatorLabel.toLowerCase()}`} value={identity.locator} />],
          ...(identity.path && identity.path !== identity.locator ? [["Storage path",<DataflowCopyValue label="destination storage path" value={identity.path} />] as DetailRow] : []),
          ["Resolution",firstText(row,"identity_status") || "unknown"],["Reason",firstText(row,"identity_reason")],
        ]} showEmpty />
        <GroupedDetailCard title="Configuration provenance" rows={[
          ["Format origin",layout.origins.format],["Mode origin",layout.origins.load_type],
          ["Partition origin",layout.origins.partition_columns],["Expression origin",layout.origins.partition_expressions],
          ["Metadata revision",layout.metadata_revision],["Metadata sources",metadataSourceLabel(layout)],
          ["Unknown format reports",formatUnknownRuns],["Configuration run",layout.dataflow_run_id],
        ]} showEmpty />
        <DestinationStateProvenance row={row} />
      </details>
    </>
  );
}

function DestinationDetailState({ availability, onRetry }: { availability: "loading" | "error" | "not-found" | "ready"; onRetry?: () => void }) {
  return <p className="destination-drawer-empty" role={availability === "error" ? "alert" : "status"}>
    {availability === "loading" ? "Loading recorded details…" : availability === "error" ? <>Unable to load recorded details. <button type="button" onClick={onRetry}>Retry</button></> : "No recorded details in the current filters."}
  </p>;
}

function DestinationWorkload({ row, availability }: { row: Record<string, unknown>; availability: "loading" | "error" | "not-found" | "ready" }) {
  const groups = [
    { title: "Rows", fields: [{ label: "Read", key: "workload_rows_read", physical: false }, { label: "Written", key: "workload_rows_written", physical: false }] },
    { title: "Files", fields: [{ label: "Added", key: "files_added", physical: true }, { label: "Removed", key: "files_removed", physical: true }] },
    { title: "Bytes", fields: [{ label: "Added", key: "bytes_added", physical: true }, { label: "Removed", key: "bytes_removed", physical: true }] },
  ];
  return <div className="destination-drawer-workload-grid">{groups.map(group => <div className="dataflow-workload-group" key={group.title}>
    <h4>{group.title}</h4>
    <dl className="destination-drawer-workload-fields">{group.fields.map(field => {
      const exact = firstMetric(row,field.key);const observed = firstMetric(row,`${field.key}_observed`);const value = exact ?? observed;
      const measured = firstMetric(row,`${field.key}_measured_runs`);const total = firstMetric(row,"run_count");
      const partial = value != null && (exact == null || measured != null && total != null && compareMetricValues(measured,total)<0);
      const average = availability === "ready" ? firstMetric(row,`${field.key}_avg_per_run`) : null;
      const tooltip = [`${field.label}: ${formatTooltipNumber(value)}`,`Coverage: ${formatTooltipNumber(measured)} / ${formatTooltipNumber(total)} filtered reports`,
        field.physical ? "Avg uses qualified reports with a recorded nonnegative value, including zero." : "Avg uses reports with a recorded nonnegative value, including zero; missing values excluded.",
        field.physical ? "Qualified physical changes; not current inventory." : DESTINATION_WORKLOAD_SCOPE].join("\n");
      const render = (amount: ReturnType<typeof metricValue>) => group.title === "Bytes" ? <CompactBytesValue value={amount} fallback="—" /> : <CompactNumberValue value={amount} fallback="—" />;
      return <div key={field.key}><dt>{field.label}</dt><Help as="dd" title={tooltip} className={field.physical && value != null && compareMetricValues(value,0)>0 ? `destination-drawer-effect is-${field.label.toLowerCase()}` : undefined}>
        <strong>{render(value)}{partial ? <sup aria-label="Partial measurements">*</sup> : null}</strong>
        <small>Avg {availability === "ready" ? render(average) : availability === "loading" ? "Loading…" : "Unavailable"}</small>
      </Help></div>;
    })}</dl>
  </div>)}</div>;
}

export function MaintenanceMetricValue({ tone, value }: { tone: "reclaim" | "files" | "warning"; value: ReactNode }) {
  return <span className={`monitoring-maintenance-metric-value is-${tone}`}>{value}</span>;
}

export function MaintenanceRelatedLoading() {
  return <div className="table-empty monitoring-maintenance-related-loading">Loading related evidence…</div>;
}

function maintenanceRows(row: Record<string, unknown>): DetailRow[] {
  return [
    ["Root succeeded", <span key="maintenance-succeeded" className="maintenance-outcome-count is-succeeded"><CompactNumberValue value={firstMetric(row, "maintenance_succeeded")} /></span>],
    ["Root failed", <span key="maintenance-failed" className="maintenance-outcome-count is-failed"><CompactNumberValue value={firstMetric(row, "maintenance_failed")} /></span>],
    ["Root skipped", <span key="maintenance-skipped" className="maintenance-outcome-count is-skipped"><CompactNumberValue value={firstMetric(row, "maintenance_skipped")} /></span>],
    ["Maintenance runtime", maintenanceRuntimeSummary(row)],
    ["Average runtime", durationValue(row, "maintenance_avg_duration_seconds")],
    ["P95 runtime", durationValue(row, "maintenance_p95_duration_seconds")],
    ["Bytes removed / second", <MaintenanceRateValue key="maintenance-reclaim-rate" row={row} metric="maintenance_bytes_reclaimed_per_second" bytesPerSecond />],
    ["Bytes / file removed", <MaintenanceRateValue key="maintenance-bytes-per-file" row={row} metric="maintenance_avg_bytes_per_file_removed" bytes />],
    ["No-change runs", firstMetric(row, "maintenance_no_op_runs")],
    ["No-change rate", <MaintenanceRateValue key="maintenance-no-op-rate" row={row} metric="maintenance_no_op_rate" percent />],
    ["No-change runtime share", <MaintenanceRateValue key="maintenance-no-op-runtime-share" row={row} metric="maintenance_no_op_runtime_share" percent />],
    ["Comparable runs", firstMetric(row, "maintenance_comparable_runs")],
    ["Comparable runtime", durationValue(row, "maintenance_comparable_duration_seconds")],
  ];
}

function layoutOriginSummary(layout: DestinationLayoutPresentation) {
  const origins = [layout.origins.format, layout.origins.load_type, layout.origins.partition_columns, layout.origins.partition_expressions]
    .filter((value): value is Exclude<typeof value, undefined> => Boolean(value && value !== "unknown"));
  if (!origins.length) return "Origins unknown";
  const unique = [...new Set(origins)];
  return unique.join(" + ");
}

function maintenanceAbsenceNote(state: MaintenanceEvidenceState) {
  if (state === "not_recorded") return "No maintenance activity recorded within the current filters. Maintenance support is not inferred from the format.";
  if (state === "not_applicable") return "Maintenance is explicitly declared not applicable.";
  return "Maintenance activity or declared capability is unavailable.";
}

function layoutFieldValue(
  layout: DestinationLayoutPresentation,
  field: "format" | "load_type",
  value: ReactNode,
) {
  const tooltip = destinationLayoutFieldTooltip(layout, field);
  return <Help title={tooltip ?? undefined}>{value}</Help>;
}

function metadataSourceLabel(layout: DestinationLayoutPresentation) {
  if (!layout.metadata_sources.length) return null;
  return layout.metadata_sources.map((source) => {
    const id = source.source_id ?? source.source ?? source.source_uri ?? source.uri;
    const revision = source.revision ?? source.metadata_revision;
    return revision ? `${String(id ?? "metadata")} @ ${String(revision)}` : String(id ?? "metadata");
  }).join(", ");
}

function conflictLabel(value: unknown) {
  const entries = conflictEntries(value);
  if (!entries.length) return null;
  const descriptions = entries.flatMap((entry) => {
    const field = safeConflictText(entry.field);
    const values = conflictValues(entry.values);
    if (!field && !values.length) return [];
    const label = conflictFieldLabel(field);
    if (!values.length) return [`Conflicting ${label} values`];
    const shown = values.slice(0, 3);
    const extra = values.length > shown.length ? ` (+${values.length - shown.length} more)` : "";
    return [`Conflicting ${label} values: ${shown.join(", ")}${extra}`];
  });
  if (descriptions.length) return descriptions.slice(0, 3).join("; ");
  const count = Array.isArray(value) ? value.length : 1;
  return `Conflicting layout value${count === 1 ? "" : "s"}`;
}

function conflictEntries(value: unknown): Array<{ field: unknown; values: unknown }> {
  if (Array.isArray(value)) {
    return value.flatMap((item) => {
      if (!item || typeof item !== "object" || Array.isArray(item)) return [];
      const record = item as Record<string, unknown>;
      return [{ field: record.field, values: record.values }];
    });
  }
  if (!value || typeof value !== "object") return [];
  const record = value as Record<string, unknown>;
  if (Object.prototype.hasOwnProperty.call(record, "field") || Object.prototype.hasOwnProperty.call(record, "values")) {
    return [{ field: record.field, values: record.values }];
  }
  return Object.entries(record)
    .filter(([field]) => field !== "metadata_sources")
    .map(([field, values]) => ({ field, values }));
}

function conflictValues(value: unknown) {
  const values = Array.isArray(value) ? value : value === null || value === undefined ? [] : [value];
  return values.flatMap((item) => {
    if (typeof item === "string" && item.trim()) return [item.trim().slice(0, 64)];
    if (typeof item === "number" && Number.isFinite(item)) return [String(item)];
    if (typeof item === "boolean") return [String(item)];
    return [];
  });
}

function safeConflictText(value: unknown) {
  if (typeof value !== "string") return "";
  return value.trim().slice(0, 64);
}

function conflictFieldLabel(field: string) {
  const labels: Record<string, string> = {
    format: "Format",
    load_type: "Load type",
    partition_columns: "Configured partitions",
    partition_expressions: "Partition expressions",
  };
  if (labels[field]) return labels[field];
  const normalized = field.replace(/^destination_/u, "").replace(/_/gu, " ").trim();
  return normalized ? normalized.charAt(0).toUpperCase() + normalized.slice(1) : "layout";
}

function PartitionLayoutBox({ layout }: { layout: DestinationLayoutPresentation }) {
  const columns = layout.partition_columns;
  const expressions = layout.partition_expressions;
  const invalidReason = destinationLayoutInvalidReason(layout, "partition_columns");
  const columnsTooltip = destinationLayoutFieldTooltip(layout, "partition_columns");
  const expressionsTooltip = destinationLayoutFieldTooltip(layout, "partition_expressions");
  const columnValue = columns === null
    ? <span className="monitoring-destination-layout-unknown">Unknown</span>
    : columns.length
      ? <span className="monitoring-destination-layout-values">{columns.map(column => <code key={column}>{column}</code>)}</span>
      : <span>Unpartitioned (configured)</span>;
  return (
    <div className="monitoring-destination-layout-partition-box">
      <div className="monitoring-destination-layout-box-heading">
        <Help as="h4" title={columnsTooltip ?? undefined}>Configured partitions</Help>
        <Help as="small" title={columnsTooltip ?? undefined}>{layout.origins.partition_columns ?? "unknown"}</Help>
      </div>
      <Help as="div" className="monitoring-destination-layout-partition-value" title={columnsTooltip ?? undefined}>{columnValue}</Help>
      {invalidReason ? <small className="monitoring-destination-layout-invalid-reason">{invalidReason}</small> : null}
      {expressions?.length ? <div className="monitoring-destination-layout-expression"><Help title={expressionsTooltip ?? undefined}>Expressions</Help><Help as="div" className="monitoring-destination-layout-values" title={expressionsTooltip ?? undefined}>{expressions.map(expression => <code key={expression}>{expression}</code>)}</Help></div> : null}
    </div>
  );
}

export function isMaintenanceRun(row: Record<string, unknown>) {
  const operationType = String(row.operation_type ?? "").toLowerCase();
  const destinationOperationType = String(row.destination_operation_type ?? "").toLowerCase();
  return operationType === "maintenance" || ["compact", "cleanup", "maintenance"].includes(destinationOperationType);
}

export function MaintenanceDataflowCell({ row }: { row: Record<string, unknown> }) {
  const name = String(row.dataflow_name ?? row.dataflow_id ?? "unknown dataflow");
  const stage = String(row.stage ?? "unknown");
  const operation = String(row.operation_type ?? "unknown");
  const destinationOperation = operationName(String(row.destination_operation_type ?? ""));
  const context = [stage, operation, destinationOperation !== "—" && destinationOperation.toLowerCase() !== operation.toLowerCase() ? destinationOperation : ""].filter(Boolean).join(" · ");
  return <Help className="freshness-run-stack-cell" title={[`Stage: ${stage}`, `Operation: ${operation}`, `Destination operation: ${destinationOperation}`].join("\n")}><strong>{name}</strong><small>{context}</small></Help>;
}

export function MaintenanceSourceCell({ row }: { row: Record<string, unknown> }) {
  const source = maintenanceSourceParts(row);
  const stageClass = monitoringConnectionStageClass(source.connection);
  return <Help className={["maintenance-source-cell", stageClass].filter(Boolean).join(" ")} title={[`Source connection: ${source.connection}`, `Source object: ${source.object}`].join("\n")}><strong>{source.connection}</strong><small>{source.object}</small></Help>;
}

export function MaintenanceLatestCell({ row, timezoneName }: { row: Record<string, unknown>; timezoneName?: string | null }) {
  const compactRun = hasValue(row.event_time) || hasValue(row.start_time);
  const status = firstText(row, compactRun ? "status" : "latest_status", compactRun ? "latest_status" : "status") || "unknown";
  const runTime = compactRun ? firstText(row, "event_time", "start_time") : firstText(row, "latest_time", "start_time");
  const label = compactRun ? "Run" : "Latest run";
  return <Help className="freshness-run-stack-cell" title={[`${label} status: ${status}`, `${label} time: ${runTime || "-"}`].join("\n")}><strong className={`maintenance-status-text ${maintenanceStatusTextClass(status)}`}><StatusPill status={statusName(status)} /></strong><small>{runTime ? formatTimestampForDisplay(runTime, timezoneName, "—") : "—"}</small></Help>;
}

export function maintenanceStatusTextClass(status: string) {
  const normalized = status.toLowerCase();
  if (normalized === "succeeded") return "is-succeeded";
  if (normalized === "failed") return "is-failed";
  if (normalized === "skipped") return "is-skipped";
  if (normalized === "running") return "is-running";
  if (normalized === "pending") return "is-pending";
  return "is-unknown";
}

export function MaintenanceRunVolumeCell({ row }: { row: Record<string, unknown> }) {
  const rowsWritten = metricValue(row, "destination_rows_written");
  const filesAdded = metricValue(row, "destination_files_added");
  const filesRemoved = metricValue(row, "destination_files_removed");
  const bytesAdded = metricValue(row, "destination_bytes_added");
  const bytesRemoved = metricValue(row, "destination_bytes_removed");
  const effectQualified = String(row.physical_effect_eligible ?? "").toLowerCase() === "true";
  const destinationOperation = firstText(row, "destination_operation_type");
  const destinationStatus = firstText(row, "destination_status");
  return <Help className="freshness-run-stack-cell" title={[`Rows written: ${formatTooltipNumber(rowsWritten)}`, `Files added: ${formatTooltipNumber(filesAdded)}`, `Files removed: ${formatTooltipNumber(filesRemoved)}`, `Bytes added: ${formatTooltipBytes(bytesAdded)}`, `Bytes removed: ${formatTooltipBytes(bytesRemoved)}`, `Destination phase: ${destinationOperation || "—"} · ${destinationStatus || "unknown"}`, effectQualified ? "Physical effect qualified" : "Reported; physical effect unconfirmed"].join("\n")}><strong><CompactNumberValue value={rowsWritten} /> rows</strong><small><CompactNumberValue value={filesAdded} /> / <CompactNumberValue value={filesRemoved} /> files</small></Help>;
}

export function maintenanceSourceParts(row: Record<string, unknown>) {
  const connection = row.source_name ?? "unknown";
  const object = row.source_full_table ?? row.source_table ?? row.source_path ?? "-";
  return { connection: String(connection), object: String(object) };
}

function firstMetric(row: Record<string, unknown>, ...keys: string[]) {
  for (const key of keys) {
    const value = metricValue(row, key);
    if (value !== undefined && value !== null && value !== "") return value;
  }
  return null;
}

function statusName(value: string) {
  return (value || "unknown").replace(/_/gu," ").replace(/^./u,letter=>letter.toUpperCase());
}

function operationName(value: string) {
  const decoded = monitoringDimensionLabel(value);
  if (decoded === "Unknown") return "—";
  return decoded.toLowerCase() === "etl" ? "ETL" : decoded.replace(/_/gu," ").replace(/^./u,letter => letter.toUpperCase());
}

function firstText(row: Record<string, unknown>, ...keys: string[]) {
  for (const key of keys) {
    const value = row[key];
    if (value !== undefined && value !== null && String(value).trim() !== "") return String(value);
  }
  return "";
}

function latestCellValue(row: Record<string, unknown>, timezoneName?: string | null) {
  const latest = firstText(row, "latest_time");
  return latest ? <span><StatusPill status={statusName(firstText(row, "latest_status"))} /> <span>{formatTimestampForDisplay(latest, timezoneName, "—")}</span></span> : "—";
}

function timestampValue(row: Record<string, unknown>, field: string, timezoneName?: string | null) {
  const value = row[field];
  return hasValue(value) ? formatTimestampForDisplay(String(value), timezoneName, "—") : "—";
}

function durationValue(row: Record<string, unknown>, ...fields: string[]) {
  const value = firstMetric(row, ...fields);
  const seconds = toApproximateNumber(value);
  return seconds == null ? "—" : formatSeconds(seconds);
}

function DestinationPhaseValue({ row }: { row: Record<string, unknown> }) {
  const operation = operationName(firstText(row, "destination_operation_type"));
  const status = firstText(row, "destination_status");
  if (!operation && !status) return "—";
  return <span className="monitor-stack-cell"><strong>{operation || "—"}</strong><small>{status || "status unavailable"}</small></span>;
}

function SummaryMetricValue({ row, metric, bytes = false, signed = false, denominatorKey = "run_count", measuredKey }: { row: Record<string, unknown>; metric: string; bytes?: boolean; signed?: boolean; denominatorKey?: string; measuredKey?: string }) {
  const exact = metricValue(row, metric);
  const observed = metricValue(row, `${metric}_observed`);
  const measured = metricValue(row, measuredKey ?? `${metric}_measured_runs`);
  const denominator = metricValue(row, denominatorKey);
  const value = exact ?? observed;
  const partial = value != null && (exact == null || measured != null && denominator != null && compareMetricValues(measured, denominator) < 0);
  const rendered = bytes ? (signed ? <SignedBytesValue value={value} /> : <CompactBytesValue value={value} />) : <CompactNumberValue value={value} />;
  return <Help className={partial ? "monitor-observed-value" : undefined} title={partial ? `${formatTooltipNumber(measured)} / ${formatTooltipNumber(denominator)} runs measured` : undefined}>{rendered}{partial ? <small className="monitor-observed-label">observed</small> : null}</Help>;
}

function SignedBytesValue({ value }: { value: ReturnType<typeof metricValue> }) {
  const approximate = toApproximateNumber(value);
  return <span>{approximate != null && approximate > 0 ? "+" : ""}<CompactBytesValue value={value} /></span>;
}

function MaintenanceRateValue({ row, metric, bytes = false, bytesPerSecond = false, percent = false }: { row: Record<string, unknown>; metric: string; bytes?: boolean; bytesPerSecond?: boolean; percent?: boolean }) {
  const value = metricValue(row, metric);
  const formatted = percent ? formatPercent(value) : bytes ? formatTooltipBytes(value) : formatTooltipNumber(value);
  return <Help title={hasValue(value) ? `${metric.replace(/^maintenance_/, "").replace(/_/g, " ")}: ${formatted}` : undefined}>{formatted}{bytesPerSecond && hasValue(value) ? "/s" : ""}</Help>;
}

function maintenanceRuntimeSummary(row: Record<string, unknown>) {
  const total = firstMetric(row, "maintenance_duration_observed");
  const measured = firstMetric(row, "maintenance_duration_measured_runs");
  const denominator = firstMetric(row, "maintenance_run_count");
  return <Help className="monitor-stack-cell" title={`Observed maintenance runtime: ${formatTooltipNumber(total)} seconds\nMeasured runs: ${formatTooltipNumber(measured)} / ${formatTooltipNumber(denominator)}`}><strong>{durationValue(row, "maintenance_duration_observed")}</strong><small>{formatTooltipNumber(measured)} / {formatTooltipNumber(denominator)} runs measured</small></Help>;
}

function runtimeSummary(row: Record<string, unknown>) {
  const total = firstMetric(row, "duration_observed");
  const measured = firstMetric(row, "duration_measured_runs");
  const denominator = firstMetric(row, "run_count");
  const average = firstMetric(row, "avg_duration_seconds");
  return <Help className="monitor-stack-cell" title={`Observed root runtime: ${formatTooltipNumber(total)} seconds\nMeasured runs: ${formatTooltipNumber(measured)} / ${formatTooltipNumber(denominator)}\nAverage measured runtime: ${formatTooltipNumber(average)} seconds`}><strong>{durationValue(row, "duration_observed")}</strong><small>{formatTooltipNumber(measured)} / {formatTooltipNumber(denominator)} runs measured</small></Help>;
}
