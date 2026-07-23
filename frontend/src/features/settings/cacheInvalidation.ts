import type { QueryKey } from "@tanstack/react-query";
import type { StudioCacheFeature, StudioCacheScope } from "../../shared/api/domainTypes";

export type DerivedQueryBranch = "overview" | "assets" | "lineage" | "monitoring";

const ALL_DERIVED_BRANCHES: DerivedQueryBranch[] = ["overview", "assets", "lineage", "monitoring"];

export function cacheInvalidationBranches(
  scope: StudioCacheScope,
  features: StudioCacheFeature[] = [],
): DerivedQueryBranch[] {
  if (scope === "all_disposable") return ALL_DERIVED_BRANCHES;
  if (scope === "analytics") return ["overview", "monitoring"];
  if (features.length === 0) return ALL_DERIVED_BRANCHES;
  return [...new Set(features)];
}

export function matchesDerivedQuery(
  queryKey: QueryKey,
  branches: DerivedQueryBranch[],
  environmentId?: number,
): boolean {
  if (queryKey[0] !== "environments") return false;
  if (environmentId !== undefined && queryKey[1] !== environmentId) return false;
  return branches.includes(queryKey[2] as DerivedQueryBranch);
}
