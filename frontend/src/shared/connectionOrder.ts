import { compareNaturalText, normalizeStage, stageFamilyRank } from "./stagePresentation";

export const CONNECTION_STAGE_FAMILIES = ["source", "bronze", "silver", "gold"] as const;
export type ConnectionStageFamily = typeof CONNECTION_STAGE_FAMILIES[number];

export function connectionStageFamily(connectionName: unknown) {
  const normalized = normalizeStage(connectionName);
  const familyRank = stageFamilyRank(normalized);
  return CONNECTION_STAGE_FAMILIES[familyRank] ?? null;
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
