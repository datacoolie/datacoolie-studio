import { Help } from "../../../shared/components/Help";
import { monitoringDimensionLabel } from "../monitoringDimensions";
import { memo } from "react";
import type { EChartsOption } from "echarts";
import type { MonitoringRecord, MonitoringReport } from "../../../shared/api/domainTypes";
import type { MonitoringFilters } from "../monitoringFilters";
import type { TableSort } from "../../../shared/components/DataTable";import { CompactBytesValue, CompactNumberValue, DetailMetric, HealthStripCard, ReportChart, ReportPanel, TablePager, baseChartOption, bottomAnchoredValueXAxis, createEmptyVolumeTrendRow, formatBytesShort, formatCompact, formatTooltipBytes, formatNumber, horizontalBarDataZoom, mergeVolumeTrendRows, metricDifference, metricNumber, metricValue, monitoringTimezone, num, reportChartPalette, reportChartGrid, resolveTrendBucketKeys, toApproximateNumber, workloadVolumeTrendOption as sharedWorkloadVolumeTrendOption, type MetricValue } from "../components/monitoringPresentation";
import { EndpointRouteNode } from "./DataflowsPagePresentation";
import { alignedVolumeAxisBounds } from "../volumePageModel";
import { MeasurementCoverage, ObservedNumberValue } from "../components/monitoringPresentation";
import { rowObservation } from "../components/monitoringObservation";
import { formatTooltipNumber } from "../../../shared/lib/numberFormat";
import { VolumeRegistryTable } from "./VolumeRegistryTable";
import { chartMetric, chartObservationMetric, chartTooltip, firstChartParam } from "../components/chartTooltip";

type ReportState = { loading: boolean; error: string | null; retry: () => void };

export function VolumePage({
  report,
  filters,
  rows,
  totalRows,
  loading,
  sort,
  onSort,
  limit,
  offset,
  onPageChange,
  onPageSizeChange,
  onInspect,
  timezoneName,
  reportState,
  scrollResetKey,
  tablePending = false,
  tableError = null,
}: {
  report: MonitoringReport | null;
  filters: MonitoringFilters;
  rows: MonitoringRecord[];
  totalRows: number;
  loading: boolean;
  sort: TableSort;
  onSort: (sort: TableSort) => void;
  limit: number;
  offset: number;
  onPageChange: (offset: number) => void;
  onPageSizeChange: (limit: number) => void;
  onInspect?: (row: MonitoringRecord) => void;
  timezoneName?: string | null;
  reportState?: ReportState;
  scrollResetKey?: string;
  tablePending?: boolean;
  tableError?: string | null;
}) {
  const resolvedTimezoneName = timezoneName !== undefined ? timezoneName : report ? monitoringTimezone(report) : null;
  const hasTableResponse = !tablePending && !tableError;
  const registryState = tableError ? "Dataflows unavailable" : tablePending ? "Loading dataflows…" : null;
  return (
    <div className="monitoring-page monitoring-volume-report">
      {report ? (
        <VolumeReportContent
          part="health"
          report={report}
          filters={filters}
          timezoneName={resolvedTimezoneName ?? "UTC"}
        />
      ) : (
        <VolumeReportPlaceholder part="health" reportState={reportState} />
      )}
      <div className="monitoring-volume-content monitoring-equal-rows-3">
        {report ? <VolumeReportContent part="charts" report={report} filters={filters} timezoneName={resolvedTimezoneName ?? "UTC"} /> : <VolumeReportPlaceholder part="charts" reportState={reportState} />}
        <ReportPanel title="Dataflow volume registry" subtitle={hasTableResponse ? <><CompactNumberValue value={totalRows} /> dataflows · totals in current filters</> : undefined} className="monitoring-volume-runs-panel" titleTooltip="One row per dataflow in the current filters. Totals are aggregated across related runs; click a row to inspect volume evidence and individual runs." headerAction={hasTableResponse ? <TablePager limit={limit} offset={offset} loadedRows={rows.length} totalRows={totalRows} loading={loading} onPageChange={onPageChange} onPageSizeChange={onPageSizeChange} /> : undefined}>
          {registryState ? <div className="monitoring-registry-table-state" role="status">{registryState}</div> : <VolumeRegistryTable rows={rows} sort={sort} onSort={onSort} onInspect={onInspect} timezoneName={resolvedTimezoneName} scrollResetKey={scrollResetKey} />}
        </ReportPanel>
      </div>
    </div>
  );
}

function volumeKpiTooltip(label: string, row: Record<string, unknown>, field: string, totalField = field, bytes = false) {
  const observation = rowObservation(row, field, totalField);
  const value = bytes ? formatTooltipBytes(observation.value) : formatTooltipNumber(observation.value);
  return `${label}${observation.partial && observation.value != null ? " (observed subtotal)" : ""}: ${value}; ${observation.coverage ?? "Coverage unavailable"}`;
}

const VolumeReportContent = memo(function VolumeReportContent({
  part,
  report,
  filters,
  timezoneName
}: {
  part: "health" | "charts";
  report: MonitoringReport;
  filters: MonitoringFilters;
  timezoneName: string;
}) {
  const kpis = report.volume.kpis ?? {};
  const net = rowObservation(kpis, "net_bytes_change");
  const highVolumeCount = kpis.high_volume_dataflow_count;
  const candidateRunCount = kpis.high_volume_candidate_run_count === undefined ? kpis.high_volume_run_count : kpis.high_volume_candidate_run_count;
  const inserted = rowObservation(kpis, "rows_inserted", "total_rows_inserted");
  const updated = rowObservation(kpis, "rows_updated", "total_rows_updated");
  const deleted = rowObservation(kpis, "rows_deleted", "total_rows_deleted");
  const addedFiles = rowObservation(kpis, "files_added");
  const removedFiles = rowObservation(kpis, "files_removed");
  const average = metricValue(kpis, "avg_bytes_per_file_added");
  const averageCoverage = rowObservation(kpis, "bytes_per_file", "avg_bytes_per_file_added");
  const averageLabel = average != null && averageCoverage.partial ? "Avg added file (observed)" : "Avg added file";
  const rowChangesPartial = [inserted, updated, deleted].some((value) => value.partial && value.value != null);
  const filesPartial = [addedFiles, removedFiles].some((value) => value.partial && value.value != null);

  const healthContent = (
      <section className="overview-health-strip monitoring-volume-health-strip">
        <HealthStripCard
          label="Rows read"
          value={<ObservedNumberValue row={kpis} field="rows_read" totalField="total_rows_read" />}
          detail={<MeasurementCoverage row={kpis} field="rows_read" />}
          intent="neutral"
          accent="source"
          className="volume-kpi volume-kpi-rows-read"
          title={`Total rows read in the current filters. Missing measurements remain unknown.\n${volumeKpiTooltip("Rows read", kpis, "rows_read", "total_rows_read")}`}
        />
        <HealthStripCard
          label="Rows written"
          value={<ObservedNumberValue row={kpis} field="rows_written" totalField="total_rows_written" />}
          detail={<MeasurementCoverage row={kpis} field="rows_written" />}
          intent="neutral"
          accent="destination"
          className="volume-kpi volume-kpi-written"
          title={`Total recorded rows written in the current filters.\n${volumeKpiTooltip("Rows written", kpis, "rows_written", "total_rows_written")}\n${volumeKpiTooltip("Lakehouse rows written", kpis, "lakehouse_rows_written", "total_lakehouse_rows_written")}`}
        />
        <HealthStripCard
          label="Row changes"
          value={
            <span className="monitor-observed-value">
              <span className="volume-kpi-triple-value" aria-label="inserted / updated / deleted">
                <Help as="b" className="is-insert" title={volumeKpiTooltip("Inserted", kpis, "rows_inserted", "total_rows_inserted")}><CompactNumberValue value={inserted.value} /></Help>
                <span>/</span>
                <Help as="b" className="is-update" title={volumeKpiTooltip("Updated", kpis, "rows_updated", "total_rows_updated")}><CompactNumberValue value={updated.value} /></Help>
                <span>/</span>
                <Help as="b" className="is-delete" title={volumeKpiTooltip("Deleted", kpis, "rows_deleted", "total_rows_deleted")}><CompactNumberValue value={deleted.value} /></Help>
              </span>
              {rowChangesPartial ? <span className="monitor-observed-label">observed</span> : null}
            </span>
          }
          detail="insert / update / delete"
          intent="neutral"
          accent="destination"
          className="volume-kpi volume-kpi-row-changes"
          title={`Recorded destination row changes in the current filters. Coverage is separate for each metric across all filtered runs; missing does not establish destination applicability.\n${volumeKpiTooltip("Inserted", kpis, "rows_inserted", "total_rows_inserted")}\n${volumeKpiTooltip("Updated", kpis, "rows_updated", "total_rows_updated")}\n${volumeKpiTooltip("Deleted", kpis, "rows_deleted", "total_rows_deleted")}`}
        />
        <HealthStripCard
          label="Net bytes change"
          value={<span className="monitor-observed-value">
            <Help as="span" title={formatSignedBytes(net.value)} aria-label={formatSignedBytes(net.value)}>{metricNumber(net.value) > 0 ? "+" : ""}<CompactBytesValue value={net.value} /></Help>
            {net.partial && net.value != null ? <span className="monitor-observed-label">observed</span> : null}
          </span>}
          detail={<MeasurementCoverage row={kpis} field="net_bytes_change" />}
          intent="neutral"
          accent="storage"
          className="volume-kpi volume-kpi-lakehouse-bytes"
          title={`Signed sum of added minus removed bytes on runs recording both values in the current filters. Growth or removal is storage context.\n${volumeKpiTooltip("Net bytes change", kpis, "net_bytes_change", "net_bytes_change", true)}\nIndependent observed totals below may cover different runs and need not subtract to the paired net value.\n${volumeKpiTooltip("Bytes added", kpis, "bytes_added", "total_bytes_added", true)}\n${volumeKpiTooltip("Bytes removed", kpis, "bytes_removed", "total_bytes_removed", true)}`}
        />
        <HealthStripCard
          label="Files changed"
          value={
            <span className="monitor-observed-value">
              <span className="volume-kpi-dual-value" aria-label="files added / files removed">
                <Help as="b" className="is-added" title={volumeKpiTooltip("Files added", kpis, "files_added")}><CompactNumberValue value={addedFiles.value} /></Help>
                <span>/</span>
                <Help as="b" className="is-removed" title={volumeKpiTooltip("Files removed", kpis, "files_removed")}><CompactNumberValue value={removedFiles.value} /></Help>
              </span>
              {filesPartial ? <span className="monitor-observed-label">observed</span> : null}
            </span>
          }
          detail={<Help as="span" title={`${averageLabel}: ${formatTooltipBytes(average)}; ${averageCoverage.coverage ?? "Coverage unavailable"}. Sum of bytes / sum of files on the same paired runs.`}><DetailMetric label="avg added file" value={<span className="monitor-observed-value"><CompactBytesValue value={average} />{average != null && averageCoverage.partial ? <span className="monitor-observed-label">observed</span> : null}</span>} tone="neutral" /></Help>}
          intent="neutral"
          accent="neutral"
          className="volume-kpi volume-kpi-files-changed"
          title={`Recorded destination files added / removed in the current filters. Coverage is separate for each metric.\n${volumeKpiTooltip("Files added", kpis, "files_added")}\n${volumeKpiTooltip("Files removed", kpis, "files_removed")}\n${averageLabel}: ${formatTooltipBytes(average)}; ${averageCoverage.coverage ?? "Coverage unavailable"}. Sum of bytes / sum of files on paired runs; this is an average per file, not per run.`}
        />
        <HealthStripCard
          label="Dataflows with volume signals"
          value={<CompactNumberValue value={highVolumeCount} />}
          detail="P95 of dataflow totals"
          intent="neutral"
          accent="neutral"
          className="volume-kpi volume-kpi-candidates"
          title={`Dataflows matching a P95 aggregate workload rule in the current filters: ${formatTooltipNumber(highVolumeCount)}. Large volume is context for investigation.\nRuns with volume signals: ${formatTooltipNumber(candidateRunCount)}, assessed using separate run-level P95 thresholds across the filtered runs. This is not the run count belonging to the dataflows above.`}
        />
      </section>
  );

  const chartContent = (
      <>
        <section className="monitoring-volume-primary-grid">
          <ReportPanel
            title="Workload volume trend"
            titleTooltip="Rows read is universal. Rows written preserves the transported write metric. Lakehouse bytes added and removed provide storage context on the secondary axis; chart geometry is approximate for decimal-string metrics and hover tooltips show exact values."
            className="monitoring-volume-trend-panel"
            headerAction={<WorkloadVolumeTrendLegend />}
          >
            <ReportChart
              option={
                sharedWorkloadVolumeTrendOption(
                  report.volume.rows_by_date ?? [],
                  report.volume.bytes_by_date ?? [],
                  filters,
                  report.summary.date_range,
                  timezoneName,
                  report.summary.effective_grain ?? undefined,
                  false
                ) ?? emptyChartOption("No workload volume trend in current filters.")
              }
              height="100%"
            />
          </ReportPanel>
          <ReportPanel
            title="Lakehouse storage delta trend"
            titleTooltip="Signed bars show net lakehouse byte movement by time bucket. Chart geometry is approximate for decimal-string metrics; tooltips include exact added/removed bytes and files."
            className="monitoring-volume-storage-trend-panel"
            headerAction={<StorageDeltaTrendLegend />}
          >
            <ReportChart
              option={storageDeltaTrendOption(report, filters, timezoneName, false)}
              height="100%"
            />
          </ReportPanel>
        </section>

        <section className="monitoring-volume-secondary-grid">
          <ReportPanel
            title="Workload mix"
            titleTooltip="Groups non-maintenance dataflow runs by operation_type and destination load/operation type. Rows read and rows written are grouped for comparison and are not stacked into one total; chart geometry is approximate for decimal-string metrics and hover tooltips show exact values."
            className="monitoring-volume-workload-mix-panel"
            headerAction={<WorkloadMixLegend />}
          >
            <ReportChart
              option={workloadMixOption(report.volume.volume_by_workload_type ?? report.volume.volume_by_load_type ?? [], false)}
              height="100%"
              wheelDataZoomStep={1}
            />
          </ReportPanel>
          <ReportPanel
            title="Source to destination volume"
            subtitle="workload and storage signals by route"
            titleTooltip="Groups dataflow runs by source and destination connection pair. Routes remain visible when they have row, byte, or file evidence; rows read remain the primary workload ranking signal."
          >
            <VolumeRoutePanel rows={report.volume.route_volume ?? []} />
          </ReportPanel>
          <ReportPanel
            title="Top dataflows by workload"
            subtitle="top 20 by rows read"
            titleTooltip="Ranks up to top 20 dataflows by rows read in current filters."
          >
            <ReportChart
              option={topDataflowsByWorkloadOption(report.volume.top_dataflows_by_rows_read ?? [])}
              height="100%"
              wheelDataZoomStep={1}
            />
          </ReportPanel>
        </section>

      </>
  );
  return part === "health" ? healthContent : chartContent;
});

function VolumeReportPlaceholder({ part, reportState }: { part: "health" | "charts"; reportState?: ReportState }) {
  const error = reportState?.error;
  if (part === "health") {
    return <>
      <section className="overview-health-strip monitoring-volume-health-strip monitoring-report-placeholder-strip" aria-busy={!error}>
        {Array.from({ length: 6 }, (_, index) => <div className="monitoring-report-health-placeholder" key={index}><span>{error ? "Unavailable" : "Loading…"}</span></div>)}
      </section>
      {error ? <div className="monitoring-report-state is-error" role="alert"><span>Could not load volume report: {error}</span>{reportState?.retry ? <button type="button" onClick={reportState.retry}>Retry</button> : null}</div> : null}
    </>;
  }
  const chartTitles = [
    "Workload volume trend",
    "Lakehouse storage delta trend",
    "Workload mix",
    "Source to destination volume",
    "Top dataflows by workload"
  ];
  return <>
    <section className="monitoring-volume-primary-grid">{chartTitles.slice(0, 2).map((title) => <ReportPanel key={title} title={title}><div className="monitoring-report-chart-placeholder" aria-hidden="true" /></ReportPanel>)}</section>
    <section className="monitoring-volume-secondary-grid">{chartTitles.slice(2).map((title) => <ReportPanel key={title} title={title}><div className="monitoring-report-chart-placeholder" aria-hidden="true" /></ReportPanel>)}</section>
  </>;
}

function WorkloadVolumeTrendLegend() {
  return <VolumeChartLegend label="Workload volume trend legend" items={[
    ["Rows read", reportChartPalette.read],
    ["Rows written", reportChartPalette.written],
    ["Bytes added", reportChartPalette.teal],
    ["Bytes removed", reportChartPalette.failed]
  ]} />;
}

function StorageDeltaTrendLegend() {
  return <VolumeChartLegend label="Lakehouse storage delta trend legend" items={[
    ["Net bytes +", reportChartPalette.teal],
    ["Net bytes −", reportChartPalette.failed],
    ["Files changed", reportChartPalette.blue]
  ]} />;
}

function WorkloadMixLegend() {
  return <VolumeChartLegend label="Workload mix legend" items={[
    ["Rows read", reportChartPalette.read],
    ["Rows written", reportChartPalette.written]
  ]} />;
}

function VolumeChartLegend({ label, items }: { label: string; items: ReadonlyArray<readonly [string, string]> }) {
  return (
    <div className="monitoring-volume-chart-legend" aria-label={label}>
      {items.map(([itemLabel, color]) => (
        <span key={itemLabel}>
          <i style={{ backgroundColor: color }} aria-hidden="true" />
          {itemLabel}
        </span>
      ))}
    </div>
  );
}

export function VolumeRoutePanel({ rows }: { rows: Array<Record<string, string | number | null>> }) {
  const visible = rows.filter(hasVolumeRouteSignal);
  if (!visible.length) return <div className="table-empty">No route volume signals in current filters.</div>;
  return (
    <div className="dataflow-signal-list dataflow-endpoint-route-list volume-route-list">
      {visible.map((row, index) => {
        const source = routeEndpoint(row, "source");
        const destination = routeEndpoint(row, "destination");
        const readObservation = rowObservation(row, "rows_read");
        const writeObservation = rowObservation(row, "rows_written");
        const rowsWrittenValue = writeObservation.value;
        const lakehouseRowsWrittenValue = metricValue(row, "lakehouse_rows_written");
        const bytesAddedValue = metricValue(row, "bytes_added");
        const bytesRemovedValue = metricValue(row, "bytes_removed");
        const filesAddedValue = metricValue(row, "files_added");
        const filesRemovedValue = metricValue(row, "files_removed");
        const hasWrittenRows = rowsWrittenValue != null;
        const bytesAdded = num(row, "bytes_added");
        const bytesRemoved = num(row, "bytes_removed");
        const filesChangedValue = metricSum(filesAddedValue, filesRemovedValue);
        const filesChanged = toApproximateNumber(filesChangedValue) ?? 0;
        const totalStorageBytesValue = metricSum(bytesAddedValue, bytesRemovedValue);
        const netBytes = metricDifference(bytesAddedValue, bytesRemovedValue);
        const hasStorageBytes = bytesAdded > 0 || bytesRemoved > 0;
        const destinationTone = hasWrittenRows
            ? "observed"
            : hasStorageBytes
              ? "bytes"
              : filesChanged > 0
                ? "files"
                : "neutral";
        const destinationValue = hasWrittenRows
            ? formatCompact(rowsWrittenValue)
            : hasStorageBytes
              ? formatBytesShort(totalStorageBytesValue)
              : filesChanged > 0
                ? formatCompact(filesChangedValue)
                : "—";
        const storageContext = netBytes === null ? "-" : `Δ ${formatSignedBytesShort(netBytes)}`;
        const routeMetrics = [
          { label: "Runs", value: formatNumber(metricValue(row, "runs")) },
          { label: "Skipped", value: formatNumber(metricValue(row, "skipped")) },
          ...(readObservation.value == null ? [] : [{ label: "Rows read", value: formatNumber(readObservation.value), valueColor: reportChartPalette.read }]),
          ...(rowsWrittenValue == null ? [] : [{ label: "Rows written", value: formatNumber(rowsWrittenValue), valueColor: reportChartPalette.written }]),
          ...(lakehouseRowsWrittenValue == null ? [] : [{ label: "Lakehouse rows written", value: formatNumber(lakehouseRowsWrittenValue), valueColor: reportChartPalette.written }]),
          ...(bytesAddedValue == null ? [] : [{ label: "Bytes added", value: formatTooltipBytes(bytesAddedValue), valueColor: reportChartPalette.teal }]),
          ...(bytesRemovedValue == null ? [] : [{ label: "Bytes removed", value: formatTooltipBytes(bytesRemovedValue), valueColor: reportChartPalette.failed }]),
          ...(filesAddedValue == null && filesRemovedValue == null ? [] : [{ label: "Files added / removed", value: `${formatNumber(filesAddedValue)} / ${formatNumber(filesRemovedValue)}`, valueColor: reportChartPalette.blue }]),
          ...(netBytes == null ? [] : [{ label: "Net bytes", value: formatSignedBytes(netBytes), valueColor: metricNumber(netBytes) >= 0 ? reportChartPalette.teal : reportChartPalette.failed }]),
        ];
        return (
          <Help as="div"
            key={`${source.connection}-${destination.connection}-${index}`}
            className="dataflow-signal-row dataflow-endpoint-route-row"
            content={{
              title: "Route volume",
              context: `${source.connection} → ${destination.connection}`,
              metrics: routeMetrics,
            }}
          >
            <div className="dataflow-route-flow">
              <div className="dataflow-route-line">
                <EndpointRouteNode endpoint={source} />
                <span className="dataflow-route-arrow" aria-hidden="true">→</span>
                <EndpointRouteNode endpoint={destination} />
              </div>
            </div>
            <div className="dataflow-route-health">
              <strong className="volume-route-read-value"><CompactNumberValue value={readObservation.value} /></strong>
              <small><CompactNumberValue value={metricValue(row, "runs")} /> runs</small>
            </div>
            <div className="dataflow-route-volume">
              <strong className={`volume-route-destination-value volume-route-value-${destinationTone}`}>{hasWrittenRows ? <CompactNumberValue value={rowsWrittenValue} /> : destinationValue}</strong>
              <small className="volume-route-context">{storageContext}</small>
            </div>
          </Help>
        );
      })}
    </div>
  );
}

function metricSum(left: MetricValue, right: MetricValue): MetricValue {
  if (left === null || left === undefined || right === null || right === undefined) return null;
  if (typeof left === "string" && /^-?\d+$/u.test(left.trim()) && typeof right === "string" && /^-?\d+$/u.test(right.trim())) {
    return (BigInt(left) + BigInt(right)).toString();
  }
  if (typeof left === "number" && Number.isSafeInteger(left) && typeof right === "number" && Number.isSafeInteger(right)) {
    return (BigInt(left) + BigInt(right)).toString();
  }
  const leftNumber = toApproximateNumber(left);
  const rightNumber = toApproximateNumber(right);
  return leftNumber === null || rightNumber === null ? null : leftNumber + rightNumber;
}

function hasVolumeRouteSignal(row: Record<string, string | number | null>) {
  return (
    (toApproximateNumber(rowObservation(row, "rows_read").value) ?? 0) > 0 ||
    (toApproximateNumber(rowObservation(row, "rows_written").value) ?? 0) > 0 ||
    num(row, "lakehouse_rows_written") > 0 ||
    num(row, "bytes_added") > 0 ||
    num(row, "bytes_removed") > 0 ||
    num(row, "files_added") > 0 ||
    num(row, "files_removed") > 0
  );
}

function formatSignedBytes(value: MetricValue) {
  const formatted = formatTooltipBytes(value);
  if (formatted === "-") return formatted;
  const text = String(value);
  return /^-?0(?:\.0*)?$/u.test(text) || text.startsWith("-") ? formatted : `+${formatted}`;
}

function formatSignedBytesShort(value: MetricValue) {
  const formatted = formatBytesShort(value);
  if (formatted === "-") return formatted;
  return metricNumber(value) > 0 ? `+${formatted}` : formatted;
}

function routeEndpoint(row: Record<string, unknown>, direction: "source" | "destination") {
  const connection = monitoringDimensionLabel(row[`${direction}_name`]);
  const format = monitoringDimensionLabel(row[`${direction}_format`] ?? row[`${direction}_connection_type`]);
  const connectionType = monitoringDimensionLabel(row[`${direction}_connection_type`]);
  return { locator: connection, connection, format, connectionType };
}

export function storageDeltaTrendOption(report: MonitoringReport, filters: MonitoringFilters, timezoneName: string, showLegend = true): EChartsOption {
  const visible = volumeTrendRows(report, filters, timezoneName);
  if (!visible.length) return emptyChartOption("No lakehouse storage delta in current filters.");
  const netByteValues = visible.map((row) => toApproximateNumber(metricValue(row, "net_bytes")));
  const fileChangeValues = visible.map((row) => {
    const added = toApproximateNumber(metricValue(row, "files_added"));
    const removed = toApproximateNumber(metricValue(row, "files_removed"));
    return added === null || removed === null ? null : added + removed;
  });
  const axisBounds = alignedVolumeAxisBounds(
    netByteValues.filter((value): value is number => value !== null),
    fileChangeValues.filter((value): value is number => value !== null),
  );
  return baseChartOption({
    tooltip: {
      trigger: "axis",
      formatter: chartTooltip((params) => {
        const row = visible[Number(firstChartParam(params)?.dataIndex ?? 0)] ?? {};
        return {
          title: String(row.bucket || row.date || ""),
          context: timezoneName ? `Timezone: ${timezoneName}` : undefined,
          metrics: [
            { ...chartMetric("Net bytes", formatTooltipBytes(metricValue(row, "net_bytes"))), valueColor: row.net_bytes == null ? undefined : metricNumber(row.net_bytes) >= 0 ? reportChartPalette.teal : reportChartPalette.failed },
            { ...chartMetric("Bytes added", formatTooltipBytes(metricValue(row, "bytes_added"))), valueColor: row.bytes_added != null ? reportChartPalette.teal : undefined },
            { ...chartMetric("Bytes removed", formatTooltipBytes(metricValue(row, "bytes_removed"))), valueColor: row.bytes_removed != null ? reportChartPalette.failed : undefined },
            { ...chartMetric("Files added / removed", `${formatNumber(metricValue(row, "files_added"))} / ${formatNumber(metricValue(row, "files_removed"))}`), valueColor: row.files_added != null || row.files_removed != null ? reportChartPalette.blue : undefined },
          ]
        };
      })
    },
    legend: showLegend ? { top: 0, left: "center", itemWidth: 9, itemHeight: 9, textStyle: { fontSize: 10 } } : { show: false },
    grid: reportChartGrid({ left: 42, right: 38, top: showLegend ? 28 : 5, containLabel: false }),
    xAxis: { type: "category", data: visible.map((row) => row.bucket || row.date), axisLabel: { fontSize: 10, hideOverlap: true }, axisTick: { show: false } },
    yAxis: [
      { type: "value", min: axisBounds.primaryMin, max: axisBounds.primaryMax, axisLabel: { fontSize: 10, formatter: (value: number) => formatBytesShort(value) }, splitLine: { lineStyle: { color: reportChartPalette.grid } } },
      { type: "value", position: "right", min: axisBounds.secondaryMin, max: axisBounds.secondaryMax, axisLabel: { fontSize: 10, formatter: (value: number) => value < 0 ? "" : formatCompact(value) }, splitLine: { show: false } }
    ],
    series: [
      {
        name: "Net lakehouse bytes",
        type: "bar",
        itemStyle: {
          color: (params: any) => Number(params?.value ?? 0) >= 0 ? reportChartPalette.teal : reportChartPalette.failed,
          borderRadius: 3
        },
        data: netByteValues
      },
      { ...lineSeries("Files changed", fileChangeValues, reportChartPalette.blue, 1), showSymbol: fileChangeValues.filter((value) => value !== null).length === 1 }
    ]
  });
}

export function workloadMixOption(rows: Array<Record<string, string | number | null>>, showLegend = true): EChartsOption {
  const visible = rows
    .filter((row) => !isMaintenanceWorkload(row))
    .slice()
    .sort((left, right) => (toApproximateNumber(rowObservation(right, "rows_read").value) ?? 0) - (toApproximateNumber(rowObservation(left, "rows_read").value) ?? 0));
  if (!visible.length) return emptyChartOption("No workload mix in current filters.");
  const labels = visible.map((row) => monitoringDimensionLabel(row.workload_type ?? row.load_type));
  const dataZoom = horizontalBarDataZoom(visible.length);
  return baseChartOption({
    tooltip: {
      trigger: "item",
      formatter: chartTooltip((params) => {
        const row = visible[Number(firstChartParam(params)?.dataIndex ?? 0)] ?? {};
        return {
          title: labels[Number(firstChartParam(params)?.dataIndex ?? 0)] ?? "unknown",
          metrics: [
            chartMetric("Runs", formatNumber(metricValue(row, "runs") ?? metricValue(row, "count"))),
            chartObservationMetric("Rows read", row, "rows_read", reportChartPalette.read),
            chartObservationMetric("Rows written", row, "rows_written", reportChartPalette.written),
          ]
        };
      })
    },
    legend: showLegend ? { top: 0, left: "center", itemWidth: 9, itemHeight: 9, textStyle: { fontSize: 10 } } : { show: false },
    grid: reportChartGrid({ left: 110, right: dataZoom ? 24 : 10, top: showLegend ? 28 : 8, containLabel: false }),
    xAxis: bottomAnchoredValueXAxis({ formatter: (value) => formatCompact(value) }),
    yAxis: {
      type: "category",
      data: labels,
      inverse: true,
      axisTick: { show: false },
      axisLabel: { fontSize: 10, width: 96, overflow: "truncate", color: reportChartPalette.muted }
    },
    dataZoom,
    series: [
      { name: "Rows read", type: "bar", barGap: "20%", itemStyle: { color: reportChartPalette.read, borderRadius: 2 }, data: visible.map((row) => plotMetric(rowObservation(row, "rows_read").value)) },
      { name: "Rows written", type: "bar", itemStyle: { color: reportChartPalette.written, borderRadius: 2 }, data: visible.map((row) => plotMetric(rowObservation(row, "rows_written").value)) }
    ]
  });
}

function isMaintenanceWorkload(row: Record<string, string | number | null>) {
  const workloadType = String(row.workload_type ?? row.load_type ?? "").toLowerCase();
  return workloadType.includes("maintenance");
}

export function topDataflowsByWorkloadOption(rows: Array<Record<string, string | number | null>>): EChartsOption {
  const visible = rows.slice().sort((left, right) => num(right, "value") - num(left, "value"));
  if (!visible.length) return emptyChartOption("No top dataflow workload in current filters.");
  const labels = visible.map((row) => String(row.name ?? row.dataflow_id ?? "unknown"));
  const dataZoom = horizontalBarDataZoom(visible.length);
  return baseChartOption({
    tooltip: {
      trigger: "item",
      formatter: chartTooltip((params) => {
        const row = visible[Number(firstChartParam(params)?.dataIndex ?? 0)] ?? {};
        return {
          title: String(row.name ?? "unknown"),
          context: `Dataflow ID: ${String(row.dataflow_id ?? "-")}`,
          metrics: [
            { ...chartMetric("Rows read", formatNumber(metricValue(row, "value"))), valueColor: reportChartPalette.read },
            chartMetric("Runs", formatNumber(metricValue(row, "count"))),
          ]
        };
      })
    },
    grid: reportChartGrid({ left: 138, right: dataZoom ? 24 : 10, top: 8, containLabel: false }),
    xAxis: bottomAnchoredValueXAxis({ formatter: (value) => formatCompact(value) }),
    yAxis: {
      type: "category",
      data: labels,
      inverse: true,
      axisTick: { show: false },
      axisLabel: { align: "right", fontSize: 10, width: 124, overflow: "truncate", margin: 8, color: reportChartPalette.muted }
    },
    dataZoom,
    series: [
      {
        name: "Rows read",
        type: "bar",
        barMaxWidth: 18,
        itemStyle: { color: reportChartPalette.read, borderRadius: [0, 3, 3, 0] },
        label: {
          show: true,
          position: "right",
          color: reportChartPalette.text,
          fontSize: 9,
          formatter: (params: any) => {
            const value = Number(params?.value ?? 0);
            return value > 0 ? formatCompact(value) : "";
          }
        },
        data: visible.map((row) => plotMetric(metricValue(row, "value")))
      }
    ]
  });
}

function volumeTrendRows(report: MonitoringReport, filters: MonitoringFilters, timezoneName: string) {
  const merged = mergeVolumeTrendRows(report.volume.rows_by_date ?? [], report.volume.bytes_by_date ?? []);
  const effectiveGrain = String(merged.find((row) => row.grain)?.grain ?? report.summary.effective_grain ?? filters.grain ?? "day");
  const knownDateKeys = merged.map((row) => row.bucket || row.date).filter((date) => date && date !== "unknown");
  const dateKeys = resolveTrendBucketKeys(filters, report.summary.date_range, timezoneName, knownDateKeys, effectiveGrain);
  const mergedByKey = new Map(merged.map((row) => [row.bucket || row.date, row]));
  const visible = dateKeys.length > 0
    ? dateKeys.map((dateKey) => ({ ...createEmptyVolumeTrendRow(dateKey, effectiveGrain), ...(mergedByKey.get(dateKey) ?? {}) }))
    : merged;
  return visible.map((row) => ({
    ...row,
    rows_written: metricValue(row, "rows_written"),
    lakehouse_rows_written: metricValue(row, "lakehouse_rows_written"),
    rows_inserted: metricValue(row, "rows_inserted"),
    rows_updated: metricValue(row, "rows_updated"),
    rows_deleted: metricValue(row, "rows_deleted"),
    files_added: metricValue(row, "files_added"),
    files_removed: metricValue(row, "files_removed"),
    net_bytes: metricValue(row, "net_bytes") ?? metricDifference(metricValue(row, "bytes_added"), metricValue(row, "bytes_removed"))
  }));
}

function lineSeries(name: string, data: Array<number | null>, color: string, yAxisIndex = 0) {
  return {
    name,
    type: "line" as const,
    yAxisIndex,
    connectNulls: false,
    showSymbol: false,
    showAllSymbol: false,
    symbol: "circle",
    symbolSize: 5,
    clip: false,
    z: 6,
    smooth: 0.2,
    lineStyle: { width: 1.5, color },
    itemStyle: { color: "#ffffff", borderColor: color, borderWidth: 1.3 },
    data
  };
}

function plotMetric(value: MetricValue) {
  return value === null || value === undefined || value === "" ? null : toApproximateNumber(value);
}

function emptyChartOption(text: string): EChartsOption {
  return baseChartOption({
    graphic: {
      type: "text",
      left: "center",
      top: "middle",
      style: { text, fill: reportChartPalette.muted, fontSize: 12, fontWeight: 600 }
    }
  });
}
