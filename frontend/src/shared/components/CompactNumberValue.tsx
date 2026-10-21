import type { HTMLAttributes } from "react";
import { Help, useHelpEnabled } from "./Help";
import { formatTooltipNumber, presentNumber, type CompactNumberStyle, type MetricValue } from "../lib/numberFormat";

export interface CompactNumberValueProps extends Pick<HTMLAttributes<HTMLSpanElement>, "className" | "style"> {
  value: MetricValue;
  compactStyle?: CompactNumberStyle;
  prefix?: string;
  suffix?: string;
  fallback?: string;
}

export function CompactNumberValue({ value, prefix, suffix, compactStyle, fallback = "-", className, style }: CompactNumberValueProps) {
  const presented = presentNumber(value, { prefix, suffix, compactStyle });
  const display = presented.display === "-" ? fallback : presented.display;
  const exact = presented.exact === "-" ? fallback : presented.exact;
  const helpEnabled = useHelpEnabled();
  if (helpEnabled) {
    const tooltipValue = formatTooltipNumber(value);
    const tooltip = tooltipValue === "-" ? fallback : `${prefix ?? ""}${tooltipValue}${suffix ?? ""}`;
    return <Help className={["monitor-compact-value", className].filter(Boolean).join(" ")} style={style} title={tooltip} aria-label={tooltip}>{display}</Help>;
  }
  return (
    <span className={["monitor-compact-value", className].filter(Boolean).join(" ")} style={style} title={exact} aria-label={exact}>
      {display}
    </span>
  );
}
