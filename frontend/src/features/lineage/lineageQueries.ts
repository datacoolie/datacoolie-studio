import { infiniteQueryOptions, queryOptions, useQuery } from "@tanstack/react-query";
import { api } from "../../shared/api/client";
import { environmentQueryKeys } from "../environments/environmentQueries";

const structuralStaleTime = Number.POSITIVE_INFINITY;
export const LINEAGE_RUN_HISTORY_PAGE_SIZE = 10;

export const lineageQueryKeys = {
  graph: (environmentId: number, includeDraft = false) => (
    includeDraft
      ? [...environmentQueryKeys.lineage(environmentId), "draft"] as const
      : environmentQueryKeys.lineage(environmentId)
  ),
  latestStatus: (environmentId: number) => (
    [...environmentQueryKeys.monitoring(environmentId), "latest-status"] as const
  ),
  dataflowRuns: (environmentId: number, dataflowId: string, dataflowName: string) => (
    [...environmentQueryKeys.monitoring(environmentId), "lineage-dataflow-runs", dataflowId, dataflowName] as const
  ),
};

export function lineageGraphOptions(environmentId: number, includeDraft = false) {
  return queryOptions({
    queryKey: lineageQueryKeys.graph(environmentId, includeDraft),
    queryFn: ({ signal }) => api.getLineage(environmentId, includeDraft, signal),
    staleTime: structuralStaleTime,
  });
}

export function lineageLatestStatusOptions(environmentId: number) {
  return queryOptions({
    queryKey: lineageQueryKeys.latestStatus(environmentId),
    queryFn: ({ signal }) => api.getLatestStatus(environmentId, signal),
    staleTime: structuralStaleTime,
  });
}

export function lineageDataflowRunsOptions(environmentId: number, dataflowId: string, dataflowName: string) {
  return infiniteQueryOptions({
    queryKey: lineageQueryKeys.dataflowRuns(environmentId, dataflowId, dataflowName),
    initialPageParam: 0,
    queryFn: ({ pageParam, signal }) => api.getMonitoringDataflows(environmentId, {
        investigateKind: "dataflow_id",
        investigateValue: dataflowId,
        range: "all",
        log_session_scope: "all",
        limit: LINEAGE_RUN_HISTORY_PAGE_SIZE,
        offset: pageParam,
        sortBy: "start_time",
        sortDir: "desc",
      }, signal),
    getNextPageParam: (lastPage, pages) => {
      const loaded = pages.reduce((total, page) => total + page.records.length, 0);
      const total = lastPage.summary.total_records;
      if (typeof total === "number") return loaded < total ? loaded : undefined;
      return lastPage.records.length === LINEAGE_RUN_HISTORY_PAGE_SIZE ? loaded : undefined;
    },
    staleTime: structuralStaleTime,
  });
}

export function useLineageGraph(environmentId: number, includeDraft = false) {
  return useQuery(lineageGraphOptions(environmentId, includeDraft));
}

export function useLineageLatestStatus(environmentId: number, enabled: boolean) {
  return useQuery({
    ...lineageLatestStatusOptions(environmentId),
    enabled,
  });
}
