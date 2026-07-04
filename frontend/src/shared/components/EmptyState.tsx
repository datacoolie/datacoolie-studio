import type { ReactNode } from "react";

interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  action?: ReactNode;
}

export function EmptyState({ icon, title, action }: EmptyStateProps) {
  return (
    <div className="empty-state">
      {icon ? <div className="empty-icon">{icon}</div> : null}
      <div className="empty-title">{title}</div>
      {action ? <div>{action}</div> : null}
    </div>
  );
}
