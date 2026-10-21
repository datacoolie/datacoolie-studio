import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  RefreshCw,
  XCircle,
} from "lucide-react";
import type { ReactNode } from "react";

import type { SourceValidationResult } from "../../shared/api/domainTypes";
import type { SourceSyncStatus } from "../../shared/api/contractTypes";
import { formatAbsoluteTime, formatRelativeTime } from "../../shared/time";
import { SourceOperationIcon } from "./SourceOperationFeedback";
import {
  checkLabel,
  checkTooltip,
  syncExecutionTooltip,
  syncLabel,
  validationLabel,
  validationStateFromResult,
  validationTooltip,
  type SourceStatusView,
} from "./sourceStatusModel";

export function LabeledStatus({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="source-labeled-status">
      <span>{label}</span>
      {children}
    </div>
  );
}

export function StatusPill({ view, timezoneName }: { view: SourceStatusView; timezoneName: string | null }) {
  return (
    <span className={`source-status-pill ${view.tone}`} title={view.tooltip || undefined}>
      {view.icon === "running" ? <SourceOperationIcon size={11} /> : null}
      {view.icon === "check" ? <CheckCircle2 size={11} /> : null}
      {view.icon === "warning" ? <AlertTriangle size={11} /> : null}
      {view.icon === "error" ? <XCircle size={11} /> : null}
      {view.icon === "clock" ? <Clock size={11} /> : null}
      {view.icon === "refresh" ? <RefreshCw size={11} /> : null}
      <span>{view.label}</span>
      {view.timestamp ? <StatusTimestamp value={view.timestamp} timezoneName={timezoneName} /> : null}
    </span>
  );
}

export function ValidationBadge({
  status,
  fallback,
  timezoneName,
}: {
  status: SourceSyncStatus["validation"] | null;
  fallback: SourceValidationResult | null;
  timezoneName: string | null;
}) {
  const state = status?.state ?? validationStateFromResult(fallback);
  const timestamp = state === "validating"
    ? null
    : status?.completed_at ?? fallback?.validated_at;
  const view: SourceStatusView = {
    label: validationLabel(state),
    tone: state === "ready" ? "ok" : state === "warning" || state === "validating" ? "warn" : state === "invalid" ? "error" : "muted",
    icon: state === "ready" ? "check" : state === "warning" || state === "validating" ? "warning" : state === "invalid" ? "error" : "clock",
    timestamp,
    tooltip: validationTooltip(status, fallback, timestamp, timezoneName),
  };
  return <StatusPill view={view} timezoneName={timezoneName} />;
}

export function CheckBadge({ status, timezoneName }: { status: SourceSyncStatus | null; timezoneName: string | null }) {
  const observation = status?.observation ?? null;
  const state = observation?.state ?? "never";
  const timestamp = observation?.checked_at;
  const view: SourceStatusView = {
    label: checkLabel(state),
    tone: state === "unchanged" ? "ok" : state === "changed" || state === "checking" || state === "paused" ? "warn" : state === "error" ? "error" : "muted",
    icon: state === "unchanged" ? "check" : state === "changed" || state === "checking" || state === "paused" ? "warning" : state === "error" ? "error" : "clock",
    timestamp: state === "paused" || state === "never" ? null : timestamp,
    tooltip: checkTooltip(observation, timestamp, timezoneName),
  };
  return <StatusPill view={view} timezoneName={timezoneName} />;
}

export function SyncBadge({ status, timezoneName }: { status: SourceSyncStatus | null; timezoneName: string | null }) {
  const execution = status?.sync_execution ?? null;
  const state = execution?.state ?? "never";
  const timestamp = state === "running"
    ? execution?.started_at
    : execution?.completed_at;
  const view: SourceStatusView = {
    label: syncLabel(state),
    tone: state === "succeeded" ? "ok" : state === "running" ? "warn" : state === "failed" ? "error" : "muted",
    icon: state === "succeeded" ? "check" : state === "running" ? "running" : state === "failed" ? "error" : "clock",
    timestamp,
    tooltip: syncExecutionTooltip(execution, timestamp, timezoneName),
  };
  return <StatusPill view={view} timezoneName={timezoneName} />;
}

function StatusTimestamp({ value, timezoneName }: { value: string; timezoneName: string | null }) {
  return (
    <em title={formatAbsoluteTime(value, timezoneName) ?? undefined}>
      {formatRelativeTime(value) ?? value}
    </em>
  );
}
