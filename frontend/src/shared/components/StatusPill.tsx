interface StatusPillProps {
  status?: string | null;
}

export function StatusPill({ status }: StatusPillProps) {
  const normalized = (status || "unknown").toLowerCase();
  return <span className={`status-pill status-${normalized}`}>{status || "unknown"}</span>;
}
