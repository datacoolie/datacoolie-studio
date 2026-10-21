import type { MonitoringRecord } from "../../../shared/api/domainTypes";
import { DataTable, StatusCell, formatSeconds, num, type TableSort } from "../../../shared/components/DataTable";
import { Help } from "../../../shared/components/Help";
import { CompactBytesValue, CompactNumberValue, TablePager, metricDifference, metricValue, toApproximateNumber } from "../components/monitoringPresentation";
import { formatTooltipBytes, formatTooltipNumber, type MetricValue } from "../../../shared/lib/numberFormat";
import { FreshnessIdentitySection, FreshnessRunTimeCell, GroupedDetailCard, hasValue } from "./detailPrimitives";

export function VolumeDetailSections({
  row,
  relatedDataflows,
  total,
  offset,
  limit,
  sort,
  onSort,
  onPageChange,
  onPageSizeChange,
  onOpenDataflow,
  timezoneName,
}: {
  row: Record<string, unknown>;
  relatedDataflows: MonitoringRecord[];
  total: number;
  offset: number;
  limit: number;
  sort: TableSort;
  onSort: (sort: TableSort) => void;
  onPageChange: (offset: number) => void;
  onPageSizeChange: (limit: number) => void;
  onOpenDataflow?: (row: MonitoringRecord) => void;
  timezoneName?: string | null;
}) {
  const hasVolumeEvidence = num(row, "candidate_run_count") > 0
    || hasValue(row.volume_candidate_reason)
    || (Array.isArray(row.volume_candidate_signals) && row.volume_candidate_signals.length > 0);
  return (
    <>
      <FreshnessIdentitySection row={row} />
      <section className="monitoring-detail-section monitoring-volume-summary-section">
        <h3>Volume summary</h3>
        <div className="monitoring-freshness-summary-grid monitoring-volume-summary-grid">
          <GroupedDetailCard
            title="Workload totals"
            rows={[
              ["Runs", <CompactNumberValue value={metricValue(row, "run_count")} />],
              ["Rows read", <CompactNumberValue value={metricValue(row, "volume_rows_read")} />],
              ["Rows written", <CompactNumberValue value={metricValue(row, "volume_rows_written")} />],
              ["Lakehouse rows written", <CompactNumberValue value={metricValue(row, "volume_lakehouse_rows_written")} />],
            ]}
            showEmpty
            className="monitoring-volume-summary-card is-workload"
          />
          <GroupedDetailCard
            title="Lakehouse changes"
            rows={[
              ["Inserted / updated / deleted", <VolumeAggregateRowChanges row={row} />],
              ["Files added / removed", <VolumeAggregateFiles row={row} />],
              ["Bytes added / removed", <VolumeAggregateBytes row={row} />],
              ["Net bytes", <VolumeNetBytes value={metricValue(row, "volume_net_bytes")} />],
            ]}
            showEmpty
            className="monitoring-volume-summary-card is-storage"
          />
          <GroupedDetailCard
            title="Per-run profile"
            rows={[
              ["Average rows read", <CompactNumberValue value={metricValue(row, "avg_rows_read")} />],
              ["Average rows written", <CompactNumberValue value={metricValue(row, "avg_rows_written")} />],
              ["Peak rows read", <CompactNumberValue value={metricValue(row, "peak_rows_read")} />],
              ["P95 rows read", <CompactNumberValue value={metricValue(row, "p95_rows_read")} />],
              ["Average duration", formatSeconds(num(row, "avg_duration_seconds"))],
              ["P95 duration", formatSeconds(num(row, "p95_duration_seconds"))],
            ]}
            showEmpty
            className="monitoring-volume-summary-card is-profile"
          />
          <GroupedDetailCard
            title="Volume evidence"
            rows={[
              ["Primary signal", row.volume_candidate_reason || "No P95 aggregate signal"],
              ["Candidate runs", <CompactNumberValue value={num(row, "candidate_run_count")} />],
              ["Matched signals", <VolumeSignalList value={row.volume_candidate_signals} />],
            ]}
            showEmpty
            className={`monitoring-volume-summary-card is-evidence${hasVolumeEvidence ? " has-evidence" : ""}`}
          />
        </div>
      </section>
      <section className="monitoring-detail-section monitoring-freshness-runs-section">
        <div className="monitoring-detail-section-header monitoring-freshness-runs-header">
          <h3>Dataflow runs</h3>
          <TablePager
            limit={limit}
            offset={offset}
            loadedRows={relatedDataflows.length}
            totalRows={total}
            loading={false}
            onPageChange={onPageChange}
            onPageSizeChange={onPageSizeChange}
          />
        </div>
        <DataTable
          rows={relatedDataflows}
          compactNumbers
          compactNumberStyle="infix"
          columns={[
            { key: "start_time", label: "Time", sortable: true, autoFit: true, minWidth: 144, maxWidth: 216, render: (run) => <FreshnessRunTimeCell row={run} timezoneName={timezoneName} /> },
            { key: "status", label: "Status", sortable: true, autoFit: true, minWidth: 76, maxWidth: 104, render: (run) => <StatusCell row={run} /> },
            { key: "source_rows_read", label: "Rows read", sortable: true, autoFit: true, minWidth: 82, maxWidth: 128, render: (run) => <CompactNumberValue value={metricValue(run, "source_rows_read")} /> },
            { key: "destination_rows_written", label: "Rows written", sortable: true, autoFit: true, minWidth: 104, maxWidth: 148, render: (run) => <CompactNumberValue value={metricValue(run, "destination_rows_written")} /> },
            { key: "destination_rows_inserted", label: "Row changes", sortable: true, autoFit: true, minWidth: 104, maxWidth: 148, render: (run) => <VolumeRunRowChangesCell row={run} /> },
            { key: "destination_files_added", label: "Files + / −", sortable: true, autoFit: true, minWidth: 84, maxWidth: 112, render: (run) => <VolumeRunFilesCell row={run} /> },
            { key: "destination_bytes_added", label: "Net bytes", sortable: true, autoFit: true, minWidth: 86, maxWidth: 124, render: (run) => <CompactBytesValue value={metricDifference(metricValue(run, "destination_bytes_added"), metricValue(run, "destination_bytes_removed"))} /> },
            { key: "duration_seconds", label: "Duration", sortable: true, autoFit: true, minWidth: 76, maxWidth: 104, render: (run) => formatSeconds(num(run, "duration_seconds")) },
          ]}
          maxRows={limit}
          offset={0}
          onRowClick={onOpenDataflow}
          sort={sort}
          onSort={onSort}
          fixedLayout
          className="monitoring-child-dataflows-table monitoring-volume-run-table"
          timezoneName={timezoneName}
        />
      </section>
    </>
  );
}

export function VolumeAggregateRowChanges({ row }: { row: Record<string, unknown> }) {
  const inserted = metricValue(row, "volume_rows_inserted");
  const updated = metricValue(row, "volume_rows_updated");
  const deleted = metricValue(row, "volume_rows_deleted");
  return (
    <Help className="monitoring-volume-change-values" title={`Inserted / updated / deleted: ${formatTooltipNumber(inserted)} / ${formatTooltipNumber(updated)} / ${formatTooltipNumber(deleted)}`}>
      <span className="is-positive"><CompactNumberValue value={inserted} /></span><i>/</i>
      <span className="is-warning"><CompactNumberValue value={updated} /></span><i>/</i>
      <span className="is-negative"><CompactNumberValue value={deleted} /></span>
    </Help>
  );
}

export function VolumeAggregateFiles({ row }: { row: Record<string, unknown> }) {
  const added = metricValue(row, "volume_files_added");
  const removed = metricValue(row, "volume_files_removed");
  return (
    <Help className="monitoring-volume-change-values" title={`Files added / removed: ${formatTooltipNumber(added)} / ${formatTooltipNumber(removed)}`}>
      <span className="is-positive"><CompactNumberValue value={added} /></span><i>/</i>
      <span className="is-negative"><CompactNumberValue value={removed} /></span>
    </Help>
  );
}

export function VolumeAggregateBytes({ row }: { row: Record<string, unknown> }) {
  const added = metricValue(row, "volume_bytes_added");
  const removed = metricValue(row, "volume_bytes_removed");
  return (
    <Help className="monitoring-volume-change-values" title={`Bytes added / removed: ${formatTooltipBytes(added)} / ${formatTooltipBytes(removed)}`}>
      <span className="is-positive"><CompactBytesValue value={added} /></span><i>/</i>
      <span className="is-negative"><CompactBytesValue value={removed} /></span>
    </Help>
  );
}

export function VolumeNetBytes({ value }: { value: MetricValue }) {
  const approximate = toApproximateNumber(value);
  const tone = approximate === null ? "is-neutral" : approximate > 0 ? "is-positive" : approximate < 0 ? "is-negative" : "is-neutral";
  return <CompactBytesValue value={value} className={`monitoring-volume-net-bytes ${tone}`} />;
}

export function VolumeSignalList({ value }: { value: unknown }) {
  if (!Array.isArray(value) || !value.length) return <span>-</span>;
  return (
    <span className="monitoring-volume-signal-list">
      {value.map((signal, index) => {
        const item = signal && typeof signal === "object" ? signal as Record<string, unknown> : {};
        const label = String(item.label ?? item.kind ?? "Signal");
        const ratio = Number(item.ratio ?? 0);
        return <span key={`${label}-${index}`}>{label}{ratio > 0 ? ` · ${ratio.toFixed(2)}× P95` : ""}</span>;
      })}
    </span>
  );
}

export function VolumeRunRowChangesCell({ row }: { row: MonitoringRecord }) {
  const inserted = metricValue(row, "destination_rows_inserted");
  const updated = metricValue(row, "destination_rows_updated");
  const deleted = metricValue(row, "destination_rows_deleted");
  return (
    <Help className="volume-row-changes-inline" title={`Inserted / updated / deleted: ${formatTooltipNumber(inserted)} / ${formatTooltipNumber(updated)} / ${formatTooltipNumber(deleted)}`}>
      <span className="is-insert"><CompactNumberValue value={inserted} /></span><i>/</i>
      <span className="is-update"><CompactNumberValue value={updated} /></span><i>/</i>
      <span className="is-delete"><CompactNumberValue value={deleted} /></span>
    </Help>
  );
}

export function VolumeRunFilesCell({ row }: { row: MonitoringRecord }) {
  const added = metricValue(row, "destination_files_added");
  const removed = metricValue(row, "destination_files_removed");
  const title = `Files added / removed: ${formatTooltipNumber(added)} / ${formatTooltipNumber(removed)}`;
  return (
    <Help className="volume-files-changed-inline" title={title}>
      <span className="is-added"><CompactNumberValue value={added} /></span><i>/</i>
      <span className="is-removed"><CompactNumberValue value={removed} /></span>
    </Help>
  );
}
