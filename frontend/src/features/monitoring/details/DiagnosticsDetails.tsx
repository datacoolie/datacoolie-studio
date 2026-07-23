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
import { hasValue, looksJson, parseJsonLike, renderGroupedValue } from "./detailPrimitives";

export function DiagnosticsDetailSections({
  row,
  timezoneName,
  onOpenJob,
}: {
  row: Record<string, unknown>;
  timezoneName?: string | null;
  onOpenJob?: (row: Record<string, unknown>) => void;
}) {
  const evidence = evidenceObject(row.evidence);
  const category = String(row.category ?? "diagnostics");
  const actionItems = diagnosticsInvestigationActions(row, evidence);
  const evidenceItems = diagnosticsEvidenceItems(category, row, evidence);
  const linkedJob = diagnosticsLinkedJobRow(row, evidence);
  return (
    <>
      <section className="monitoring-detail-section monitoring-diagnostics-finding">
        <h3>What happened</h3>
        <div className="monitoring-diagnostics-finding-callout">
          <strong>{display(row, "issue")}</strong>
          <p>{diagnosticsRuleDescription(category)}</p>
          {hasValue(row.latest_time) ? (
            <small>Latest observed · {renderGroupedValue(row.latest_time, timezoneName, "latest_time")}</small>
          ) : null}
        </div>
      </section>

      <section className="monitoring-detail-section">
        <h3>Evidence</h3>
        <div className="monitoring-diagnostics-evidence-grid">
          {evidenceItems.map((item) => (
            <div
              key={item.label}
              className={`monitoring-diagnostics-evidence-card${item.intent && item.intent !== "neutral" ? ` is-${item.intent}` : ""}${item.wide ? " is-wide" : ""}${item.primary ? " is-primary" : ""}`}
            >
              <span>{item.label}</span>
              <strong>{renderGroupedValue(item.value, timezoneName, item.field)}</strong>
            </div>
          ))}
        </div>
      </section>

      {linkedJob ? <DiagnosticsLinkedJobSection row={linkedJob} onOpenJob={onOpenJob} /> : null}

      {actionItems.length ? (
        <section className="monitoring-detail-section">
          <h3>Investigation path</h3>
          <ol className="monitoring-diagnostics-action-list">
            {actionItems.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ol>
        </section>
      ) : null}
    </>
  );
}

export function evidenceObject(value: unknown): Record<string, unknown> {
  if (value && typeof value === "object" && !Array.isArray(value)) return value as Record<string, unknown>;
  if (typeof value === "string" && looksJson(value)) {
    const parsed = parseJsonLike(value);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed as Record<string, unknown>;
  }
  return {};
}

export function DiagnosticsLinkedJobSection({
  row,
  onOpenJob,
}: {
  row: Record<string, unknown>;
  onOpenJob?: (row: Record<string, unknown>) => void;
}) {
  const jobId = String(row.job_id ?? "");
  if (!jobId) return null;
  return (
    <section className="monitoring-detail-section monitoring-linked-job-section">
      <h3>Linked job</h3>
      <button
        className="monitoring-linked-job-row is-diagnostics-link"
        type="button"
        onClick={() => onOpenJob?.(row)}
        disabled={!onOpenJob}
      >
        <BriefcaseBusiness size={15} aria-hidden="true" />
        <span className="monitoring-linked-job-identity">
          <strong>{jobId}</strong>
          <small>Open canonical job run</small>
        </span>
        <ChevronRight size={16} aria-hidden="true" />
      </button>
    </section>
  );
}
