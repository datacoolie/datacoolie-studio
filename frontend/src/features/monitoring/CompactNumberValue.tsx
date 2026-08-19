import type { HTMLAttributes } from "react";
import { presentNumber } from "./monitoringNumberFormat";

export interface CompactNumberValueProps extends Pick<HTMLAttributes<HTMLSpanElement>, "className" | "style"> {
  value: number | null | undefined;
  prefix?: string;
  suffix?: string;
  fallback?: string;
}

export function CompactNumberValue({ value, prefix, suffix, fallback = "-", className, style }: CompactNumberValueProps) {
  const presented = presentNumber(value, { prefix, suffix });
  const display = presented.display === "-" ? fallback : presented.display;
  const exact = presented.exact === "-" ? fallback : presented.exact;
  return (
    <span className={["monitor-compact-value", className].filter(Boolean).join(" ")} style={style} title={exact} aria-label={exact}>
      {display}
    </span>
  );
}
