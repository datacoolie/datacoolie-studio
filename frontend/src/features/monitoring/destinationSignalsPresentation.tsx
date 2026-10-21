import { formatBytes, formatCompact, formatTooltipNumber, metricNumber, metricValue } from "./components/monitoringPresentation";
import { Help } from "../../shared/components/Help";
import type { HelpContent } from "../../shared/components/helpContent";

function compactValue(value: ReturnType<typeof metricValue>, bytes = false) {
  const display = bytes ? formatBytes(value) : formatCompact(value);
  return display === "-" ? "—" : display;
}

export type RegistrySignal = {
  code: string;
  severity: "error" | "review" | "missing" | "info";
  count: ReturnType<typeof metricValue>;
  category: "operation" | "evidence";
  details: string[];
};

export function recordedSignals(row: Record<string, unknown>): RegistrySignal[] {
  if (!Array.isArray(row.destination_signals)) return [];
  return row.destination_signals.filter((value): value is RegistrySignal => Boolean(value && typeof value === "object" && typeof value.code === "string"));
}

export function signalLabel(signal: RegistrySignal) {
  const count = compactValue(signal.count);
  const multiple = metricNumber(signal.count) !== 1;
  switch (signal.code) {
    case "destination_failed": return `${count} destination failure${multiple ? "s" : ""}`;
    case "destination_cancelled": return `${count} destination cancellation${multiple ? "s" : ""}`;
    case "run_failed_destination_unknown": return `${count} failed run${multiple ? "s" : ""} · destination status missing`;
    case "identity_conflict": return "Identity conflict";
    case "layout_conflict": return "Layout conflict";
    case "layout_invalid": return "Invalid layout configuration";
    case "identity_unresolved": return "Identity unresolved";
    case "incomplete_metrics": return "Incomplete metrics";
    case "effect_unconfirmed": return "Physical changes unconfirmed";
    case "repeated_reports": return "Repeated reports retained";
    case "outcome_unknown": return `${count} unknown outcome${multiple ? "s" : ""}`;
    case "running": return `${count} running run${multiple ? "s" : ""}`;
    case "pending": return `${count} pending run${multiple ? "s" : ""}`;
    case "format_changed": return "Format changed";
    default: return signal.code.replace(/_/gu, " ");
  }
}

export function signalTone(signal: RegistrySignal) {
  if (signal.code === "identity_conflict") return "is-conflict";
  if (signal.code === "identity_unresolved") return "is-unresolved";
  if (signal.code === "running") return "is-running";
  if (signal.code === "pending") return "is-pending";
  if (signal.severity === "error") return "is-failed";
  if (signal.severity === "review") return "is-warning";
  return "is-muted";
}

export function signalValueColor(signal: RegistrySignal) {
  switch (signalTone(signal)) {
    case "is-conflict":
    case "is-pending": return "var(--resolution-review-color)";
    case "is-unresolved": return "var(--resolution-unresolved-color)";
    case "is-running": return "var(--color-info)";
    case "is-failed": return "var(--color-danger)";
    case "is-warning": return "var(--color-warning)";
    default: return "var(--color-text-muted)";
  }
}

function destinationSignals(row: Record<string, unknown>) {
  const all = recordedSignals(row);
  const primary = all.find(signal => signal.category === "operation") ?? all[0];
  const secondary = primary?.category === "operation"
    ? all.find(signal => signal.category === "evidence") ?? all.find(signal => signal !== primary)
    : all.find(signal => signal !== primary);
  return { primary, secondary, remaining: Math.max(0, all.length - Number(Boolean(primary)) - Number(Boolean(secondary))), all };
}

export function signalMeasureValue(row: Record<string, unknown>) {
  const { primary, secondary, remaining } = destinationSignals(row);
  return [primary ? signalLabel(primary) : "—", `${secondary ? signalLabel(secondary) : ""}${remaining ? ` · +${remaining}` : ""}`];
}

export function destinationSignalsHelpContent(row: Record<string, unknown>): HelpContent {
  const signals = recordedSignals(row);
  if (!signals.length) {
    return { title: "Signals", body: "No recorded signal within the current filters; object health is not established." };
  }
  const details = signals.flatMap((signal, index) => {
    const lines = Array.isArray(signal.details) ? signal.details.filter((detail): detail is string => typeof detail === "string") : [];
    return lines.length ? [`${index + 1}. ${signalLabel(signal)}: ${lines.join(" · ")}`] : [];
  });
  return {
    title: `Destination signals (${signals.length})`,
    context: "Value color follows severity; counts are affected runs or evidence records.",
    metrics: signals.map(signal => ({
      label: signalLabel(signal),
      value: `${signal.severity.charAt(0).toUpperCase()}${signal.severity.slice(1)} · ${formatTooltipNumber(signal.count)} affected`,
      valueColor: signalValueColor(signal),
      ...(signalLabel(signal).length > 36 ? { wrap: "multiline" as const } : {}),
    })),
    note: details.length ? details.join("\n") : undefined,
  };
}

export function DestinationSignalsCell({ row }: { row: Record<string, unknown> }) {
  const { primary, secondary, remaining } = destinationSignals(row);
  return <Help as="span" className="destination-registry-signals" content={destinationSignalsHelpContent(row)}>
    <strong className={primary ? signalTone(primary) : undefined}>{primary ? signalLabel(primary) : "—"}</strong>
    {secondary ? <small className={`destination-registry-secondary-signal ${signalTone(secondary)}`}><span>{signalLabel(secondary)}</span>{remaining > 0 ? <span className="destination-registry-more-signals"> · +{remaining}</span> : null}</small> : null}
  </Help>;
}

export function DestinationSignalList({ row }: { row: Record<string, unknown> }) {
  const signals = recordedSignals(row);
  if (!signals.length) return <p className="destination-drawer-empty">No recorded signals in the current filters.</p>;
  return <ul className="destination-drawer-signal-list">{signals.map(signal => <li key={signal.code}>
    <strong className={signalTone(signal)}>{signalLabel(signal)}</strong>
    <small title={`Severity: ${signal.severity}; count: ${formatTooltipNumber(signal.count)}`}>{(signal.details ?? []).join(" · ")}</small>
  </li>)}</ul>;
}
