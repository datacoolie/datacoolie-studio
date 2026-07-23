import { Activity, AlertTriangle, CheckCircle2, Database, GitBranch, SlidersHorizontal } from "lucide-react";
import type { EnvironmentOverview, ResolutionState } from "../../shared/api/domainTypes";
import { EmptyState } from "../../shared/components/EmptyState";
import { RelativeTime } from "../../shared/components/RelativeTime";
import { elapsedWholeDays } from "../../shared/time";
import type { ModuleKey } from "../../app/moduleRegistry";

interface OverviewPageProps {
  overview: EnvironmentOverview | null;
  onNavigate: (module: ModuleKey, search?: string) => void;
}

export function OverviewPage({ overview, onNavigate }: OverviewPageProps) {
  const sources = overview?.sources;
  const metadata = overview?.metadata;
  const lineage = overview?.lineage;
  const monitoring = overview?.monitoring;
  const enabledMetadataSources = sources?.metadata.enabled ?? 0;
  const metadataSourceCount = sources?.metadata.configured ?? 0;
  const enabledLogPaths = sources?.logs.enabled ?? 0;
  const logPathCount = sources?.logs.configured ?? 0;
  const failedJobs = monitoring?.total_failures ?? 0;
  const metadataErrors = metadata?.errors.length ?? 0;
  const lineageErrors = lineage?.error_count ?? 0;
  const monitoringErrors = monitoring?.errors.length ?? 0;
  const dataflowSuccessRate = monitoring?.dataflow_success_rate ?? 0;
  const readErrors = metadataErrors + lineageErrors + monitoringErrors;
  const now = Date.now();
  const failedWindows = monitoring?.failed_job_windows ?? { last7: 0, last30: 0, last365: 0 };
  const logFreshness = getLogFreshness(monitoring?.latest_log_at ?? null, now);
  const sourceValidation = sources?.validation ?? { errors: 0, warnings: 0 };
  const sourceValidationIntent: StatIntent = !metadataSourceCount && !logPathCount
    ? "neutral"
    : sourceValidation.errors > 0
      ? "bad"
      : sourceValidation.warnings > 0
        ? "warning"
        : "good";
  const nextActions = getNextActions({
    enabledMetadataSources,
    enabledLogPaths,
    metadataDataflows: metadata?.dataflows ?? 0,
    lineageDataflows: lineage?.dataflows ?? 0,
    metadataErrors,
    lineageErrors,
    monitoringErrors,
    failedJobs,
    failedWindows,
    logFreshness
  });
  const primaryAction = nextActions[0];
  const secondaryActions = nextActions.slice(1);
  const status = getEnvironmentStatus({
    enabledMetadataSources,
    enabledLogPaths,
    dataflows: metadata?.dataflows ?? 0,
    jobs: monitoring?.job_records ?? 0,
    readErrors,
    failedWindows,
    logFreshness,
    primaryAction
  });
  const stageSummary = metadata?.stages ?? [];
  const loadTypeSummary = metadata?.load_types ?? [];
  const lineageCoverage = metadata?.dataflows ? Math.round(((lineage?.dataflows ?? 0) / metadata.dataflows) * 100) : 0;
  const referenceMappingCoverage = lineage?.references
    ? Math.round(
      ((lineage.automatic_references + lineage.manual_references) / lineage.references) * 100
    )
    : 0;

  if (!metadataSourceCount && !logPathCount) {
    return (
      <EmptyState
        icon={<SlidersHorizontal size={24} />}
        title="Add sources to start"
        action={
          <button className="icon-action" onClick={() => onNavigate("sources")}>
            <SlidersHorizontal size={16} />
            <span>Open Sources</span>
          </button>
        }
      />
    );
  }

  return (
    <div className="view-stack">

      {/* ── Module snapshot cards ──────────────────────────────────── */}
      <div className="env-module-grid">
        <ModuleSnapshot
          icon={<SlidersHorizontal size={18} />}
          title="Sources"
          moduleStatus={enabledMetadataSources + enabledLogPaths > 0 ? "ready" : "empty"}
          metrics={[
            { label: "metadata", value: metadataSourceCount },
            { label: "log paths", value: logPathCount },
            { label: "enabled", value: enabledMetadataSources + enabledLogPaths }
          ]}
          onClick={() => onNavigate("sources")}
        />
        <ModuleSnapshot
          icon={<Database size={18} />}
          title="Metadata"
          moduleStatus={(metadata?.dataflows ?? 0) > 0 ? (metadataErrors > 0 ? "warning" : "ready") : "empty"}
          metrics={[
            { label: "dataflows", value: metadata?.dataflows ?? 0 },
            { label: "connections", value: metadata?.connections ?? 0 },
            { label: "errors", value: metadataErrors }
          ]}
          onClick={() => onNavigate("metadata")}
        />
        <ModuleSnapshot
          icon={<GitBranch size={18} />}
          title="Lineage"
          moduleStatus={(lineage?.assets ?? 0) > 0 ? (lineageErrors > 0 ? "warning" : "ready") : "empty"}
          metrics={[
            { label: "assets", value: lineage?.assets ?? 0 },
            { label: "dataflows", value: lineage?.dataflows ?? 0 },
            { label: "coverage", value: `${lineageCoverage}%` }
          ]}
          onClick={() => onNavigate("lineage")}
        />
        <ModuleSnapshot
          icon={<Activity size={18} />}
          title="Monitoring"
          moduleStatus={(monitoring?.job_records ?? 0) > 0 ? (failedWindows.last7 > 0 ? "warning" : "ready") : "empty"}
          metrics={[
            { label: "jobs", value: monitoring?.job_records ?? 0 },
            { label: "success rate", value: `${dataflowSuccessRate}%` },
            { label: "failed 7d", value: failedWindows.last7 }
          ]}
          onClick={() => onNavigate("monitoring")}
        />
      </div>

      {/* ── Detail panels ─────────────────────────────────────────── */}
      <div className="overview-panel-grid">
        <OverviewPanel title="Setup and readiness">
          <RatioStat label="Metadata configured" enabled={enabledMetadataSources} total={metadataSourceCount} suffix="enabled" />
          <RatioStat label="ETL logs configured" enabled={enabledLogPaths} total={logPathCount} suffix="enabled" />
          <CompactStat
            label="Source validation"
            value={`${sourceValidation.errors} errors, ${sourceValidation.warnings} warnings`}
            intent={sourceValidationIntent}
          />
          <CompactStat label="Read errors (all modules)" value={readErrors} intent={readErrors > 0 ? "warning" : "good"} />
        </OverviewPanel>

        <OverviewPanel title="Attention and next actions">
          <div className={`overview-attention-summary intent-${status.intent}`}>
            <p className="overview-attention-summary-line">
              <span className="overview-attention-summary-icon">
                {status.intent === "bad" || status.intent === "warning"
                  ? <AlertTriangle size={14} />
                  : <CheckCircle2 size={14} />}
              </span>
              <span>{status.label} {status.reason}</span>
            </p>
          </div>
          <button className="summary-action-row overview-action-row primary" onClick={() => onNavigate(status.actionModule)}>
            <span>{status.actionLabel}</span>
            <strong>{status.label}</strong>
          </button>
          {secondaryActions.length ? secondaryActions.map((action) => (
            <button key={action.label} className="summary-action-row overview-action-row" onClick={() => onNavigate(action.module)}>
              <span>{action.label}</span>
              <strong>{action.detail}</strong>
            </button>
          )) : (
            <div className="overview-attention-empty">No immediate attention items.</div>
          )}
        </OverviewPanel>

        <OverviewPanel title="Data estate">
          <RatioStat label="Connections" enabled={metadata?.enabled_connections ?? 0} total={metadata?.connections ?? 0} suffix="enabled" />
          <RatioStat label="Dataflows" enabled={metadata?.enabled_dataflows ?? 0} total={metadata?.dataflows ?? 0} suffix="enabled" />
          <RatioStat label="Schema hints" enabled={metadata?.enabled_schema_hints ?? 0} total={metadata?.schema_hints ?? 0} suffix="enabled" />
          <CompactStat label="Lineage coverage" value={`${lineageCoverage}%`} />
          <CompactStat label="Mapping coverage" value={`${referenceMappingCoverage}%`} />
          <ResolutionStat
            automatic={lineage?.automatic_references ?? 0}
            manual={lineage?.manual_references ?? 0}
            unresolved={lineage?.unresolved_references ?? 0}
          />
          <PillSummary label="Stages" items={stageSummary} onItemClick={(value) => openDataflowMetadataFilter(onNavigate, value)} />
          <PillSummary label="Load types" items={loadTypeSummary} onItemClick={(value) => openDataflowMetadataFilter(onNavigate, value)} />
        </OverviewPanel>

        <OverviewPanel title="Operations">
          <CompactStat label="Failed last 7 days" value={failedWindows.last7} intent={failedWindows.last7 ? "bad" : "good"} />
          <CompactStat label="Failed last 30 days" value={failedWindows.last30} intent={failedWindows.last30 ? "warning" : "good"} />
          <CompactStat label="Failed last 1 year" value={failedWindows.last365} intent={failedWindows.last365 ? "warning" : "good"} />
          <RatioStat label="Failed jobs" enabled={failedJobs} total={monitoring?.job_records ?? 0} suffix="failed" reverse />
          <CompactStat label="Success rate" value={`${dataflowSuccessRate}%`} />
          <CompactStat label="Active engines" value={monitoring?.active_engines ?? 0} />
          <CompactStat
            label="Latest log"
            value={<RelativeTime value={logFreshness.latestAt} titlePrefix="Latest log" />}
            intent={logFreshness.intent}
          />
          <CompactStat label="Date range" value={formatDateRange(monitoring?.date_range)} />
        </OverviewPanel>
      </div>

      {metadata?.errors.length || monitoring?.errors.length ? (
        <div className="error-list">
          {metadata?.errors.map((error, index) => <code key={`metadata-${index}`}>{JSON.stringify(error)}</code>)}
          {monitoring?.errors.map((error, index) => <code key={`monitoring-${index}`}>{JSON.stringify(error)}</code>)}
        </div>
      ) : null}
    </div>
  );
}

function ModuleSnapshot({
  icon,
  title,
  moduleStatus,
  metrics,
  onClick
}: {
  icon: React.ReactNode;
  title: string;
  moduleStatus: "ready" | "warning" | "empty";
  metrics: Array<{ label: string; value: string | number }>;
  onClick: () => void;
}) {
  return (
    <button className={`env-module-card module-status-${moduleStatus}`} onClick={onClick}>
      <div className="env-module-card-header">
        <span className="env-module-card-icon">{icon}</span>
        <strong>{title}</strong>
        <span className={`env-module-dot dot-${moduleStatus}`} />
      </div>
      <div className="env-module-metrics">
        {metrics.map((m) => (
          <div key={m.label} className="env-module-metric">
            <strong>{m.value}</strong>
            <span>{m.label}</span>
          </div>
        ))}
      </div>
    </button>
  );
}

function OverviewPanel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="overview-panel">
      <h3>{title}</h3>
      <div className="panel-body overview-panel-body">{children}</div>
    </section>
  );
}

function CompactStat({ label, value, intent = "neutral" }: { label: string; value: React.ReactNode; intent?: StatIntent }) {
  return (
    <div className={`summary-row compact-stat stat-${intent}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ResolutionStat({ automatic, manual, unresolved }: Record<ResolutionState, number>) {
  const values: Array<[ResolutionState, number]> = [
    ["automatic", automatic],
    ["manual", manual],
    ["unresolved", unresolved],
  ];
  return (
    <div className="summary-row compact-stat resolution-stat">
      <span>Reference resolution</span>
      <strong className="resolution-stat-values">
        {values.map(([state, value]) => (
          <span className={`assets-status-chip status-${state}`} key={state}>{value} {state}</span>
        ))}
      </strong>
    </div>
  );
}

function RatioStat({
  label,
  enabled,
  total,
  suffix,
  reverse = false
}: {
  label: string;
  enabled: number;
  total: number;
  suffix: string;
  reverse?: boolean;
}) {
  return <CompactStat label={label} value={`${enabled}/${total} ${suffix}`} intent={ratioIntent(enabled, total, reverse)} />;
}

function PillSummary({
  label,
  items,
  onItemClick
}: {
  label: string;
  items: Array<{ name: string; count: number }>;
  onItemClick?: (value: string) => void;
}) {
  return (
    <div className="summary-pill-list pill-summary">
      <span>{label}</span>
      <div>
        {items.length ? items.map((item) => (
          <button
            key={item.name}
            className="summary-pill-button"
            type="button"
            onClick={() => onItemClick?.(item.name)}
            title={`Filter metadata dataflows by ${label.toLowerCase()}: ${item.name}`}
          >
            <span>{item.name}</span>
            <b>{item.count}</b>
          </button>
        )) : (
          <strong>
            <span>-</span>
          </strong>
        )}
      </div>
    </div>
  );
}

function openDataflowMetadataFilter(onNavigate: (module: ModuleKey, search?: string) => void, value: string) {
  const params = new URLSearchParams({ sheet: "dataflows", q: value });
  onNavigate("metadata", `?${params.toString()}`);
}

function formatDateRange(dateRange: { min?: string | null; max?: string | null } | undefined) {
  if (!dateRange?.min && !dateRange?.max) return "-";
  if (dateRange.min === dateRange.max) return dateRange.min ?? "-";
  return `${dateRange.min ?? "-"} to ${dateRange.max ?? "-"}`;
}

type StatIntent = "neutral" | "good" | "warning" | "bad";
type LogFreshness = {
  latestAt: string | null;
  ageDays: number | null;
  intent: StatIntent;
};

function ratioIntent(value: number, total: number, reverse = false): StatIntent {
  if (!total) return "neutral";
  if (reverse) {
    if (value === 0) return "good";
    if (value < total) return "warning";
    return "bad";
  }
  if (value === total) return "good";
  if (value > 0) return "warning";
  return "bad";
}

function getLogFreshness(latestAt: string | null, now: number): LogFreshness {
  const ageDays = elapsedWholeDays(latestAt, now);
  if (ageDays === null) return { latestAt, ageDays: null, intent: "neutral" };
  return {
    latestAt,
    ageDays,
    intent: ageDays > 7 ? "warning" : "good"
  };
}

function getEnvironmentStatus(input: {
  enabledMetadataSources: number;
  enabledLogPaths: number;
  dataflows: number;
  jobs: number;
  readErrors: number;
  failedWindows: { last7: number; last30: number; last365: number };
  logFreshness: LogFreshness;
  primaryAction?: { label: string; detail: string; module: ModuleKey };
}): { label: string; reason: string; intent: "neutral" | "good" | "warning" | "bad"; actionLabel: string; actionModule: ModuleKey } {
  if (!input.enabledMetadataSources && !input.enabledLogPaths) {
    return {
      label: "Needs sources",
      reason: "Add metadata and ETL log paths before this environment can show useful context.",
      intent: "neutral",
      actionLabel: "Open Sources",
      actionModule: "sources"
    };
  }
  if (!input.enabledMetadataSources) {
    return {
      label: "Missing metadata",
      reason: `${input.jobs} jobs are available, but no enabled metadata source is configured.`,
      intent: "neutral",
      actionLabel: "Add metadata source",
      actionModule: "sources"
    };
  }
  if (input.failedWindows.last7 > 0) {
    return {
      label: "Has issues",
      reason: `${input.failedWindows.last7} failed jobs in the last 7 days.`,
      intent: "bad",
      actionLabel: input.primaryAction?.label ?? "Review actions",
      actionModule: input.primaryAction?.module ?? "monitoring"
    };
  }
  if (input.failedWindows.last30 > 0) {
    return {
      label: "Warning",
      reason: `${input.failedWindows.last30} failed jobs in the last 30 days, but none in the last 7 days.`,
      intent: "warning",
      actionLabel: input.primaryAction?.label ?? "Open Monitoring",
      actionModule: input.primaryAction?.module ?? "monitoring"
    };
  }
  if (input.readErrors > 0) {
    return {
      label: "Warning",
      reason: `${input.readErrors} read errors need review across metadata, lineage, or monitoring.`,
      intent: "warning",
      actionLabel: input.primaryAction?.label ?? "Review actions",
      actionModule: input.primaryAction?.module ?? "metadata"
    };
  }
  if (input.logFreshness.ageDays !== null && input.logFreshness.ageDays > 30) {
    return {
      label: "Stale logs",
      reason: `Latest ETL log is ${input.logFreshness.ageDays} days old. Runtime health may be outdated.`,
      intent: "warning",
      actionLabel: input.primaryAction?.label ?? "Open Monitoring",
      actionModule: input.primaryAction?.module ?? "monitoring"
    };
  }
  if (input.logFreshness.ageDays !== null && input.logFreshness.ageDays > 7) {
    return {
      label: "Warning",
      reason: `Latest ETL log is ${input.logFreshness.ageDays} days old. Check whether logging is still running.`,
      intent: "warning",
      actionLabel: input.primaryAction?.label ?? "Open Monitoring",
      actionModule: input.primaryAction?.module ?? "monitoring"
    };
  }
  if (!input.enabledLogPaths) {
    return {
      label: "Metadata only",
      reason: `${input.dataflows} dataflows loaded. Add ETL logs to include runtime health.`,
      intent: "neutral",
      actionLabel: "Add log path",
      actionModule: "sources"
    };
  }
  return {
    label: "Ready to inspect",
    reason: `${input.dataflows} dataflows loaded with runtime evidence from ${input.jobs} jobs.`,
    intent: "good",
    actionLabel: "Open Lineage",
    actionModule: "lineage"
  };
}

function getNextActions(input: {
  enabledMetadataSources: number;
  enabledLogPaths: number;
  metadataDataflows: number;
  lineageDataflows: number;
  metadataErrors: number;
  lineageErrors: number;
  monitoringErrors: number;
  failedJobs: number;
  failedWindows: { last7: number; last30: number; last365: number };
  logFreshness: LogFreshness;
}): Array<{ label: string; detail: string; module: ModuleKey }> {
  const actions: Array<{ label: string; detail: string; module: ModuleKey }> = [];
  if (!input.enabledMetadataSources) {
    actions.push({ label: "Add metadata source", detail: "Required for metadata and lineage", module: "sources" });
  }
  if (!input.enabledLogPaths) {
    actions.push({ label: "Add ETL log path", detail: "Required for monitoring", module: "sources" });
  }
  if (input.metadataErrors) {
    actions.push({ label: "Review metadata errors", detail: `${input.metadataErrors} read errors`, module: "metadata" });
  }
  if (input.metadataDataflows > 0 && !input.lineageDataflows) {
    actions.push({ label: "Inspect lineage inputs", detail: "Metadata loaded but no dataflows", module: "metadata" });
  }
  if (input.lineageErrors) {
    actions.push({ label: "Review lineage errors", detail: `${input.lineageErrors} lineage errors`, module: "lineage" });
  }
  if (input.failedWindows.last7) {
    actions.push({ label: "Open monitoring failures", detail: `${input.failedWindows.last7} failed in last 7 days`, module: "monitoring" });
  } else if (input.failedWindows.last30) {
    actions.push({ label: "Review recent failures", detail: `${input.failedWindows.last30} failed in last 30 days`, module: "monitoring" });
  } else if (input.failedWindows.last365) {
    actions.push({ label: "Review historical failures", detail: `${input.failedWindows.last365} failed in last 1 year`, module: "monitoring" });
  } else if (input.failedJobs) {
    actions.push({ label: "Review historical failures", detail: `${input.failedJobs} failed jobs total`, module: "monitoring" });
  }
  if (input.logFreshness.ageDays !== null && input.logFreshness.ageDays > 30) {
    actions.push({ label: "Review stale logs", detail: `Latest log ${input.logFreshness.ageDays} days old`, module: "monitoring" });
  } else if (input.logFreshness.ageDays !== null && input.logFreshness.ageDays > 7) {
    actions.push({ label: "Check log freshness", detail: `Latest log ${input.logFreshness.ageDays} days old`, module: "monitoring" });
  }
  if (input.monitoringErrors) {
    actions.push({ label: "Review log read errors", detail: `${input.monitoringErrors} monitoring errors`, module: "monitoring" });
  }
  return actions.slice(0, 5);
}
