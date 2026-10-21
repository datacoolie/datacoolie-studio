import { isLosslessJsonNumber, parseRawRecordJson } from "../../../shared/api/monitoringAdapters";

export type StoryRecord = Record<string, unknown>;
export type DetailAvailability = "loading" | "error" | "not-found" | "ready";
export const storyPhases = ["source", "transform", "destination"] as const;
export type StoryPhase = typeof storyPhases[number];
export type StoryMessage = { text: string; labels: string[]; truncatedLabels: string[]; coverage?: { label: string; reason: string }[] };
export const storyMetricFields = ["rows_read", "rows_written", "rows_inserted", "rows_updated", "rows_deleted", "files_added", "files_removed", "bytes_added", "bytes_removed", "bytes_saved"] as const;

export function storyIssues(diagnostics: unknown[] | null | undefined, path: string) {
  // Slash boundaries preserve escaped pointer components and exact attempt
  // indices: /attempts/1 must never match /attempts/10.
  return (diagnostics ?? []).flatMap(value => {
    const issue = storyRecord(value);
    return typeof issue?.field === "string" && typeof issue.reason === "string" && (issue.field === path || issue.field.startsWith(`${path}/`))
      ? [{ field: issue.field, reason: issue.reason }] : [];
  });
}

export function storyRecord(value: unknown): StoryRecord | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) && !isLosslessJsonNumber(value)
    ? value as StoryRecord : null;
}

export function storyScalar(value: unknown): unknown {
  return isLosslessJsonNumber(value) ? value.value : value;
}

export function storyStatus(value: unknown): string {
  return typeof value === "string" && value.trim() ? value.trim() : "Unknown";
}

export function storyLabel(value: string) {
  return value[0].toUpperCase() + value.slice(1);
}

export function storyNumber(value: unknown): number | null {
  const scalar = storyScalar(value);
  if (scalar === null || scalar === undefined || scalar === "" || typeof scalar === "boolean") return null;
  if (typeof scalar !== "string" && typeof scalar !== "number") return null;
  const number = Number(scalar);
  return Number.isFinite(number) ? number : null;
}

export function buildDataflowStory(row: StoryRecord, availability: DetailAvailability = "ready") {
  const payload = typeof row.raw_record_json === "string" ? row.raw_record_json : null;
  const parsed = payload ? storyRecord(parseRawRecordJson(payload)) : null;
  const native = parsed ?? (payload ? null : row);
  const root = parsed ? { ...row, ...parsed } : row;
  // A partial/error payload never establishes absence. The native array owns order.
  const attempts = availability === "ready" && native && Array.isArray(native.attempts) ? native.attempts : null;
  const finalIndex = attempts ? attempts.length - 1 : -1;
  const final = finalIndex >= 0 ? storyRecord(attempts![finalIndex]) : null;
  const metrics = storyRecord(native?.metrics);
  const diagnostics = native && Array.isArray(native.diagnostic_issues) ? native.diagnostic_issues : null;
  const recovered = storyStatus(root.status).toLowerCase() === "succeeded"
    && Boolean(attempts?.slice(0, -1).some((attempt) => storyStatus(storyRecord(attempt)?.status).toLowerCase() === "failed"))
    && storyStatus(final?.status).toLowerCase() === "succeeded";
  const phaseDurations = storyPhases.flatMap((phase) => {
    const duration = storyNumber(storyRecord(final?.[phase])?.duration_seconds);
    return duration !== null && duration >= 0 ? [{ phase, duration }] : [];
  });
  const longest = phaseDurations.length ? Math.max(...phaseDurations.map((phase) => phase.duration)) : null;
  return {
    root, native, payload, attempts, final, finalIndex, metrics, diagnostics, availability, recovered,
    longestPhases: phaseDurations.filter((phase) => phase.duration === longest).map((phase) => phase.phase),
    messages: storyMessages(root, final, finalIndex, diagnostics, true),
  };
}

export type DataflowStory = ReturnType<typeof buildDataflowStory>;

/** Display chronology changes without changing the producer's indices or data. */
export function storyAttemptsDescending(model: DataflowStory) {
  return (model.attempts ?? []).map((value, nativeIndex) => ({
    record: storyRecord(value), nativeIndex, isFinal: nativeIndex === model.finalIndex,
  })).reverse();
}

/** Select recorded outcome facts without treating performance or volume as cause. */
export function storyOutcome(model: DataflowStory) {
  const status = storyStatus(model.root.status).toLowerCase();
  const nativeFailure = storyPhases.find(phase => storyStatus(storyRecord(model.final?.[phase])?.status).toLowerCase() === "failed");
  const reportedFailure = typeof model.root.failure_phase === "string" && model.root.failure_phase.trim()
    ? model.root.failure_phase.trim() : null;
  const failurePhase = status === "failed" ? reportedFailure ?? nativeFailure : null;
  const headline = model.recovered ? "Succeeded after retry"
    : ({ succeeded: "Succeeded", failed: "Failed", skipped: "Skipped", running: "Running", pending: "Pending" }[status] ?? "Outcome unknown");
  let detail = "";
  if (failurePhase) detail = `Failure reported in ${failurePhase}.`;
  else if (status === "failed") detail = "Failure phase unknown.";
  else if (status === "succeeded" && storyPhases.every(phase => storyStatus(storyRecord(model.final?.[phase])?.status).toLowerCase() === "succeeded")) {
    detail = "All three phases reported success.";
  } else if (status === "skipped") detail = model.messages.some(message => message.text.trim()) ? "See the recorded message for the reason." : "Skip reason is unavailable.";
  else if (status === "running" || status === "pending") detail = "Only reported execution details are shown.";
  if (model.recovered) detail = "A previous attempt failed; the final attempt succeeded.";
  return { headline, detail, failurePhase };
}

export function storyMetric(model: DataflowStory, metric: string): unknown {
  // Root metrics never become phase execution evidence.
  if (model.metrics) return model.metrics[metric];
  const alias = metric === "rows_read" ? "source_rows_read" : `destination_${metric}`;
  return model.root[alias];
}

export function storyMessages(record: StoryRecord, final: StoryRecord | null, index: number, diagnostics: unknown[] | null, runScope = false) {
  const entries = [{ label: runScope ? "Run" : "Attempt", record, path: runScope ? "/message" : `/attempts/${index}/message` }];
  if (runScope && final) entries.push({label:"Attempt",record:final,path:`/attempts/${index}/message`});
  for (const phase of storyPhases) {
    const phaseRecord = storyRecord(final?.[phase]);
    if (phaseRecord) entries.push({ label: storyLabel(phase), record: phaseRecord, path: `/attempts/${index}/${phase}/message` });
  }
  const groups: StoryMessage[] = [];
  for (const entry of entries) {
    const text = typeof entry.record.message === "string" ? entry.record.message : "";
    const truncated = entry.record.message_truncated === true || Boolean(diagnostics?.some((value) => {
      const diagnostic = storyRecord(value);
      return diagnostic?.field === entry.path && diagnostic.reason === "truncated";
    }));
    const coverage = storyIssues(diagnostics,entry.path).map(issue=>({label:entry.label,reason:issue.reason}));
    if (!text.trim() && !truncated && !coverage.length) continue;
    // Exact equality only within this scope. Retain every origin and its coverage.
    let group = groups.find((item) => item.text === text);
    if (!group) { group = { text, labels: [], truncatedLabels: [], coverage: [] }; groups.push(group); }
    group.labels.push(entry.label);
    if (truncated) group.truncatedLabels.push(entry.label);
    group.coverage!.push(...coverage);
  }
  return groups;
}

export function noStoryAttemptMessage(model: DataflowStory) {
  if (model.attempts === null || (model.attempts.length > 0 && !model.final)) return "Attempt evidence unavailable.";
  const status = storyStatus(model.root.status).toLowerCase();
  if (status === "pending" || status === "running") return "No attempts recorded yet. Pipeline entry has not been reported.";
  if (status === "skipped" || status === "failed") return `No attempts recorded. The run ${status === "skipped" ? "was skipped" : "failed"} before pipeline entry.`;
  return "No attempts recorded. Pipeline entry is unavailable for this run.";
}
