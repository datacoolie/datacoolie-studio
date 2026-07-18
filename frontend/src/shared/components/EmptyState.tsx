import type { ReactNode } from "react";

interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  detail?: string;
  action?: ReactNode;
}

export function EmptyState({ icon, title, detail, action }: EmptyStateProps) {
  return (
    <div className="empty-state">
      {icon ? <div className="empty-icon">{icon}</div> : null}
      <div className="empty-title">{title}</div>
      {detail ? <div className="empty-subtitle">{detail}</div> : null}
      {action ? <div>{action}</div> : null}
    </div>
  );
}
