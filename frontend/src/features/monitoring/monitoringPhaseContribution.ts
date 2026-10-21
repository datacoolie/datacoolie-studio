import { isLosslessJsonNumber, parseRawRecordJson } from "../../shared/api/monitoringAdapters";

export const CONTRIBUTION_PHASES = ["source", "transform", "destination", "overhead"] as const;
export type ContributionPhase = typeof CONTRIBUTION_PHASES[number];
type RecordValue = Record<string, unknown>;

function record(value: unknown): RecordValue | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) && !isLosslessJsonNumber(value) ? value as RecordValue : null;
}

function duration(value: unknown): number | null {
  const scalar = isLosslessJsonNumber(value) ? value.value : value;
  if (typeof scalar !== "number" && (typeof scalar !== "string" || !scalar.trim())) return null;
  const number = Number(scalar);
  return Number.isFinite(number) && number >= 0 ? number : null;
}

function contribution(phases: Array<{phase: ContributionPhase; duration: number | null; status: string | null; path: string}>, unavailable: boolean) {
  const total = phases.reduce((sum, phase) => sum + (phase.duration ?? 0), 0);
  const measured = phases.filter(phase => phase.duration !== null).length;
  const proportional = !unavailable && total > 0 && Number.isFinite(total);
  return {phases: phases.map(phase => ({...phase, share: proportional && phase.duration !== null ? phase.duration / total * 100 : null})), total, measured, proportional, unavailable};
}

/** Native final-attempt observations; status and diagnostic health never admit timing. */
export function recordedPhaseTiming(native: RecordValue | null, ready = true) {
  const attempts = ready && Array.isArray(native?.attempts) ? native.attempts : null;
  const index = attempts ? attempts.length - 1 : -1;
  const final = index >= 0 ? record(attempts![index]) : null;
  const phases = CONTRIBUTION_PHASES.slice(0, 3).map(phase => {
    const value = record(final?.[phase]);
    const path = `/attempts/${index}/${phase}`;
    const hasIssue = Array.isArray(native?.diagnostic_issues) && native.diagnostic_issues.some(issue => {
      const field = record(issue)?.field;
      return typeof field === "string" && (field === path || field.startsWith(`${path}/`));
    });
    return {phase, duration: duration(value?.duration_seconds), path, status: value ? typeof value.status === "string" && value.status.trim() ? value.status : "Unknown" : attempts === null || hasIssue ? "Detail unavailable" : "No phase recorded"};
  });
  const elapsed = duration(final?.duration_seconds);
  const phaseTotal = phases.reduce((sum, phase) => sum + (phase.duration ?? 0), 0);
  const overhead = elapsed !== null && phases.every(phase => final?.[phase.phase] == null || phase.duration !== null) ? Math.max(0, elapsed - phaseTotal) : null;
  return contribution([...phases, {phase: "overhead", duration: overhead, status: null, path: `/attempts/${index}/duration_seconds`}], !ready || !final);
}

/** Native evidence wins, even when empty/invalid. Projection-only callers use observed fields. */
export function rowPhaseContribution(row: RecordValue) {
  const hasPayload = typeof row.raw_record_json === "string" && Boolean(row.raw_record_json);
  const native = hasPayload ? record(parseRawRecordJson(row.raw_record_json)) : row;
  const root = native ? {...row, ...native} : row;
  const timing = hasPayload || Object.prototype.hasOwnProperty.call(row, "attempts") ? recordedPhaseTiming(native) : contribution(CONTRIBUTION_PHASES.map(phase => {
    const aggregateField = `performance_${phase}_duration_seconds`;
    const field = Object.prototype.hasOwnProperty.call(row, aggregateField) ? aggregateField : `${phase}_duration_seconds`;
    return {
      phase,
      duration: duration(row[field]),
      status: phase === "overhead" ? null : typeof row[`${phase}_status`] === "string" ? String(row[`${phase}_status`]) : "Unknown",
      path: `/${field}`,
    };
  }), false);
  return {root, timing};
}

export function phaseContributionBottleneck(row: RecordValue, timing: ReturnType<typeof recordedPhaseTiming>) {
  const reported = typeof row.performance_bottleneck_phase === "string" && CONTRIBUTION_PHASES.includes(row.performance_bottleneck_phase as ContributionPhase) ? row.performance_bottleneck_phase as ContributionPhase : null;
  const largest = Math.max(...timing.phases.map(phase => phase.duration ?? 0));
  const phases = reported ? [reported] : timing.proportional ? timing.phases.filter(phase => phase.duration === largest).map(phase => phase.phase) : [];
  return {reported, phases, label: phases.length ? phases.map(phase => phase[0].toUpperCase() + phase.slice(1)).join(" · ") : "unk"};
}
