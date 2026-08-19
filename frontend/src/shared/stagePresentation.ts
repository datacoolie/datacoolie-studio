export const STAGE_TONE_COUNT = 12;
export type StageToneMap = ReadonlyMap<string, number>;

export function normalizeStage(value: unknown) {
  return String(value ?? "").trim().toLowerCase();
}

export function stageFamilyRank(value: unknown) {
  const normalized = normalizeStage(value);
  const families = ["source", "bronze", "silver", "gold"];
  const familyIndex = families.findIndex((family) => normalized.startsWith(family));
  return familyIndex >= 0 ? familyIndex : families.length;
}

export function compareStageValues(left: unknown, right: unknown) {
  const leftValue = normalizeStage(left);
  const rightValue = normalizeStage(right);
  if (!leftValue && rightValue) return 1;
  if (leftValue && !rightValue) return -1;
  const familyDifference = stageFamilyRank(leftValue) - stageFamilyRank(rightValue);
  if (familyDifference) return familyDifference;
  return compareNaturalText(leftValue, rightValue);
}

export function compareNaturalText(left: string, right: string) {
  const leftParts = left.split(/(\d+)/).filter(Boolean);
  const rightParts = right.split(/(\d+)/).filter(Boolean);
  const partCount = Math.max(leftParts.length, rightParts.length);

  for (let index = 0; index < partCount; index += 1) {
    const leftPart = leftParts[index];
    const rightPart = rightParts[index];
    if (leftPart == null) return -1;
    if (rightPart == null) return 1;

    const leftIsNumeric = /^\d+$/.test(leftPart);
    const rightIsNumeric = /^\d+$/.test(rightPart);
    if (leftIsNumeric && rightIsNumeric) {
      const leftDigits = leftPart.replace(/^0+(?=\d)/, "");
      const rightDigits = rightPart.replace(/^0+(?=\d)/, "");
      if (leftDigits.length !== rightDigits.length) return leftDigits.length - rightDigits.length;
      if (leftDigits !== rightDigits) return leftDigits < rightDigits ? -1 : 1;
      continue;
    }
    if (leftIsNumeric !== rightIsNumeric) return leftIsNumeric ? -1 : 1;
    if (leftPart !== rightPart) return leftPart < rightPart ? -1 : 1;
  }

  return 0;
}

function preferredStageToneIndex(value: unknown) {
  const normalized = normalizeStage(value);
  if (!normalized) return null;

  let hash = 2166136261;
  for (let index = 0; index < normalized.length; index += 1) {
    hash ^= normalized.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0) % STAGE_TONE_COUNT;
}

export function createStageToneMap(values: Iterable<unknown>): StageToneMap {
  const identities = [...new Set([...values].map(normalizeStage).filter(Boolean))].sort(compareStageValues);
  const freeTones = new Set(Array.from({ length: STAGE_TONE_COUNT }, (_, index) => index));
  const toneMap = new Map<string, number>();

  for (const identity of identities) {
    const preferredTone = preferredStageToneIndex(identity);
    if (preferredTone == null) continue;
    const tone = freeTones.has(preferredTone)
      ? preferredTone
      : freeTones.values().next().value ?? preferredTone;
    toneMap.set(identity, tone);
    freeTones.delete(tone);
  }

  return toneMap;
}

export function stageToneIndex(value: unknown, toneMap?: StageToneMap) {
  const normalized = normalizeStage(value);
  if (!normalized) return null;
  return toneMap?.get(normalized) ?? preferredStageToneIndex(normalized);
}

export function stageToneClass(value: unknown, toneMap?: StageToneMap) {
  const tone = stageToneIndex(value, toneMap);
  return tone == null
    ? "metadata-stage-tone metadata-stage-tone-neutral"
    : `metadata-stage-tone metadata-stage-tone-${tone}`;
}

export interface StageSummaryItem {
  name: string;
  count: number;
}

export function normalizeStageSummary(items: ReadonlyArray<StageSummaryItem>) {
  const grouped = new Map<string, StageSummaryItem>();
  for (const item of items) {
    const identity = normalizeStage(item.name);
    if (!identity) continue;
    const current = grouped.get(identity);
    if (current) {
      current.count += Number.isFinite(item.count) ? item.count : 0;
    } else {
      grouped.set(identity, {
        name: String(item.name).trim(),
        count: Number.isFinite(item.count) ? item.count : 0
      });
    }
  }
  return [...grouped.values()].sort((left, right) => compareStageValues(left.name, right.name));
}

export function stageFilterMatches(value: unknown, filter: unknown) {
  const normalizedFilter = normalizeStage(filter);
  return Boolean(normalizedFilter) && normalizeStage(value) === normalizedFilter;
}
