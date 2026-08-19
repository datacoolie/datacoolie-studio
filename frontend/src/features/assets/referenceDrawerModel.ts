import type { AssetBrief, AssetReferenceGroupItem, AssetReferenceOccurrenceItem } from "../../shared/api/domainTypes";

export interface ReferenceResolutionStory {
  tone: "resolved" | "attention" | "warning" | "review";
  title: string;
  detail: string;
}

export function referenceResolutionStory(
  reference: AssetReferenceGroupItem,
  occurrences: AssetReferenceOccurrenceItem[],
): ReferenceResolutionStory {
  const occurrenceCount = referenceOccurrenceCount(reference, occurrences);
  const targetName = reference.resolved_asset?.friendly_name
    || reference.resolved_asset?.display_name
    || reference.resolved_asset_id
    || "one canonical asset";

  if (reference.resolution.state !== "unresolved") {
    return {
      tone: "resolved",
      title: `All occurrences resolve to ${targetName}.`,
      detail: reference.manual_mapping?.mapping_id
        ? "A project-level manual mapping selects this target."
        : "The resolver found one unique canonical target.",
    };
  }
  if (reference.resolution.reason === "multiple_matches") {
    const candidateCount = reference.candidate_asset_ids.length;
    return {
      tone: "warning",
      title: `${plural(candidateCount, "canonical candidate")} match this reference.`,
      detail: "The resolver cannot select one unique target.",
    };
  }
  if (reference.resolution.reason === "out_of_scope") {
    const candidateCount = reference.candidate_asset_ids.length;
    return {
      tone: "attention",
      title: "A matching asset is outside the source scope.",
      detail: candidateCount
        ? `${plural(candidateCount, "candidate")} found; review it or add a manual mapping.`
        : "Review the source scope or add a manual mapping.",
    };
  }
  if (reference.resolution.reason === "target_missing") {
    const missingTarget = reference.manual_mapping?.target_normalized_value;
    return {
      tone: "attention",
      title: "The mapped target is missing from this environment.",
      detail: missingTarget
        ? `${missingTarget} is configured at project scope but is not a declared canonical asset here.`
        : "The project mapping target is unavailable in this environment.",
    };
  }
  if (reference.resolution.reason === "incomplete") {
    const resolvedCount = occurrences.filter((item) => item.resolution.state !== "unresolved").length;
    return {
      tone: "warning",
      title: `${resolvedCount} of ${occurrenceCount} occurrences resolve successfully.`,
      detail: "A project mapping can set one target for every occurrence of this reference.",
    };
  }
  if (reference.resolution.reason === "conflicting_targets") {
    return {
      tone: "review",
      title: `Occurrences resolve to ${plural(reference.resolved_asset_ids.length, "different asset")}.`,
      detail: "Review the current targets, or edit the project mapping to use one target consistently.",
    };
  }
  return {
    tone: "attention",
    title: "No declared canonical asset matches this reference.",
    detail: reference.candidate_asset_ids.length
      ? `${plural(reference.candidate_asset_ids.length, "possible candidate")} were detected.`
      : "No candidate was found.",
  };
}

export function referenceOccurrenceCount(
  reference: AssetReferenceGroupItem,
  occurrences: AssetReferenceOccurrenceItem[],
) {
  return occurrences.length || reference.occurrence_count || reference.occurrence_ids.length || reference.dependency_count;
}

export interface ReferenceUsageGroup {
  id: string;
  consumer: AssetBrief | null;
  occurrences: AssetReferenceOccurrenceItem[];
}

export function groupReferenceUsage(
  reference: AssetReferenceGroupItem,
  occurrences: AssetReferenceOccurrenceItem[],
): ReferenceUsageGroup[] {
  const groups = new Map<string, ReferenceUsageGroup>();
  for (const consumer of reference.consumer_assets) {
    groups.set(consumer.id, { id: consumer.id, consumer, occurrences: [] });
  }
  for (const occurrence of occurrences) {
    const id = occurrence.consumer_asset_id || "unknown";
    const existing = groups.get(id);
    if (existing) {
      existing.occurrences.push(occurrence);
      continue;
    }
    groups.set(id, { id, consumer: occurrence.consumer_asset || null, occurrences: [occurrence] });
  }
  return [...groups.values()].sort((left, right) => usageGroupLabel(left).localeCompare(usageGroupLabel(right)));
}

export function occurrenceLocationLabel(occurrence: AssetReferenceOccurrenceItem) {
  for (const observation of occurrence.observations) {
    const location = recordField(observation, "location");
    const path = stringField(location, "path") || stringField(location, "module");
    if (path) {
      const line = positiveNumber(location?.line);
      const column = positiveNumber(location?.column);
      return [path, line, column].filter((item) => item !== null).join(":");
    }
  }
  if (occurrence.observations.some((item) => Boolean(stringField(item, "sql")))) return "SQL query";
  return null;
}

export function occurrenceResolutionMethod(occurrence: AssetReferenceOccurrenceItem) {
  return humanize(occurrence.resolution_method) || humanize(occurrence.resolution.state) || "unknown result";
}

export function occurrenceScopeLabel(occurrence: AssetReferenceOccurrenceItem) {
  const scope = occurrence.context_scope?.trim();
  if (!scope || normalizeText(occurrence.normalized_value).includes(normalizeText(scope))) return null;
  return `context: ${scope}`;
}

export function shouldShowNormalizedValue(reference: AssetReferenceGroupItem) {
  return normalizeText(reference.display_name) !== normalizeText(reference.normalized_value);
}

export function plural(count: number, singular: string) {
  return `${count} ${singular}${count === 1 ? "" : "s"}`;
}

function recordField(value: Record<string, unknown>, key: string) {
  const field = value[key];
  return field && typeof field === "object" && !Array.isArray(field) ? field as Record<string, unknown> : null;
}

function stringField(value: Record<string, unknown> | null, key: string) {
  const field = value?.[key];
  return typeof field === "string" && field.trim() ? field.trim() : null;
}

function positiveNumber(value: unknown) {
  const result = Number(value);
  return Number.isInteger(result) && result > 0 ? result : null;
}

function usageGroupLabel(group: ReferenceUsageGroup) {
  return group.consumer?.friendly_name || group.consumer?.display_name || "Unknown consumer";
}

function humanize(value: string | null | undefined) {
  return value?.trim().replace(/_/gu, " ") || null;
}

function normalizeText(value: string | null | undefined) {
  return String(value || "").trim().toLocaleLowerCase().replace(/\\/gu, "/").replace(/\/+$/gu, "");
}
