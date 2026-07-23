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
import { ErrorMessageBlock, GroupedDetailCard, JsonBlock, detailValue, firstValue, hasValue, looksJson } from "./detailPrimitives";

export function reconciliationSummary(row: Record<string, unknown>) {
  const status = row.reconciliation_status;
  const mismatch = row.reconciliation_mismatch_count;
  if (!hasValue(status) && !hasValue(mismatch)) return null;
  return {
    kind: "reconciliation",
    status: hasValue(status) ? String(status) : "-",
    mismatch: Number(mismatch) || 0,
  };
}

export function JobDetailSections({
  row,
  timezoneName,
}: {
  row: Record<string, unknown>;
  timezoneName?: string | null;
}) {
  const operationTypes = formatListLikeValue(row.operation_types) || "-";
  return (
    <>
      <section className="monitoring-detail-section">
        <h3>Job run</h3>
        <div className="monitoring-job-detail-grid">
          <GroupedDetailCard
            title="Identity"
            className="monitoring-job-identity-card"
            rows={[
              ["Workspace ID", row.workspace_id],
              ["Num", firstValue(row, ["job_num", "job_number", "num"])],
              ["Index", row.job_index],
              ["Operation", operationTypes],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
          <GroupedDetailCard
            title="Execution"
            className="monitoring-job-execution-card"
            rows={[
              ["Status", <StatusCell row={row} />],
              ["Start", row.start_time, "start_time"],
              ["End", row.end_time, "end_time"],
              ["Duration", row.duration_seconds, "duration_seconds"],
            ]}
            timezoneName={timezoneName}
          />
          <GroupedDetailCard
            title="Runtime context"
            className="monitoring-job-runtime-card"
            rows={[
              ["Platform", row.platform_name],
              ["Engine", row.engine_name],
              ["Provider", row.metadata_provider_name],
              ["Watermark manager", row.watermark_manager_name],
            ]}
            showEmpty
            timezoneName={timezoneName}
          />
          <GroupedDetailCard
            title="Run config"
            className="monitoring-job-config-card"
            rows={[
              ["Dry run", row.dry_run],
              ["Stop on error", row.stop_on_error],
              ["Max workers", row.max_workers],
              ["Retry count", row.retry_count],
              ["Retry delay", row.retry_delay],
              ["Retention hours", row.retention_hours],
            ]}
            timezoneName={timezoneName}
          />
          {hasValue(row.stages) ? (
            <div className="monitoring-job-group-card monitoring-job-stages">
              <span>Stages</span>
              <JsonBlock value={row.stages} compactArray />
            </div>
          ) : null}
        </div>
      </section>

      <section className="monitoring-detail-section">
        <h3>Job summary</h3>
        <div className="monitoring-job-detail-grid">
          <GroupedDetailCard
            title="Dataflow totals"
            rows={[
              ["Total", row.total_dataflows ?? row.child_dataflow_count, "total_dataflows"],
              ["Succeeded", row.total_succeeded ?? row.child_succeeded_count, "total_succeeded"],
              ["Failed", row.total_failed ?? row.child_failed_count, "total_failed"],
              ["Skipped", row.total_skipped ?? row.child_skipped_count, "total_skipped"],
              ["Running", row.total_running, "total_running"],
              ["Pending", row.total_pending, "total_pending"],
              ["Reconciliation", reconciliationSummary(row)],
              ["Child P95 duration", row.child_p95_duration_seconds, "child_p95_duration_seconds"],
            ]}
            timezoneName={timezoneName}
          />
          <GroupedDetailCard
            title="Row totals"
            rows={[
              ["Read", row.total_rows_read ?? row.child_total_rows_read, "total_rows_read"],
              ["Written", row.total_rows_written ?? row.child_total_rows_written, "total_rows_written"],
              ["Inserted", row.total_rows_inserted ?? row.child_total_rows_inserted, "total_rows_inserted"],
              ["Updated", row.total_rows_updated ?? row.child_total_rows_updated, "total_rows_updated"],
              ["Deleted", row.total_rows_deleted, "total_rows_deleted"],
            ]}
            timezoneName={timezoneName}
          />
          <GroupedDetailCard
            title="File and byte totals"
            rows={[
              ["Files added", row.total_files_added],
              ["Files removed", row.total_files_removed],
              ["Bytes added", row.total_bytes_added, "total_bytes_added"],
              ["Bytes removed", row.total_bytes_removed, "total_bytes_removed"],
            ]}
            timezoneName={timezoneName}
          />
        </div>
      </section>

      <ErrorMessageSection row={row} timezoneName={timezoneName} />
    </>
  );
}

export function formatListLikeValue(value: unknown) {
  if (!hasValue(value)) return "";
  if (Array.isArray(value)) return value.map((item) => String(item ?? "").trim()).filter(Boolean).join(", ");
  const textValue = String(value).trim();
  if (!textValue) return "";
  if (looksJson(textValue)) {
    try {
      const parsed = JSON.parse(textValue);
      if (Array.isArray(parsed)) return parsed.map((item) => String(item ?? "").trim()).filter(Boolean).join(", ");
    } catch {
      return textValue;
    }
  }
  return textValue.split(",").map((item) => item.trim()).filter(Boolean).join(", ");
}

export function ErrorMessageSection({ row, timezoneName }: { row: Record<string, unknown>; timezoneName?: string | null }) {
  if (!hasValue(row.error_message)) return null;
  return (
    <section className="monitoring-detail-section monitoring-error-message-section">
      <h3>Error Message</h3>
      <ErrorMessageBlock value={detailValue(row, "error_message", timezoneName)} />
    </section>
  );
}
