import { ArrowLeft, ArrowRight, Boxes, BriefcaseBusiness, Check, ChevronRight, Clock3, Copy, FileText, SearchCheck, Workflow, X } from "lucide-react";
import { isValidElement, useEffect, useMemo, useState, type CSSProperties, type ReactNode } from "react";
import { createPortal } from "react-dom";
import type { MonitoringRecord } from "../../../shared/api/domainTypes";
import { useDrawerEscape } from "../../../shared/hooks/useDrawerEscape";
import { formatTimestampForDisplay, hasExplicitTimezone, isTimestampFieldName } from "../../../shared/time";
import { lifecycleStatusFromField, lifecycleStatusPresentation, type LifecycleStatus } from "../../../shared/statusPresentation";
import { LineageFormatIcon } from "../../lineage/components/LineageFormatIcon";
import { DataTable, StatusCell, display, formatBytes, formatNumber, formatSeconds, num, type TableSort } from "../MonitoringCharts";
import {
  diagnosticsCategoryLabel,
  diagnosticsEvidenceItems,
  diagnosticsInvestigationActions,
  diagnosticsLinkedJobRow,
  diagnosticsRuleDescription,
  diagnosticsSeverityPresentation,
} from "../diagnosticsPresentation";
import { formatMaintenanceLag, maintenanceFormatIconKind, maintenanceTableHealthClass, maintenanceTableHealthLabel, maintenanceTableHealthTone } from "../maintenancePresentation";
import { formatPhasePercent, monitoringEndpointPresentation, TablePager } from "../components/monitoringPrimitives";
import { SystemLogViewer } from "../SystemLogViewer";
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
              ["Runs", formatNumber(num(row, "run_count"))],
              ["Rows read", formatNumber(num(row, "volume_rows_read"))],
              ["Est rows written", formatNumber(num(row, "volume_est_rows_written"))],
              ["Observed lakehouse rows", formatNumber(num(row, "volume_lakehouse_rows_written"))],
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
              ["Net bytes", <VolumeNetBytes value={num(row, "volume_net_bytes")} />],
            ]}
            showEmpty
            className="monitoring-volume-summary-card is-storage"
          />
          <GroupedDetailCard
            title="Per-run profile"
            rows={[
              ["Average rows read", formatNumber(num(row, "avg_rows_read"))],
              ["Average est rows written", formatNumber(num(row, "avg_est_rows_written"))],
              ["Peak rows read", formatNumber(num(row, "peak_rows_read"))],
              ["P95 rows read", formatNumber(num(row, "p95_rows_read"))],
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
              ["Candidate runs", formatNumber(num(row, "candidate_run_count"))],
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
          columns={[
            { key: "start_time", label: "Time", sortable: true, autoFit: true, minWidth: 144, maxWidth: 216, render: (run) => <FreshnessRunTimeCell row={run} timezoneName={timezoneName} /> },
            { key: "status", label: "Status", sortable: true, autoFit: true, minWidth: 76, maxWidth: 104, render: (run) => <StatusCell row={run} /> },
            { key: "source_rows_read", label: "Rows read", sortable: true, autoFit: true, minWidth: 82, maxWidth: 128, render: (run) => formatNumber(num(run, "source_rows_read")) },
            { key: "volume_est_rows_written", label: "Est rows written", sortable: true, autoFit: true, minWidth: 104, maxWidth: 148, render: (run) => formatNumber(num(run, "volume_est_rows_written")) },
            { key: "destination_rows_inserted", label: "Row changes", sortable: true, autoFit: true, minWidth: 104, maxWidth: 148, render: (run) => <VolumeRunRowChangesCell row={run} /> },
            { key: "destination_files_added", label: "Files + / −", sortable: true, autoFit: true, minWidth: 84, maxWidth: 112, render: (run) => <VolumeRunFilesCell row={run} /> },
            { key: "destination_bytes_added", label: "Net bytes", sortable: true, autoFit: true, minWidth: 86, maxWidth: 124, render: (run) => formatBytes(num(run, "destination_bytes_added") - num(run, "destination_bytes_removed")) },
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
  const inserted = formatNumber(num(row, "volume_rows_inserted"));
  const updated = formatNumber(num(row, "volume_rows_updated"));
  const deleted = formatNumber(num(row, "volume_rows_deleted"));
  return (
    <span className="monitoring-volume-change-values" title={`Inserted / updated / deleted: ${inserted} / ${updated} / ${deleted}`}>
      <span className="is-positive">{inserted}</span><i>/</i>
      <span className="is-warning">{updated}</span><i>/</i>
      <span className="is-negative">{deleted}</span>
    </span>
  );
}

export function VolumeAggregateFiles({ row }: { row: Record<string, unknown> }) {
  const added = formatNumber(num(row, "volume_files_added"));
  const removed = formatNumber(num(row, "volume_files_removed"));
  return (
    <span className="monitoring-volume-change-values" title={`Files added / removed: ${added} / ${removed}`}>
      <span className="is-positive">{added}</span><i>/</i>
      <span className="is-negative">{removed}</span>
    </span>
  );
}

export function VolumeAggregateBytes({ row }: { row: Record<string, unknown> }) {
  const added = formatBytes(num(row, "volume_bytes_added"));
  const removed = formatBytes(num(row, "volume_bytes_removed"));
  return (
    <span className="monitoring-volume-change-values" title={`Bytes added / removed: ${added} / ${removed}`}>
      <span className="is-positive">{added}</span><i>/</i>
      <span className="is-negative">{removed}</span>
    </span>
  );
}

export function VolumeNetBytes({ value }: { value: number }) {
  const tone = value > 0 ? "is-positive" : value < 0 ? "is-negative" : "is-neutral";
  return <span className={`monitoring-volume-net-bytes ${tone}`}>{formatBytes(value)}</span>;
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
  const inserted = num(row, "destination_rows_inserted");
  const updated = num(row, "destination_rows_updated");
  const deleted = num(row, "destination_rows_deleted");
  const value = `${formatNumber(inserted)} / ${formatNumber(updated)} / ${formatNumber(deleted)}`;
  return (
    <span className="volume-row-changes-inline" title={`Inserted / updated / deleted: ${value}`}>
      <span className="is-insert">{formatNumber(inserted)}</span><i>/</i>
      <span className="is-update">{formatNumber(updated)}</span><i>/</i>
      <span className="is-delete">{formatNumber(deleted)}</span>
    </span>
  );
}

export function VolumeRunFilesCell({ row }: { row: MonitoringRecord }) {
  const added = num(row, "destination_files_added");
  const removed = num(row, "destination_files_removed");
  const title = `Files added / removed: ${formatNumber(added)} / ${formatNumber(removed)}`;
  return (
    <span className="volume-files-changed-inline" title={title}>
      <span className="is-added">{formatNumber(added)}</span><i>/</i>
      <span className="is-removed">{formatNumber(removed)}</span>
    </span>
  );
}
