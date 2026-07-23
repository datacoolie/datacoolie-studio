import { X } from "lucide-react";
import type { MetadataEditorIssue } from "../../shared/api/domainTypes";
import { useDrawerEscape } from "../../shared/hooks/useDrawerEscape";

interface MetadataIssuesDrawerProps {
  issues: MetadataEditorIssue[];
  onClose: () => void;
  onIssueClick: (issue: MetadataEditorIssue) => void;
}

export function MetadataIssuesDrawer({ issues, onClose, onIssueClick }: MetadataIssuesDrawerProps) {
  const errorCount = issues.filter((issue) => issue.severity === "error").length;
  const warningCount = issues.filter((issue) => issue.severity === "warning").length;

  useDrawerEscape(onClose);

  return (
    <div className="metadata-drawer-backdrop" onMouseDown={onClose}>
      <aside className="metadata-drawer metadata-issues-drawer" aria-label="Metadata issues" onMouseDown={(event) => event.stopPropagation()}>
        <header className="metadata-drawer-header">
          <div>
            <span className="eyebrow">Metadata validation</span>
            <h2>Issues</h2>
            <small>
              {errorCount} errors, {warningCount} warnings
            </small>
          </div>
          <button className="icon-action small" type="button" onClick={onClose} title="Close">
            <X size={16} />
          </button>
        </header>
        <div className="metadata-issue-list drawer-list">
          {issues.map((issue, index) => (
            <button
              key={`${issue.sheet}-${issue.row_index}-${issue.column}-${index}`}
              className={`metadata-issue severity-${issue.severity}`}
              type="button"
              onClick={() => onIssueClick(issue)}
            >
              <strong>{issue.severity}</strong>
              <span>
                {issue.sheet} row {issue.row_index + 1}, {issue.column}
              </span>
              <p>{issue.message}</p>
            </button>
          ))}
          {!issues.length ? <div className="table-empty">No issues</div> : null}
        </div>
      </aside>
    </div>
  );
}
