/** Shared presentation roles. Consumers retain ownership of metric meaning. */
export interface HelpMetric {
  label: string;
  value: string;
  tone?: "neutral" | "success" | "failed" | "warning" | "info";
  valueColor?: string;
  wrap?: "path" | "multiline";
}

export interface HelpContent {
  title?: string;
  context?: string;
  body?: string;
  metrics?: readonly HelpMetric[];
  note?: string;
}

export function escapeHelpHtml(value: unknown): string {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]!);
}

const HELP_VALUE_COLOR_TOKENS = new Set([
  "var(--color-success)", "var(--color-danger)", "var(--color-warning)",
  "var(--color-warning-text)", "var(--color-info)", "var(--color-text-muted)",
  "var(--resolution-review-color)", "var(--resolution-unresolved-color)",
]);

/** Accept repository colors and a small theme-token allowlist, never arbitrary CSS. */
export function helpMetricValueColor(value: string | undefined): string | undefined {
  return value && (/^#[\da-f]{6}$/iu.test(value) || HELP_VALUE_COLOR_TOKENS.has(value)) ? value : undefined;
}

export function helpContentHtml(content: HelpContent): string {
  const text = (role: string, value: string | undefined) => value === undefined ? "" : `<div class="help-${role}">${escapeHelpHtml(value)}</div>`;
  return `<div class="help-content">${text("title", content.title)}${text("context", content.context)}${text("body", content.body)}${content.metrics?.length ? `<dl class="help-metrics">${content.metrics.map(({label,value,tone = "neutral",valueColor,wrap}) => {
    const color = helpMetricValueColor(valueColor);
    return `<div${wrap ? ` class="help-metric-${wrap}"` : ""}><dt>${escapeHelpHtml(label)}</dt><dd class="help-tone-${tone}"${color ? ` style="color:${color}"` : ""}>${escapeHelpHtml(value)}</dd></div>`;
  }).join("")}</dl>` : ""}${text("note", content.note)}</div>`;
}
