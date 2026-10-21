import type { HelpContent, HelpMetric } from "../../../shared/components/helpContent";
import { helpContentHtml } from "../../../shared/components/helpContent";
import { formatTooltipNumber, toApproximateNumber, type MetricValue } from "../../../shared/lib/numberFormat";
import { rowObservation } from "./monitoringObservation";

/** Shared structured content for ECharts hover formatting and lifecycle ownership. */
export type ChartTooltipParams = {
  dataIndex?: number;
  seriesIndex?: number;
  seriesName?: string;
  name?: string;
  value?: unknown;
  data?: unknown;
  marker?: unknown;
  axisValue?: unknown;
  axisValueLabel?: string;
};

export type ChartTooltipContentBuilder = (params: ChartTooltipParams | ChartTooltipParams[]) => HelpContent;

export type ChartTooltipFormatter = ((params: ChartTooltipParams | ChartTooltipParams[]) => string) & {
  content?: ChartTooltipContentBuilder;
};

export function chartTooltip(content: ChartTooltipContentBuilder): ChartTooltipFormatter {
  const formatter = ((params: ChartTooltipParams | ChartTooltipParams[]) => {
    const value = content(params);
    const accessibleText = [
      value.title,
      value.context,
      value.body,
      ...(value.metrics ?? []).map(({ label, value: metricValue }) => `${label}: ${metricValue}`),
      value.note,
    ].filter((part): part is string => Boolean(part && part.length)).join("\n");
    const html = helpContentHtml(value);
    return html.replace(
      '<div class="help-content">',
      `<div class="help-content" aria-label="${escapeAttribute(accessibleText)}">`,
    );
  }) as ChartTooltipFormatter;
  formatter.content = content;
  return formatter;
}

function escapeAttribute(value: string): string {
  return value.replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]!);
}

export function chartContentHtml(content: HelpContent): string {
  return helpContentHtml(content);
}

export function chartText(value: unknown, fallback = "-"): string {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}

export function chartMetric(label: string, value: unknown, tone?: HelpMetric["tone"]): HelpMetric {
  return { label, value: chartText(value), ...(tone ? { tone } : {}) };
}

export function chartObservationMetric(label: string, row: Record<string, unknown>, field: string, valueColor?: string): HelpMetric {
  const observation = rowObservation(row, field);
  const coverage = observation.coverage?.replace(/ runs measured$/u, " runs");
  return {
    ...chartMetric(label, `${formatTooltipNumber(observation.value)}${coverage ? `; ${coverage}` : ""}`),
    valueColor: observation.value != null ? valueColor : undefined,
  };
}

/**
 * Source durations use grouped, rounded seconds and a compact duration.
 * Missing measurements remain unavailable; large integer seconds stay exact.
 */
export function chartDurationMetric(value: unknown, { compactOnly = false }: { compactOnly?: boolean } = {}): string {
  const metric = value as MetricValue;
  const exact = formatTooltipNumber(metric);
  if (exact === "-") return "Unavailable";
  const approximate = toApproximateNumber(metric);
  if (compactOnly) {
    return approximate === null
      ? `${exact}s`
      : `${formatTooltipNumber(approximate < 60 ? metric : approximate / 60)}${approximate < 60 ? "s" : "m"}`;
  }
  return approximate === null
    ? `${exact} seconds`
    : `${formatTooltipNumber(approximate < 60 ? approximate : approximate / 60)}${approximate < 60 ? "s" : "m"} (${exact} seconds)`;
}

/** ECharts calls root formatters with an array for axis triggers. */
export function firstChartParam(params: ChartTooltipParams | ChartTooltipParams[]): ChartTooltipParams {
  return Array.isArray(params) ? params[0] ?? {} : params;
}

export function chartDataIndex(params: ChartTooltipParams | ChartTooltipParams[], fallback = 0): number {
  const value = Number(firstChartParam(params).dataIndex);
  return Number.isInteger(value) && value >= 0 ? value : fallback;
}

export function chartTooltipContent(formatter: unknown): ChartTooltipContentBuilder | undefined {
  if (typeof formatter !== "function") return undefined;
  return (formatter as ChartTooltipFormatter).content;
}
