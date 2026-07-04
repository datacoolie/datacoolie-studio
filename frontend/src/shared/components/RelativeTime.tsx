import { useEffect, useState } from "react";

import { formatAbsoluteTime, formatRelativeTime } from "../time";

interface RelativeTimeProps {
  value?: string | null;
  now?: number;
  fallback?: string;
  titlePrefix?: string;
  className?: string;
}

export function RelativeTime({
  value,
  now,
  fallback = "-",
  titlePrefix,
  className
}: RelativeTimeProps) {
  const [clock, setClock] = useState(() => Date.now());

  useEffect(() => {
    if (now !== undefined) return;
    const intervalId = window.setInterval(() => setClock(Date.now()), 60_000);
    return () => window.clearInterval(intervalId);
  }, [now]);

  const relative = formatRelativeTime(value, now ?? clock);
  const absolute = formatAbsoluteTime(value);
  const title = absolute ? `${titlePrefix ? `${titlePrefix}: ` : ""}${absolute}` : undefined;

  return (
    <span className={className} title={title}>
      {relative ?? fallback}
    </span>
  );
}
