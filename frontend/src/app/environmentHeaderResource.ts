import { api } from "../shared/api/client";
import type { EnvironmentFreshness } from "../shared/api/types";

export const DEFAULT_SOURCE_CHECK_INTERVAL_SECONDS = 30;

export interface EnvironmentHeaderData {
  freshness: EnvironmentFreshness;
}

/** Fetches only the data rendered in the shared Environment Context Bar. */
export async function fetchEnvironmentHeader(environmentId: number): Promise<EnvironmentHeaderData> {
  return { freshness: await api.getEnvironmentFreshness(environmentId) };
}

export function sourceCheckIntervalMs(value: number | null | undefined) {
  const seconds = Number.isInteger(value) && Number(value) >= 5 && Number(value) <= 3600
    ? Number(value)
    : DEFAULT_SOURCE_CHECK_INTERVAL_SECONDS;
  return seconds * 1_000;
}

export function sourceCacheVersionChanged(previous: EnvironmentHeaderData, next: EnvironmentHeaderData) {
  return previous.freshness.source_cache_version !== next.freshness.source_cache_version;
}

export function structuralCacheVersionChanged(previous: EnvironmentHeaderData, next: EnvironmentHeaderData) {
  return previous.freshness.structural_cache_version !== next.freshness.structural_cache_version;
}
