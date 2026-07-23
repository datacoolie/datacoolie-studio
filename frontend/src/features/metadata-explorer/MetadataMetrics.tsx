import { AlertTriangle, Database, GitBranch, Lightbulb, PlayCircle } from "lucide-react";
import type { ReactNode } from "react";
import type { MetadataEditorIssue } from "../../shared/api/domainTypes";

interface MetadataMetricsProps {
  connections: number;
  dataflows: number;
  enabledDataflows: number;
  schemaHints: number;
  issues: MetadataEditorIssue[];
  onIssuesClick: () => void;
}

export function MetadataMetrics({ connections, dataflows, enabledDataflows, schemaHints, issues, onIssuesClick }: MetadataMetricsProps) {
  const hasError = issues.some((issue) => issue.severity === "error");
  return (
    <div className="metadata-kpi-strip">
      <MetaKpiTile icon={<Database size={16} />} label="Connections" value={connections} />
      <MetaKpiTile icon={<GitBranch size={16} />} label="Dataflows" value={dataflows} />
      <MetaKpiTile icon={<PlayCircle size={16} />} label="Enabled" value={enabledDataflows} />
      <MetaKpiTile icon={<Lightbulb size={16} />} label="Schema hints" value={schemaHints} />
      <MetaKpiTile
        icon={<AlertTriangle size={16} />}
        label="Issues"
        value={issues.length}
        intent={hasError ? "bad" : "neutral"}
        onClick={issues.length ? onIssuesClick : undefined}
      />
    </div>
  );
}

function MetaKpiTile({ icon, label, value, intent = "neutral", onClick }: {
  icon: ReactNode;
  label: string;
  value: number;
  intent?: "neutral" | "bad";
  onClick?: () => void;
}) {
  const cls = `metadata-kpi-tile metadata-kpi-${intent}${onClick ? " metadata-kpi-button" : ""}`;
  const content = (
    <span className="metadata-kpi-line">
      <span className="metadata-kpi-icon">{icon}</span>
      <strong className="metadata-kpi-value">{value}</strong>
      <span className="metadata-kpi-label">{label}</span>
    </span>
  );
  if (onClick) return <button className={cls} type="button" onClick={onClick}>{content}</button>;
  return <div className={cls}>{content}</div>;
}
