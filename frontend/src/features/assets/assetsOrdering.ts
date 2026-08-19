import type { AssetInventoryItem, AssetReferenceGroupItem } from "../../shared/api/domainTypes";
import { compareNaturalText, normalizeStage } from "../../shared/stagePresentation";
import { compareConnectionNames } from "../../shared/connectionOrder";

export const RECOMMENDED_ASSET_SORT_KEY = "recommended";
export const RECOMMENDED_SORT = { sortBy: RECOMMENDED_ASSET_SORT_KEY, sortDir: "asc" as const };

const REFERENCE_RESOLUTION_ORDER = ["unresolved", "manual", "automatic"] as const;

export function orderAssetsByConnection(rows: AssetInventoryItem[]) {
  return [...rows].sort((left, right) => {
    const connectionDifference = compareConnectionNames(left.connection_name, right.connection_name);
    if (connectionDifference) return connectionDifference;
    const nameDifference = compareNaturalText(normalizeStage(left.display_name), normalizeStage(right.display_name));
    if (nameDifference) return nameDifference;
    return String(left.id).localeCompare(String(right.id), undefined, { numeric: true, sensitivity: "base" });
  });
}

export function orderReferencesByAction(rows: AssetReferenceGroupItem[]) {
  return [...rows].sort((left, right) => {
    const resolutionDifference = referenceResolutionRank(left) - referenceResolutionRank(right);
    if (resolutionDifference) return resolutionDifference;
    const attentionDifference = Number(right.attention_count || 0) - Number(left.attention_count || 0);
    if (attentionDifference) return attentionDifference;
    const nameDifference = compareNaturalText(normalizeStage(left.display_name), normalizeStage(right.display_name));
    if (nameDifference) return nameDifference;
    return String(left.id).localeCompare(String(right.id), undefined, { numeric: true, sensitivity: "base" });
  });
}

export function referenceResolutionRank(reference: AssetReferenceGroupItem) {
  const state = normalizeStage(reference.resolution?.state);
  const rank = REFERENCE_RESOLUTION_ORDER.indexOf(state as typeof REFERENCE_RESOLUTION_ORDER[number]);
  return rank >= 0 ? rank : REFERENCE_RESOLUTION_ORDER.length;
}

export function startsConnectionGroup(row: AssetInventoryItem, previous: AssetInventoryItem | undefined) {
  return !previous || normalizeStage(row.connection_name) !== normalizeStage(previous.connection_name);
}

export function startsReferenceResolutionGroup(row: AssetReferenceGroupItem, previous: AssetReferenceGroupItem | undefined) {
  return !previous || referenceResolutionRank(row) !== referenceResolutionRank(previous);
}
