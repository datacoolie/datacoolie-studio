import { ErrorMessageBlock, GroupedDetailCard, firstValue, hasValue } from "./detailPrimitives";

export function FailureEvidenceSections({ row, timezoneName }: { row: Record<string, unknown>; timezoneName?: string | null }) {
  const message = failureErrorMessage(row);
  const diagnosticTags = failureDiagnosticTags(row.failure_tags);
  const hasFailureContext = [
    row.failure_phase,
    row.failure_category,
  ].some(hasValue);
  if (!message && !hasFailureContext) return null;
  return (
    <section className="monitoring-detail-section monitoring-failure-evidence-section">
      <h3>Errors and notes</h3>
      {message ? <ErrorMessageBlock value={message} /> : null}
      {hasFailureContext ? (
        <div className="monitoring-dataflow-detail-grid">
          <GroupedDetailCard
            title="Failure context"
            rows={[
              ["Failure phase", row.failure_phase],
              ["Failure category", row.failure_category],
            ]}
            timezoneName={timezoneName}
          />
        </div>
      ) : null}
      {diagnosticTags.length ? (
        <div className="monitoring-failure-diagnostic-signals" aria-label="Failure diagnostic signals">
          <strong>Diagnostic signals</strong>
          <div>
            {diagnosticTags.map((tag) => <span key={tag}>{tag}</span>)}
          </div>
        </div>
      ) : null}
    </section>
  );
}

export function failureDiagnosticTags(value: unknown) {
  if (!Array.isArray(value)) return [];
  return Array.from(new Set(
    value.map((item) => String(item ?? "").trim()).filter(Boolean)
  )).slice(0, 5);
}

export function failureErrorMessage(row: Record<string, unknown>) {
  return firstValue(row, [
    "failure_message",
    "source_error_message",
    "transform_error_message",
    "destination_error_message",
    "error_messages",
    "error_message",
  ]);
}
