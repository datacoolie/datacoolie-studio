import { monitoringAttempts, monitoringDiagnosticIssues, monitoringRawRecord } from "../../../shared/api/monitoringAdapters";
import { GroupedDetailCard, JsonBlock, hasValue, dataflowPhaseBottleneck, dataflowPhaseSegments, type DataflowPhaseKey } from "./detailPrimitives";
import { formatPhasePercent } from "../components/monitoringPresentation";
import { DataflowAttemptMessages } from "./DataflowMessages";
export { DataflowDetailSections } from "./DataflowStoryDetails";

/** Render the v6 authored request and native attempt evidence without flattening it into legacy fields. */
export function V6EvidenceSections({ row }: { row: Record<string, unknown> }) {
  const raw = monitoringRawRecord(row);
  const hasV6Evidence = hasOwn(raw, "metrics") || hasOwn(raw, "read_request") || hasOwn(raw, "attempts") || hasOwn(raw, "diagnostic_issues");
  if (!hasV6Evidence) return null;
  const attempts = monitoringAttempts(row);
  const diagnosticIssues = monitoringDiagnosticIssues(row);
  return (
    <section className="monitoring-detail-section monitoring-dataflow-v6-evidence">
      <h3>Run evidence</h3>
      <div className="monitoring-dataflow-detail-grid">
        {hasOwn(raw, "metrics") ? (
          <EvidenceCard title="Measured metrics" value={raw.metrics} />
        ) : null}
        {hasOwn(raw, "read_request") ? (
          <EvidenceCard title="Authored read request" value={raw.read_request} />
        ) : null}
        {hasValue(row.read_boundary_adjustment_state) ? (
          <GroupedDetailCard
            title="Read boundary adjustment"
            rows={[
              ["State", row.read_boundary_adjustment_state],
              ["Basis", row.read_boundary_adjustment_basis],
              ["Reason", row.read_boundary_adjustment_reason],
              ["Input lower", row.read_boundary_adjustment_input_json, "adjustment_input_json"],
              ["Resolved lower", row.read_boundary_adjustment_effective_json, "adjustment_effective_json"],
            ]}
            showEmpty
          />
        ) : null}
      </div>
      {hasOwn(raw, "attempts") ? (
        <section className="monitoring-detail-subsection monitoring-v6-attempts">
          <h4>Attempt history</h4>
          {attempts.length ? attempts.map((attempt, index) => (
            <V6AttemptCard key={`attempt-${index}`} attempt={attempt} index={index} />
          )) : <p className="monitor-muted">{noAttemptMessage(row)}</p>}
        </section>
      ) : null}
      {hasOwn(raw, "diagnostic_issues") ? (
        <section className="monitoring-detail-subsection monitoring-v6-diagnostics">
          <h4>Diagnostic issues</h4>
          <JsonBlock value={diagnosticIssues} />
        </section>
      ) : null}
    </section>
  );
}

export function noAttemptMessage(row: Record<string, unknown>) {
  const status = String(row.status ?? "").trim().toLowerCase();
  if (status === "skipped") return "No attempts recorded. The run was skipped before pipeline entry.";
  if (status === "failed") return "No attempts recorded. The run failed before pipeline entry.";
  if (status === "running" || status === "pending") return "No attempts recorded yet. Pipeline entry has not been reported.";
  return "No attempts recorded. Pipeline entry is unavailable for this run.";
}

function V6AttemptCard({ attempt, index }: { attempt: unknown; index: number }) {
  const record = asRecord(attempt) ?? {};
  const attemptNumber = record.attempt_number;
  const source = asRecord(record.source);
  const transform = asRecord(record.transform);
  const destination = asRecord(record.destination);
  const watermark = asRecord(record.watermark);
  const label = attemptNumber === undefined || attemptNumber === null ? "Attempt (unidentified)" : `Attempt ${String(attemptNumber)}`;
  const attemptRows = presentRows(record, ["attempt_number", "status", "start_time", "end_time", "duration_seconds"]);
  return (
    <article className="monitoring-v6-attempt-card">
      <h5>{label}</h5>
      <DataflowAttemptMessages attempt={record} />
      {attemptRows.length ? <GroupedDetailCard title="Execution" rows={attemptRows} showEmpty /> : null}
      <div className="monitoring-dataflow-detail-grid">
        {source ? <V6PhaseEvidence title="Source" phase={source} selectionLabel="Resolved selection" /> : null}
        {transform ? <V6PhaseEvidence title="Transform" phase={transform} /> : null}
        {destination ? <V6PhaseEvidence title="Destination" phase={destination} /> : null}
        {watermark ? <V6WatermarkEvidence watermark={watermark} /> : null}
      </div>
      {!attemptRows.length && !source && !transform && !destination && !watermark && !hasValue(record.message) && record.message_truncated !== true ? (
        <p className="monitor-muted">Attempt {index + 1} has no typed detail.</p>
      ) : null}
    </article>
  );
}

function V6PhaseEvidence({ title, phase, selectionLabel }: { title: string; phase: Record<string, unknown>; selectionLabel?: string }) {
  const selection = phase.selection;
  const sourceAction = phase.source_action;
  const excluded = new Set(["selection", "source_action", "message", "message_truncated"]);
  const rows = presentRows(phase, Object.keys(phase).filter((key) => !excluded.has(key)));
  return (
    <div className="monitoring-v6-phase-evidence">
      <GroupedDetailCard title={title} rows={rows} showEmpty />
      {selection !== undefined ? (
        <div className="monitoring-v6-evidence-block">
          <span>{selectionLabel ?? "Selection"}</span>
          <JsonBlock value={selection} />
        </div>
      ) : null}
      {sourceAction !== undefined ? (
        <div className="monitoring-v6-evidence-block">
          <span>Source action</span>
          <JsonBlock value={sourceAction} />
        </div>
      ) : null}
    </div>
  );
}

function V6WatermarkEvidence({ watermark }: { watermark: Record<string, unknown> }) {
  const candidate = asRecord(watermark.candidate);
  const rows = presentRows(watermark, ["loaded_watermark", "merge_base", "watermark_to_save", "save_status", "saved_watermark", "load_status"]);
  return (
    <div className="monitoring-v6-watermark-evidence">
      <GroupedDetailCard title="Watermark and persistence" rows={rows} showEmpty />
      {candidate ? (
        <div className="monitoring-v6-evidence-block">
          <span>Candidate values</span>
          <JsonBlock value={candidate.values ?? candidate} />
          {candidate.kind !== undefined ? <small>Candidate kind: {String(candidate.kind)}</small> : null}
        </div>
      ) : null}
    </div>
  );
}

function EvidenceCard({ title, value }: { title: string; value: unknown }) {
  return (
    <div className="monitoring-v6-evidence-card">
      <h4>{title}</h4>
      <JsonBlock value={value} />
    </div>
  );
}

function presentRows(record: Record<string, unknown>, keys: string[]) {
  return keys.filter((key) => hasOwn(record, key)).map((key) => [humanize(key), record[key], key] as [string, unknown, string]);
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function hasOwn(value: Record<string, unknown>, key: string) {
  return Object.prototype.hasOwnProperty.call(value, key);
}

function humanize(value: string) {
  return value.replace(/_/g, " ").replace(/\b\w/g, (character) => character.toUpperCase());
}

export function hasPerformanceEvidence(row: Record<string, unknown>) {
  return [
    row.performance_bottleneck_phase,
    row.performance_candidate_code,
    row.performance_candidate_reason,
    row.performance_candidate_priority,
    row.performance_rows_processed,
    row.performance_rows_per_second,
    row.performance_overhead_ratio,
    row.performance_dominant_phase_ratio,
  ].some(hasValue);
}

export function DataflowPhaseBottleneck({ row }: { row: Record<string, unknown> }) {
  const bottleneck = dataflowPhaseBottleneck(row);
  if (!bottleneck) return <span className="monitor-muted">-</span>;
  return (
    <span className="monitoring-phase-bottleneck">
      <span className={`phase-text-${bottleneck.phase}`}>{bottleneck.label}</span>
      {" "}
      <PhaseContributionSummary row={row} />
    </span>
  );
}

export function PhaseContributionSummary({ row }: { row: Record<string, unknown> }) {
  const segments = dataflowPhaseSegments(row);
  if (!segments.length) return null;
  return (
    <span className="monitoring-phase-bottleneck-summary" aria-label="Phase contribution summary">
      <span className="monitoring-phase-summary-punctuation">(</span>
      {segments.map((segment) => (
        <span key={segment.phase} className={`phase-text-${segment.phase}`}>
          {phaseShortLabel(segment.phase)} {formatPhasePercent(segment.percent)}
        </span>
      ))}
      <span className="monitoring-phase-summary-punctuation">)</span>
    </span>
  );
}

export function phaseShortLabel(phase: DataflowPhaseKey) {
  if (phase === "source") return "S";
  if (phase === "transform") return "T";
  if (phase === "destination") return "D";
  return "O";
}
