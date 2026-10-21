import { BriefcaseBusiness, ChevronRight } from "lucide-react";
import { display } from "../../../shared/components/DataTable";
import {
  diagnosticsEvidenceItems,
  diagnosticsInvestigationActions,
  diagnosticsLinkedJobRow,
  diagnosticsCategoryLabel,
  diagnosticsRuleDescription,
  diagnosticsSeverityPresentation,
  diagnosticsSourceLabel,
} from "../diagnosticsPresentation";
import { hasValue, looksJson, parseJsonLike, renderGroupedValue } from "./detailPrimitives";

export function DiagnosticsDetailSections({
  row,
  timezoneName,
  onOpenJob,
  onOpenDataflow,
  onOpenSources,
}: {
  row: Record<string, unknown>;
  timezoneName?: string | null;
  onOpenJob?: (row: Record<string, unknown>) => void;
  onOpenDataflow?: (row: Record<string, unknown>) => void;
  onOpenSources?: () => void;
}) {
  const evidence = evidenceObject(row.evidence);
  const category = String(row.category ?? "diagnostics");
  const actionItems = diagnosticsInvestigationActions(row, evidence);
  const evidenceItems = diagnosticsEvidenceItems(category, row, evidence);
  const linkedJob = diagnosticsLinkedJobRow(row, evidence);
  const severity = diagnosticsSeverityPresentation(row.severity ?? row.status ?? "info");
  const relatedSource = row._source_id ?? row.source_id ?? evidence._source_id ?? evidence.source_id;
  const relatedDataflow = row.dataflow_id ?? row.dataflow_run_id ?? evidence.dataflow_id ?? evidence.dataflow_run_id ?? evidence.run_id;
  return (
    <>
      <section className="monitoring-detail-section monitoring-diagnostics-finding">
        <h3>What happened</h3>
        <div className="monitoring-diagnostics-finding-callout">
          <div className="monitoring-diagnostics-finding-meta"><span className={`diagnostics-severity diagnostics-${severity.tone}`}>{severity.label}</span>{row.category ? <span>{String(row.category).replace(/_/g, " ")}</span> : null}</div>
          <strong>{hasValue(row.issue) ? display(row, "issue") : String(row.reason ?? row.message ?? diagnosticsCategoryLabel(category))}</strong>
          <p>{diagnosticsRuleDescription(category)}</p>
          {hasValue(row.latest_time) ? (
            <small>Latest observed · {renderGroupedValue(row.latest_time, timezoneName, "latest_time")}</small>
          ) : null}
        </div>
      </section>

      {relatedSource !== undefined || relatedDataflow !== undefined ? (
        <section className="monitoring-detail-section monitoring-diagnostics-related-section">
          <h3>Related source and run</h3>
          <div className="monitoring-diagnostics-related-actions">
            {relatedSource !== undefined ? <button type="button" className="monitoring-related-link" onClick={() => onOpenSources?.()} disabled={!onOpenSources}>
              <span><strong>{diagnosticsSourceLabel(relatedSource)}</strong><small>Open Sources</small></span><ChevronRight size={15} aria-hidden="true" />
            </button> : null}
            {relatedDataflow !== undefined ? <button type="button" className="monitoring-related-link" onClick={() => onOpenDataflow?.(evidenceWithIdentity(row, evidence))} disabled={!onOpenDataflow}>
              <span><strong>{String(relatedDataflow)}</strong><small>Open dataflow run</small></span><ChevronRight size={15} aria-hidden="true" />
            </button> : null}
          </div>
        </section>
      ) : null}

      <section className="monitoring-detail-section">
        <h3>Evidence</h3>
        <div className="monitoring-diagnostics-evidence-grid">
          {evidenceItems.map((item) => (
            <div
              key={item.label}
              className={`monitoring-diagnostics-evidence-card${item.intent && item.intent !== "neutral" ? ` is-${item.intent}` : ""}${item.wide ? " is-wide" : ""}${item.primary ? " is-primary" : ""}`}
            >
              <span>{item.label}</span>
              <strong>{renderGroupedValue(item.value, timezoneName, item.field)}</strong>
            </div>
          ))}
        </div>
      </section>

      {linkedJob ? <DiagnosticsLinkedJobSection row={linkedJob} onOpenJob={onOpenJob} /> : null}

      {actionItems.length ? (
        <section className="monitoring-detail-section">
          <h3>Investigation path</h3>
          <ol className="monitoring-diagnostics-action-list">
            {actionItems.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ol>
        </section>
      ) : null}
    </>
  );
}

export function evidenceObject(value: unknown): Record<string, unknown> {
  if (value && typeof value === "object" && !Array.isArray(value)) return value as Record<string, unknown>;
  if (typeof value === "string" && looksJson(value)) {
    const parsed = parseJsonLike(value);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) return parsed as Record<string, unknown>;
  }
  return {};
}

export function DiagnosticsLinkedJobSection({
  row,
  onOpenJob,
}: {
  row: Record<string, unknown>;
  onOpenJob?: (row: Record<string, unknown>) => void;
}) {
  const jobId = row.job_id === null || row.job_id === undefined ? null : String(row.job_id);
  if (jobId === null) return null;
  return (
    <section className="monitoring-detail-section monitoring-linked-job-section">
      <h3>Linked job</h3>
      <button
        className="monitoring-linked-job-row is-diagnostics-link"
        type="button"
        onClick={() => onOpenJob?.(row)}
        disabled={!onOpenJob}
      >
        <BriefcaseBusiness size={15} aria-hidden="true" />
        <span className="monitoring-linked-job-identity">
          <strong>{jobId}</strong>
          <small>Open canonical job run</small>
        </span>
        <ChevronRight size={16} aria-hidden="true" />
      </button>
    </section>
  );
}

function evidenceWithIdentity(row: Record<string, unknown>, evidence: Record<string, unknown>) {
  return { ...row, ...evidence };
}
