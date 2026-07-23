import type { EChartsOption } from "echarts";
import type { MonitoringRecord, MonitoringReport } from "../../../shared/api/types";
import type { MonitoringFilters } from "../monitoringFilters";
import type { TableSort } from "../MonitoringCharts";
import {
  CompactValue,
  DataTable,
  DataflowContextCell,
  DataflowNameCell,
  DetailMetric,
  EndpointCell,
  EndpointRouteNode,
  HealthStripCard,
  ReportChart,
  ReportPanel,
  TablePager,
  baseChartOption,
  bottomAnchoredValueXAxis,
  createEmptyVolumeTrendRow,
  formatBytes,
  formatBytesShort,
  formatCompact,
  formatNumber,
  horizontalBarDataZoom,
  mergeVolumeTrendRows,
  monitoringTimezone,
  num,
  reportChartPalette,
  reportChartGrid,
  resolveTrendBucketKeys,
  workloadVolumeTrendOption as sharedWorkloadVolumeTrendOption
} from "../monitoringShared";
import { alignedVolumeAxisBounds } from "../volumePageModel";

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
  onInspect
}: {
  report: MonitoringReport;
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
}) {
  const kpis = report.volume.kpis ?? {};
  const timezoneName = monitoringTimezone(report);
  const registryRows = rows;
  const netBytes = Number(kpis.net_bytes_change ?? 0);
  const highVolumeCount = Number(kpis.high_volume_dataflow_count ?? 0);
  const candidateRunCount = Number(kpis.high_volume_candidate_run_count ?? kpis.high_volume_run_count ?? 0);

  return (
    <div className="monitoring-page monitoring-volume-report">
      <section className="overview-health-strip monitoring-volume-health-strip">
        <HealthStripCard
          label="Rows read"
          value={formatNumber(kpis.total_rows_read ?? 0)}
          detail={<DetailMetric label="runs" value={formatNumber(report.summary.dataflow_records ?? 0)} tone="neutral" />}
          intent="neutral"
          accent="source"
          className="volume-kpi volume-kpi-rows-read"
          title="Universal workload signal. source_rows_read is collected for all dataflow runs."
        />
        <HealthStripCard
          label="Est rows written"
          value={formatNumber(kpis.total_est_rows_written ?? 0)}
          detail={
            <span className="health-rate-detail">
              <DetailMetric label="lakehouse obs" value={formatNumber(kpis.total_rows_written ?? 0)} tone="written" labelFirst />
              <span className="separator"> · </span>
              <DetailMetric label="non-lh est" value={formatNumber(kpis.total_est_rows_written_non_lakehouse ?? 0)} tone="written" labelFirst />
            </span>
          }
          intent="neutral"
          accent="destination"
          className="volume-kpi volume-kpi-estimated-write"
          title="Estimated rows written keeps destination_rows_written intact. Lakehouse destinations use observed destination_rows_written. Non-lakehouse succeeded runs estimate rows written from source_rows_read."
        />
        <HealthStripCard
          label="Row changes"
          value={
            <span className="volume-kpi-triple-value" aria-label="inserted / updated / deleted">
              <b className="is-insert">{formatNumber(kpis.total_rows_inserted ?? 0)}</b>
              <span>/</span>
              <b className="is-update">{formatNumber(kpis.total_rows_updated ?? 0)}</b>
              <span>/</span>
              <b className="is-delete">{formatNumber(kpis.total_rows_deleted ?? 0)}</b>
            </span>
          }
          detail={<DetailMetric label="insert / update / delete" value="lakehouse" tone="neutral" />}
          intent="neutral"
          accent="destination"
          className="volume-kpi volume-kpi-row-changes"
          title="Lakehouse destination row changes: inserted / updated / deleted. Non-lakehouse destinations do not collect these write-side metrics."
        />
        <HealthStripCard
          label="Lakehouse bytes"
          value={formatBytes(netBytes)}
          detail={
            <span className="health-rate-detail">
              <DetailMetric label="added" value={formatBytes(kpis.total_bytes_added ?? 0)} tone="good" labelFirst />
              <span className="separator"> · </span>
              <DetailMetric label="removed" value={formatBytes(kpis.total_bytes_removed ?? 0)} tone="bad" labelFirst />
            </span>
          }
          intent="neutral"
          accent="storage"
          className="volume-kpi volume-kpi-lakehouse-bytes"
          title="Net lakehouse bytes = destination_bytes_added - destination_bytes_removed. Growth is context, not automatically a problem."
        />
        <HealthStripCard
          label="Files changed"
          value={
            <span className="volume-kpi-dual-value" aria-label="files added / files removed">
              <b className="is-added">{formatNumber(kpis.files_added ?? 0)}</b>
              <span>/</span>
              <b className="is-removed">{formatNumber(kpis.files_removed ?? 0)}</b>
            </span>
          }
          detail={<DetailMetric label="avg added file" value={formatBytes(kpis.avg_bytes_per_file_added ?? 0)} tone="neutral" />}
          intent="neutral"
          accent="neutral"
          className="volume-kpi volume-kpi-files-changed"
          title="Lakehouse destination file churn: files added / files removed. Useful for spotting small-file or maintenance patterns."
        />
        <HealthStripCard
          label="High-volume candidates"
          value={formatNumber(highVolumeCount)}
          detail={
            <DetailMetric label="candidate runs" value={formatNumber(candidateRunCount)} tone="amber" labelFirst />
          }
          intent="neutral"
          accent={highVolumeCount ? "warning" : "neutral"}
          className="volume-kpi volume-kpi-candidates"
          title="Distinct dataflows matching a P95 aggregate workload rule in the current filters. Candidate runs remain available as drill-down evidence."
        />
      </section>

      <div className="monitoring-volume-content report-layout-table-heavy-3">
        <section className="monitoring-volume-primary-grid">
          <ReportPanel
            title="Workload volume trend"
            titleTooltip="Rows read is universal. Estimated rows written uses observed lakehouse writes and estimates non-lakehouse succeeded writes from rows read. Lakehouse bytes added and removed provide storage context on the secondary axis."
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
            titleTooltip="Signed bars show net lakehouse byte movement by time bucket. Tooltips include added/removed bytes and files."
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
            titleTooltip="Groups non-maintenance dataflow runs by operation_type and destination load/operation type. Rows read and estimated rows written are grouped for comparison and are not stacked into one total."
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

        <ReportPanel
          title="Dataflow volume registry"
          subtitle={`${formatNumber(totalRows)} dataflows · totals in current filters`}
          className="monitoring-volume-runs-panel"
          titleTooltip="One row per dataflow in the current filters. Totals are aggregated across related runs; click a row to inspect volume evidence and individual runs."
          headerAction={
            <TablePager
              limit={limit}
              offset={offset}
              loadedRows={registryRows.length}
              totalRows={totalRows}
              loading={loading}
              onPageChange={onPageChange}
              onPageSizeChange={onPageSizeChange}
            />
          }
        >
          <DataflowVolumeRegistryTable
            rows={registryRows}
            sort={sort}
            onSort={onSort}
            onInspect={onInspect}
          />
        </ReportPanel>
      </div>
    </div>
  );
}

function WorkloadVolumeTrendLegend() {
  return <VolumeChartLegend label="Workload volume trend legend" items={[
    ["Rows read", reportChartPalette.read],
    ["Est rows written", reportChartPalette.written],
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
    ["Est rows written", reportChartPalette.written]
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

function DataflowVolumeRegistryTable({
  rows,
  sort,
  onSort,
  onInspect
}: {
  rows: MonitoringRecord[];
  sort?: TableSort;
  onSort?: (sort: TableSort) => void;
  onInspect?: (row: MonitoringRecord) => void;
}) {
  return (
    <DataTable<MonitoringRecord>
      rows={rows}
      columns={[
        { key: "dataflow_name", label: "Dataflow", sortable: true, minWidth: 150, fillPriority: "normal", render: (row) => <DataflowNameCell row={row} /> },
        { key: "context", label: "Context", sortable: true, sortKey: "stage", minWidth: 108, maxWidth: 150, fillPriority: "normal", render: (row) => <DataflowContextCell row={row} /> },
        { key: "source", label: "Source", minWidth: 142, maxWidth: 220, fillPriority: "normal", render: (row) => <EndpointCell row={row} direction="source" /> },
        { key: "destination", label: "Destination", minWidth: 142, maxWidth: 220, fillPriority: "normal", render: (row) => <EndpointCell row={row} direction="destination" /> },
        { key: "run_count", label: "Runs", sortable: true, autoFit: true, minWidth: 54, maxWidth: 82, render: (row) => <CompactValue value={formatNumber(num(row, "run_count"))} /> },
        { key: "volume_rows_read", label: "Rows read", sortable: true, autoFit: true, minWidth: 82, maxWidth: 124, render: (row) => <CompactValue value={formatNumber(num(row, "volume_rows_read"))} /> },
        { key: "volume_est_rows_written", label: "Est rows written", sortable: true, autoFit: true, minWidth: 104, maxWidth: 148, render: (row) => <CompactValue value={formatNumber(num(row, "volume_est_rows_written"))} /> },
        { key: "volume_rows_inserted", label: "Row changes", sortable: true, minWidth: 118, maxWidth: 154, render: (row) => <VolumeRowChangesCell row={row} /> },
        { key: "volume_files_changed", label: "Files", sortable: true, autoFit: true, minWidth: 82, maxWidth: 112, render: (row) => <VolumeFilesCell row={row} /> },
        { key: "volume_net_bytes", label: "Net bytes", sortable: true, autoFit: true, minWidth: 86, maxWidth: 124, render: (row) => <CompactValue value={formatBytes(num(row, "volume_net_bytes"))} /> },
        { key: "volume_candidate_reason", label: "Volume signal", minWidth: 124, fillPriority: "last", render: (row) => <VolumeReasonCell row={row} /> }
      ]}
      maxRows={rows.length}
      sort={sort}
      onSort={onSort}
      onRowClick={onInspect}
      className="monitoring-volume-table"
      fixedLayout
    />
  );
}

function VolumeRowChangesCell({ row }: { row: MonitoringRecord }) {
  const inserted = num(row, "volume_rows_inserted");
  const updated = num(row, "volume_rows_updated");
  const deleted = num(row, "volume_rows_deleted");
  const title = `Inserted / updated / deleted: ${formatNumber(inserted)} / ${formatNumber(updated)} / ${formatNumber(deleted)}`;
  return (
    <span className="volume-row-changes-inline" title={title}>
      <span className="is-insert">{formatNumber(inserted)}</span><i>/</i>
      <span className="is-update">{formatNumber(updated)}</span><i>/</i>
      <span className="is-delete">{formatNumber(deleted)}</span>
    </span>
  );
}

function VolumeFilesCell({ row }: { row: MonitoringRecord }) {
  const added = num(row, "volume_files_added");
  const removed = num(row, "volume_files_removed");
  const title = `Files added / removed: ${formatNumber(added)} / ${formatNumber(removed)}`;
  return (
    <span className="volume-files-changed-inline" title={title}>
      <span className="is-added">{formatNumber(added)}</span><i>/</i>
      <span className="is-removed">{formatNumber(removed)}</span>
    </span>
  );
}

function VolumeReasonCell({ row }: { row: MonitoringRecord }) {
  const reason = String(row.volume_candidate_reason ?? "");
  if (!reason) return <span className="performance-reason" title="No high-volume rule matched.">-</span>;
  return <span className="performance-reason performance-reason-warning" title={reason}>{reason}</span>;
}

function VolumeRoutePanel({ rows }: { rows: Array<Record<string, string | number | null>> }) {
  const visible = rows.filter(hasVolumeRouteSignal);
  if (!visible.length) return <div className="table-empty">No route volume signals in current filters.</div>;
  return (
    <div className="dataflow-signal-list dataflow-endpoint-route-list volume-route-list">
      {visible.map((row, index) => {
        const source = routeEndpoint(row, "source");
        const destination = routeEndpoint(row, "destination");
        const rowsRead = num(row, "rows_read");
        const estRowsWritten = num(row, "est_rows_written");
        const rowsWritten = num(row, "rows_written");
        const bytesAdded = num(row, "bytes_added");
        const bytesRemoved = num(row, "bytes_removed");
        const filesChanged = num(row, "files_added") + num(row, "files_removed");
        const netBytes = bytesAdded - bytesRemoved;
        const hasStorageBytes = bytesAdded > 0 || bytesRemoved > 0;
        const destinationTone = estRowsWritten > 0
          ? "estimate"
          : rowsWritten > 0
            ? "observed"
            : hasStorageBytes
              ? "bytes"
              : filesChanged > 0
                ? "files"
                : "neutral";
        const destinationValue = estRowsWritten > 0
          ? formatNumber(estRowsWritten)
          : rowsWritten > 0
            ? formatNumber(rowsWritten)
            : hasStorageBytes
              ? formatBytesShort(bytesAdded + bytesRemoved)
              : filesChanged > 0
                ? formatNumber(filesChanged)
                : "—";
        const destinationLabel = estRowsWritten > 0
          ? "Est rows written"
          : rowsWritten > 0
            ? "LH rows written"
            : hasStorageBytes
              ? "Bytes changed"
              : filesChanged > 0
                ? "Files changed"
                : "No destination volume";
        const destinationTitle = [
          `${destinationLabel}: ${destinationValue}`,
          estRowsWritten > 0 && rowsWritten > 0 ? `Observed lakehouse rows written: ${formatNumber(rowsWritten)}` : "",
          hasStorageBytes ? `Net bytes: ${formatSignedBytes(netBytes)}` : "",
          filesChanged > 0 ? `Files changed: ${formatNumber(filesChanged)}` : ""
        ].filter(Boolean).join(" · ");
        const storageContext = netBytes === 0 ? formatBytes(0) : `Δ ${formatSignedBytes(netBytes)}`;
        return (
          <div
            key={`${source.connection}-${destination.connection}-${index}`}
            className="dataflow-signal-row dataflow-endpoint-route-row"
            title={[
              `Source connection: ${source.connection}`,
              `Destination connection: ${destination.connection}`,
              `Runs: ${formatNumber(num(row, "runs"))}`,
              `Skipped: ${formatNumber(num(row, "skipped"))}`,
              `Rows read: ${formatNumber(rowsRead)}`,
              `Estimated rows written: ${formatNumber(estRowsWritten)}`,
              `Observed lakehouse rows written: ${formatNumber(rowsWritten)}`,
              `Lakehouse bytes added / removed: ${formatBytes(bytesAdded)} / ${formatBytes(bytesRemoved)}`,
              `Lakehouse files added / removed: ${formatNumber(num(row, "files_added"))} / ${formatNumber(num(row, "files_removed"))}`
            ].join("\n")}
          >
            <div className="dataflow-route-flow">
              <div className="dataflow-route-line">
                <EndpointRouteNode endpoint={source} />
                <span className="dataflow-route-arrow" aria-hidden="true">→</span>
                <EndpointRouteNode endpoint={destination} />
              </div>
            </div>
            <div className="dataflow-route-health">
              <strong className="volume-route-read-value" title={`Rows read: ${formatNumber(rowsRead)}`}>{formatNumber(rowsRead)}</strong>
              <small title={`Rows read: ${formatNumber(rowsRead)} · Runs: ${formatNumber(num(row, "runs"))}`}>{formatNumber(num(row, "runs"))} runs</small>
            </div>
            <div className="dataflow-route-volume">
              <strong className={`volume-route-destination-value volume-route-value-${destinationTone}`} title={destinationTitle}>{destinationValue}</strong>
              <small title={destinationTitle} className="volume-route-context">{storageContext}</small>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function hasVolumeRouteSignal(row: Record<string, string | number | null>) {
  return (
    num(row, "rows_read") > 0 ||
    num(row, "est_rows_written") > 0 ||
    num(row, "rows_written") > 0 ||
    num(row, "bytes_added") > 0 ||
    num(row, "bytes_removed") > 0 ||
    num(row, "files_added") > 0 ||
    num(row, "files_removed") > 0
  );
}

function formatSignedBytes(value: number) {
  const formatted = formatBytes(value);
  return value > 0 ? `+${formatted}` : formatted;
}

function routeEndpoint(row: Record<string, unknown>, direction: "source" | "destination") {
  const connection = String(row[`${direction}_name`] ?? "unknown");
  const format = String(row[`${direction}_format`] ?? row[`${direction}_connection_type`] ?? "");
  const connectionType = String(row[`${direction}_connection_type`] ?? "unknown");
  return { locator: connection, connection, format, connectionType };
}

function storageDeltaTrendOption(report: MonitoringReport, filters: MonitoringFilters, timezoneName: string, showLegend = true): EChartsOption {
  const visible = volumeTrendRows(report, filters, timezoneName);
  if (!visible.length) return emptyChartOption("No lakehouse storage delta in current filters.");
  const netByteValues = visible.map((row) => num(row, "net_bytes"));
  const fileChangeValues = visible.map((row) => num(row, "files_added") + num(row, "files_removed"));
  const axisBounds = alignedVolumeAxisBounds(netByteValues, fileChangeValues);
  return baseChartOption({
    tooltip: {
      trigger: "axis",
      formatter: (params: any) => {
        const first = Array.isArray(params) ? params[0] : params;
        const row = visible[Number(first?.dataIndex ?? 0)] ?? {};
        return [
          `<strong>${row.bucket || row.date || ""}</strong>`,
          timezoneName ? `Timezone: ${timezoneName}` : "",
          `Net bytes: ${formatBytes(num(row, "net_bytes"))}`,
          `Bytes added: ${formatBytes(num(row, "bytes_added"))}`,
          `Bytes removed: ${formatBytes(num(row, "bytes_removed"))}`,
          `Files added / removed: ${formatNumber(row.files_added)} / ${formatNumber(row.files_removed)}`
        ].filter(Boolean).join("<br/>");
      }
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
      lineSeries("Files changed", fileChangeValues, reportChartPalette.blue, 1)
    ]
  });
}

function workloadMixOption(rows: Array<Record<string, string | number | null>>, showLegend = true): EChartsOption {
  const visible = rows
    .filter((row) => !isMaintenanceWorkload(row))
    .slice()
    .sort((left, right) => num(right, "rows_read") - num(left, "rows_read"));
  if (!visible.length) return emptyChartOption("No workload mix in current filters.");
  const labels = visible.map((row) => String(row.workload_type ?? row.load_type ?? "unknown"));
  const dataZoom = horizontalBarDataZoom(visible.length);
  return baseChartOption({
    tooltip: {
      trigger: "item",
      formatter: (params: any) => {
        const row = visible[Number(params?.dataIndex ?? 0)] ?? {};
        return [
          `<strong>${labels[Number(params?.dataIndex ?? 0)] ?? "unknown"}</strong>`,
          `Runs: ${formatNumber(num(row, "runs") || num(row, "count"))}`,
          `Rows read: ${formatNumber(num(row, "rows_read"))}`,
          `Estimated rows written: ${formatNumber(num(row, "est_rows_written"))}`
        ].join("<br/>");
      }
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
      { name: "Rows read", type: "bar", barGap: "20%", itemStyle: { color: reportChartPalette.read, borderRadius: 2 }, data: visible.map((row) => num(row, "rows_read")) },
      { name: "Est rows written", type: "bar", itemStyle: { color: reportChartPalette.written, borderRadius: 2 }, data: visible.map((row) => num(row, "est_rows_written")) }
    ]
  });
}

function isMaintenanceWorkload(row: Record<string, string | number | null>) {
  const workloadType = String(row.workload_type ?? row.load_type ?? "").toLowerCase();
  return workloadType.includes("maintenance");
}

function topDataflowsByWorkloadOption(rows: Array<Record<string, string | number | null>>): EChartsOption {
  const visible = rows.slice().sort((left, right) => num(right, "value") - num(left, "value"));
  if (!visible.length) return emptyChartOption("No top dataflow workload in current filters.");
  const labels = visible.map((row) => String(row.name ?? row.dataflow_id ?? "unknown"));
  const dataZoom = horizontalBarDataZoom(visible.length);
  return baseChartOption({
    tooltip: {
      trigger: "item",
      formatter: (params: any) => {
        const row = visible[Number(params?.dataIndex ?? 0)] ?? {};
        return [
          `<strong>${row.name ?? "unknown"}</strong>`,
          `Dataflow id: ${row.dataflow_id ?? "-"}`,
          `Rows read: ${formatNumber(num(row, "value"))}`,
          `Runs: ${formatNumber(num(row, "count"))}`
        ].join("<br/>");
      }
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
        data: visible.map((row) => num(row, "value"))
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
    est_rows_written: num(row, "est_rows_written"),
    rows_inserted: num(row, "rows_inserted"),
    rows_updated: num(row, "rows_updated"),
    rows_deleted: num(row, "rows_deleted"),
    files_added: num(row, "files_added"),
    files_removed: num(row, "files_removed"),
    net_bytes: num(row, "net_bytes") || num(row, "bytes_added") - num(row, "bytes_removed")
  }));
}

function lineSeries(name: string, data: number[], color: string, yAxisIndex = 0) {
  return {
    name,
    type: "line" as const,
    yAxisIndex,
    connectNulls: true,
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
