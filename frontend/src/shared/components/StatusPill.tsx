interface StatusPillProps {
  status?: string | null;
}

export function StatusPill({ status }: StatusPillProps) {
  const normalized = (status || "unknown").toLowerCase();
  const presentation = lifecycleStatusPresentation(normalized);
  return (
    <span
      className={`status-pill status-${normalized}`}
      style={presentation ? { color: presentation.textColor, backgroundColor: presentation.pillBackground } : undefined}
    >
      {status || "unknown"}
    </span>
  );
}
import { lifecycleStatusPresentation } from "../statusPresentation";
