import { useEffect, useState } from "react";
import type { EChartsOption } from "echarts";
import type { MonitoringRecord, MonitoringReport } from "../../../shared/api/types";
import type { MonitoringFilters } from "../monitoringFilters";
import {
  CompactValue,
  CopyableText,
  DataTable,
  DataflowContextCell,
  DataflowNameCell,
  DetailMetric,
  EndpointCell,
  EndpointRouteNode,
  HealthStripCard,
  ReportChart,
  ReportPanel,
  TableDateTimeValue,
  TablePager,
  baseChartOption,
  bottomAnchoredValueXAxis,
  compactRunId,
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
  resolveTrendBucketKeys
} from "../monitoringShared";

const VOLUME_PAGE_SIZE = 100;

export function VolumePage({
  report,
  filters,
  rows,
  onInspect
}: {
  report: MonitoringReport;
  filters: MonitoringFilters;
  rows?: MonitoringRecord[];
  onInspect?: (row: MonitoringRecord) => void;
}) {
  const kpis = report.volume.kpis ?? {};
  const timezoneName = monitoringTimezone(report);
  const investigationRows = ((rows ?? report.volume.investigation_queue ?? []) as MonitoringRecord[])
    .slice()
    .sort((left, right) => volumeRunTimeValue(right) - volumeRunTimeValue(left));
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState(VOLUME_PAGE_SIZE);

  useEffect(() => {
    setOffset(0);
  }, [report]);

  const visibleRows = investigationRows.slice(offset, offset + limit);
  const netBytes = Number(kpis.net_bytes_change ?? 0);
  const highVolumeCount = Number(kpis.high_volume_run_count ?? 0);

  return (
    <div className="monitoring-page monitoring-volume-report">
      <section className="overview-health-strip monitoring-volume-health-strip">
        <HealthStripCard
          label="Rows read"
          value={formatNumber(kpis.total_rows_read ?? 0)}
          detail={<DetailMetric label="runs" value={formatNumber(report.summary.dataflow_records ?? 0)} tone="neutral" />}
          intent="neutral"
          title="Universal workload signal. source_rows_read is collected for all dataflow runs."
        />
        <HealthStripCard
          label="Est rows written"
          value={formatNumber(kpis.total_est_rows_written ?? 0)}
          detail={
            <span className="health-rate-detail">
              <DetailMetric label="lakehouse obs" value={formatNumber(kpis.total_rows_written ?? 0)} tone="blue" labelFirst />
              <span className="separator"> · </span>
              <DetailMetric label="non-lh est" value={formatNumber(kpis.total_est_rows_written_non_lakehouse ?? 0)} tone="neutral" labelFirst />
            </span>
          }
          intent="neutral"
          title="Estimated rows written keeps destination_rows_written intact. Lakehouse destinations use observed destination_rows_written. Non-lakehouse succeeded runs estimate rows written from source_rows_read."
        />
        <HealthStripCard
          label="Row changes"
          value={`${formatNumber(kpis.total_rows_inserted ?? 0)} / ${formatNumber(kpis.total_rows_updated ?? 0)} / ${formatNumber(kpis.total_rows_deleted ?? 0)}`}
          detail={<DetailMetric label="insert / update / delete" value="lakehouse" tone="neutral" />}
          intent={Number(kpis.total_rows_deleted ?? 0) ? "warning" : "neutral"}
          title="Lakehouse destination row changes: inserted / updated / deleted. Non-lakehouse destinations do not collect these write-side metrics."
        />
        <HealthStripCard
          label="Lakehouse bytes"
          value={formatBytes(netBytes)}
          detail={
            <span className="health-rate-detail">
              <DetailMetric label="added" value={formatBytes(kpis.total_bytes_added ?? 0)} tone="blue" labelFirst />
              <span className="separator"> · </span>
              <DetailMetric label="removed" value={formatBytes(kpis.total_bytes_removed ?? 0)} tone="bad" labelFirst />
            </span>
          }
          intent="neutral"
          title="Net lakehouse bytes = destination_bytes_added - destination_bytes_removed. Growth is context, not automatically a problem."
        />
        <HealthStripCard
          label="Files changed"
          value={`${formatNumber(kpis.files_added ?? 0)} / ${formatNumber(kpis.files_removed ?? 0)}`}
          detail={<DetailMetric label="avg added file" value={formatBytes(kpis.avg_bytes_per_file_added ?? 0)} tone="neutral" />}
          intent="neutral"
          title="Lakehouse destination file churn: files added / files removed. Useful for spotting small-file or maintenance patterns."
        />
        <HealthStripCard
          label="High-volume runs"
          value={formatNumber(highVolumeCount)}
          detail={
            <span className="health-rate-detail">
              <DetailMetric label="read" value={formatNumber(kpis.high_volume_rows_count ?? 0)} tone="blue" labelFirst />
              <span className="separator"> · </span>
              <DetailMetric label="est write" value={formatNumber(kpis.high_volume_est_rows_count ?? 0)} tone="purple" labelFirst />
              <span className="separator"> · </span>
              <DetailMetric label="bytes/files" value={`${formatNumber(kpis.high_volume_bytes_count ?? 0)} / ${formatNumber(kpis.high_volume_files_count ?? 0)}`} tone="amber" labelFirst />
            </span>
          }
          intent={highVolumeCount ? "warning" : "good"}
          title="High-volume runs are records at or above the P95 threshold in current filters for rows read, estimated rows written, lakehouse rows written, net lakehouse bytes, or lakehouse file churn."
        />
      </section>

      <div className="monitoring-volume-content report-layout-table-heavy-3">
        <section className="monitoring-volume-primary-grid">
          <ReportPanel
            title="Workload volume trend"
            subtitle="rows read and estimated write rows"
            titleTooltip="Rows read is universal. Estimated rows written uses observed lakehouse writes and estimates non-lakehouse succeeded writes from rows read."
          >
            <ReportChart
              option={workloadVolumeTrendOption(report, filters, timezoneName)}
              height="100%"
            />
          </ReportPanel>
          <ReportPanel
            title="Lakehouse storage delta trend"
            subtitle="bytes and files added / removed"
            titleTooltip="Signed bars show net lakehouse byte movement by time bucket. Tooltips include added/removed bytes and files."
          >
            <ReportChart
              option={storageDeltaTrendOption(report, filters, timezoneName)}
              height="100%"
            />
          </ReportPanel>
        </section>

        <section className="monitoring-volume-secondary-grid">
          <ReportPanel
            title="Workload mix"
            subtitle="operation and load context"
            titleTooltip="Groups non-maintenance dataflow runs by operation_type and destination load/operation type. Bars show only rows read and estimated rows written."
          >
            <ReportChart
              option={workloadMixOption(report.volume.volume_by_workload_type ?? report.volume.volume_by_load_type ?? [])}
              height="100%"
              wheelDataZoomStep={1}
            />
          </ReportPanel>
          <ReportPanel
            title="Source to destination volume"
            subtitle="all routes by read workload"
            titleTooltip="Groups all dataflow runs by source and destination connection pair in current filters, ranked by rows read first because it is the universal workload signal."
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
          title="Volume investigation queue"
          className="monitoring-volume-runs-panel"
          titleTooltip="Prioritized dataflow runs sorted by high-volume rule match, rows read, estimated rows written, lakehouse rows, net bytes, and latest time."
          headerAction={
            <TablePager
              limit={limit}
              offset={offset}
              loadedRows={visibleRows.length}
              totalRows={investigationRows.length}
              loading={false}
              onPageChange={setOffset}
              onPageSizeChange={(nextLimit) => {
                setLimit(nextLimit);
                setOffset(0);
              }}
            />
          }
        >
          <VolumeInvestigationTable rows={visibleRows} timezoneName={timezoneName} onInspect={onInspect} />
        </ReportPanel>
      </div>
    </div>
  );
}

function VolumeInvestigationTable({
  rows,
  timezoneName,
  onInspect
}: {
  rows: MonitoringRecord[];
  timezoneName?: string | null;
  onInspect?: (row: MonitoringRecord) => void;
}) {
  return (
    <DataTable<MonitoringRecord>
      rows={rows}
      columns={[
        { key: "job_id", label: "Job", sortable: true, width: 104, render: (row) => <CopyableText value={row.job_id} displayValue={compactRunId(row.job_id)} /> },
        { key: "dataflow_name", label: "Dataflow", sortable: true, width: 152, render: (row) => <DataflowNameCell row={row} /> },
        { key: "context", label: "Context", sortable: true, sortKey: "stage", width: 118, render: (row) => <DataflowContextCell row={row} /> },
        { key: "source", label: "Source", width: 146, render: (row) => <EndpointCell row={row} direction="source" /> },
        { key: "destination", label: "Destination", width: 146, render: (row) => <EndpointCell row={row} direction="destination" /> },
        { key: "volume_rows_read", label: "Rows", sortable: true, width: 132, render: (row) => <VolumeRowsCell row={row} /> },
        { key: "volume_files_changed", label: "Files", sortable: true, autoFit: true, minWidth: 82, maxWidth: 112, render: (row) => <VolumeFilesCell row={row} /> },
        { key: "volume_net_bytes", label: "Bytes", sortable: true, width: 118, render: (row) => <VolumeBytesCell row={row} /> },
        { key: "duration_seconds", label: "Duration", sortable: true, autoFit: true, minWidth: 76, maxWidth: 96, render: (row) => <CompactValue value={formatNumber(num(row, "duration_seconds"))} /> },
        { key: "end_time", label: "End", sortable: true, width: 178, render: (row) => <TableDateTimeValue value={row.end_time ?? row.start_time} timezoneName={timezoneName} /> },
        { key: "volume_candidate_reason", label: "Reason", minWidth: 112, fillPriority: "last", render: (row) => <VolumeReasonCell row={row} /> }
      ]}
      maxRows={rows.length}
      onRowClick={onInspect}
      timezoneName={timezoneName}
      className="monitoring-volume-table"
      fixedLayout
    />
  );
}

function VolumeRowsCell({ row }: { row: MonitoringRecord }) {
  const read = num(row, "volume_rows_read") || num(row, "source_rows_read");
  const estimatedWritten = num(row, "volume_est_rows_written");
  const written = num(row, "volume_lakehouse_rows_written") || num(row, "destination_rows_written");
  const inserted = num(row, "volume_rows_inserted") || num(row, "destination_rows_inserted");
  const updated = num(row, "volume_rows_updated") || num(row, "destination_rows_updated");
  const deleted = num(row, "volume_rows_deleted") || num(row, "destination_rows_deleted");
  return (
    <span
      className="monitor-stack-cell"
      title={[
        `Rows read: ${formatNumber(read)}`,
        `Estimated rows written: ${formatNumber(estimatedWritten)}`,
        `Observed lakehouse rows written: ${formatNumber(written)}`,
        `Inserted / updated / deleted: ${formatNumber(inserted)} / ${formatNumber(updated)} / ${formatNumber(deleted)}`
      ].join("\n")}
    >
      <strong>{formatNumber(read)} / {formatNumber(estimatedWritten)}</strong>
      <small>lh {formatNumber(written)}</small>
    </span>
  );
}

function VolumeFilesCell({ row }: { row: MonitoringRecord }) {
  const added = num(row, "volume_files_added") || num(row, "destination_files_added");
  const removed = num(row, "volume_files_removed") || num(row, "destination_files_removed");
  return <CompactValue value={`${formatNumber(added)} / ${formatNumber(removed)}`} />;
}

function VolumeBytesCell({ row }: { row: MonitoringRecord }) {
  const added = num(row, "volume_bytes_added") || num(row, "destination_bytes_added");
  const removed = num(row, "volume_bytes_removed") || num(row, "destination_bytes_removed");
  const net = num(row, "volume_net_bytes") || added - removed;
  return (
    <span className="monitor-stack-cell" title={`Added: ${formatBytes(added)}\nRemoved: ${formatBytes(removed)}\nNet: ${formatBytes(net)}`}>
      <strong>{formatBytes(net)}</strong>
      <small>{formatBytes(added)} / {formatBytes(removed)}</small>
    </span>
  );
}

function VolumeReasonCell({ row }: { row: MonitoringRecord }) {
  const reason = String(row.volume_candidate_reason ?? "");
  if (!reason) return <span className="performance-reason" title="No high-volume rule matched.">-</span>;
  return <span className="performance-reason performance-reason-warning" title={reason}>{reason}</span>;
}

function VolumeRoutePanel({ rows }: { rows: Array<Record<string, string | number | null>> }) {
  const visible = rows.filter((row) => num(row, "rows_read") > 0 || num(row, "est_rows_written") > 0 || num(row, "rows_written") > 0);
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
              <strong>{formatNumber(rowsRead)}</strong>
              <small>{formatNumber(num(row, "runs"))} runs</small>
            </div>
            <div className="dataflow-route-volume">
              <strong>{formatNumber(estRowsWritten)}</strong>
              <small>lh {formatNumber(rowsWritten)}</small>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function routeEndpoint(row: Record<string, unknown>, direction: "source" | "destination") {
  const connection = String(row[`${direction}_name`] ?? "unknown");
  const format = String(row[`${direction}_format`] ?? row[`${direction}_connection_type`] ?? "");
  const connectionType = String(row[`${direction}_connection_type`] ?? "unknown");
  return { locator: connection, connection, format, connectionType };
}

function workloadVolumeTrendOption(report: MonitoringReport, filters: MonitoringFilters, timezoneName: string): EChartsOption {
  const visible = volumeTrendRows(report, filters, timezoneName);
  if (!visible.length) return emptyChartOption("No workload volume trend in current filters.");
  return baseChartOption({
    tooltip: {
      trigger: "axis",
      formatter: (params: any) => {
        const first = Array.isArray(params) ? params[0] : params;
        const row = visible[Number(first?.dataIndex ?? 0)] ?? {};
        return [
          `<strong>${row.bucket || row.date || ""}</strong>`,
          row.grain ? `Grain: ${row.grain}` : "",
          timezoneName ? `Timezone: ${timezoneName}` : "",
          `Rows read: ${formatNumber(row.rows_read)}`,
          `Estimated rows written: ${formatNumber(row.est_rows_written)}`,
          `Observed lakehouse rows written: ${formatNumber(row.rows_written)}`,
          `Inserted / updated / deleted: ${formatNumber(row.rows_inserted)} / ${formatNumber(row.rows_updated)} / ${formatNumber(row.rows_deleted)}`,
          `Lakehouse bytes added: ${formatBytes(num(row, "bytes_added"))}`,
          `Lakehouse bytes removed: ${formatBytes(num(row, "bytes_removed"))}`
        ].filter(Boolean).join("<br/>");
      }
    },
    legend: { top: 0, left: "center", itemWidth: 9, itemHeight: 9, textStyle: { fontSize: 10 } },
    grid: reportChartGrid({ left: 42, right: 42, top: 28, containLabel: false }),
    xAxis: { type: "category", data: visible.map((row) => row.bucket || row.date), axisLabel: { fontSize: 10, hideOverlap: true }, axisTick: { show: false } },
    yAxis: [
      { type: "value", axisLabel: { fontSize: 10, formatter: (value: number) => formatCompact(value) }, splitLine: { lineStyle: { color: reportChartPalette.grid } } },
      { type: "value", axisLabel: { fontSize: 10, formatter: (value: number) => formatBytesShort(value) }, splitLine: { show: false } }
    ],
    series: [
      { name: "Rows read", type: "bar", itemStyle: { color: reportChartPalette.blue, borderRadius: [3, 3, 0, 0] }, data: visible.map((row) => row.rows_read) },
      { name: "Est rows written", type: "bar", itemStyle: { color: reportChartPalette.pending, borderRadius: [3, 3, 0, 0] }, data: visible.map((row) => row.est_rows_written) },
      lineSeries("Lakehouse bytes added", visible.map((row) => row.bytes_added), reportChartPalette.teal, 1),
      lineSeries("Lakehouse bytes removed", visible.map((row) => row.bytes_removed), reportChartPalette.failed, 1)
    ]
  });
}

function storageDeltaTrendOption(report: MonitoringReport, filters: MonitoringFilters, timezoneName: string): EChartsOption {
  const visible = volumeTrendRows(report, filters, timezoneName);
  if (!visible.length) return emptyChartOption("No lakehouse storage delta in current filters.");
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
    legend: { top: 0, left: "center", itemWidth: 9, itemHeight: 9, textStyle: { fontSize: 10 } },
    grid: reportChartGrid({ left: 42, right: 12, top: 28, containLabel: false }),
    xAxis: { type: "category", data: visible.map((row) => row.bucket || row.date), axisLabel: { fontSize: 10, hideOverlap: true }, axisTick: { show: false } },
    yAxis: { type: "value", axisLabel: { fontSize: 10, formatter: (value: number) => formatBytesShort(value) }, splitLine: { lineStyle: { color: reportChartPalette.grid } } },
    series: [
      {
        name: "Net lakehouse bytes",
        type: "bar",
        itemStyle: {
          color: (params: any) => Number(params?.value ?? 0) >= 0 ? reportChartPalette.teal : reportChartPalette.failed,
          borderRadius: 3
        },
        data: visible.map((row) => row.net_bytes)
      },
      lineSeries("Files changed", visible.map((row) => num(row, "files_added") + num(row, "files_removed")), reportChartPalette.blue)
    ]
  });
}

function workloadMixOption(rows: Array<Record<string, string | number | null>>): EChartsOption {
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
    legend: { top: 0, left: "center", itemWidth: 9, itemHeight: 9, textStyle: { fontSize: 10 } },
    grid: reportChartGrid({ left: 110, right: dataZoom ? 24 : 10, top: 28, containLabel: false }),
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
      { name: "Rows read", type: "bar", stack: "volume", itemStyle: { color: reportChartPalette.blue, borderRadius: 2 }, data: visible.map((row) => num(row, "rows_read")) },
      { name: "Est rows written", type: "bar", stack: "volume", itemStyle: { color: reportChartPalette.pending, borderRadius: 2 }, data: visible.map((row) => num(row, "est_rows_written")) }
    ]
  });
}

function isMaintenanceWorkload(row: Record<string, string | number | null>) {
  const workloadType = String(row.workload_type ?? row.load_type ?? "").toLowerCase();
  return workloadType.includes("maintenance");
}

function volumeRunTimeValue(row: MonitoringRecord) {
  const value = row.end_time ?? row.start_time;
  if (typeof value !== "string") return 0;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : 0;
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
        itemStyle: { color: reportChartPalette.blue, borderRadius: [0, 3, 3, 0] },
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
