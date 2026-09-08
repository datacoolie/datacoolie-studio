import { compareNaturalText, normalizeStage } from "./stagePresentation";

export const CONNECTION_STAGE_FAMILIES = ["source", "bronze", "silver", "gold"] as const;
export type ConnectionStageFamily = typeof CONNECTION_STAGE_FAMILIES[number];

const CONNECTION_STAGE_PATTERN = /(?:^|[^a-z0-9])(source|bronze|silver|gold)\d*(?=$|[^a-z0-9])/g;

export function connectionStageFamily(connectionName: unknown) {
  const normalized = normalizeStage(connectionName);
  let firstMatch: { family: ConnectionStageFamily; index: number; rank: number } | null = null;

  for (const match of normalized.matchAll(CONNECTION_STAGE_PATTERN)) {
    const family = match[1] as ConnectionStageFamily;
    const index = (match.index ?? 0) + match[0].indexOf(family);
    const rank = CONNECTION_STAGE_FAMILIES.indexOf(family);
    if (!firstMatch || index < firstMatch.index || (index === firstMatch.index && rank < firstMatch.rank)) {
      firstMatch = { family, index, rank };
    }
  }

  return firstMatch?.family ?? null;
}

export function connectionStageRank(connectionName: unknown) {
  const family = connectionStageFamily(connectionName);
  return family ? CONNECTION_STAGE_FAMILIES.indexOf(family) : CONNECTION_STAGE_FAMILIES.length;
}

export function compareConnectionNames(left: unknown, right: unknown) {
  const leftValue = normalizeStage(left);
  const rightValue = normalizeStage(right);
  const familyDifference = connectionStageRank(leftValue) - connectionStageRank(rightValue);
  if (familyDifference) return familyDifference;
  if (!leftValue && rightValue) return 1;
  if (leftValue && !rightValue) return -1;
  return compareNaturalText(leftValue, rightValue);
}
