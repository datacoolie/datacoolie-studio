import { queryOptions, useQuery } from "@tanstack/react-query";
import { api } from "../../shared/api/client";
import { environmentQueryKeys } from "../environments/environmentQueries";

const structuralStaleTime = Number.POSITIVE_INFINITY;

export const lineageQueryKeys = {
  graph: (environmentId: number) => environmentQueryKeys.lineage(environmentId),
  latestStatus: (environmentId: number) => (
    [...environmentQueryKeys.monitoring(environmentId), "latest-status"] as const
  ),
  dataflowRuns: (environmentId: number, dataflowId: string, dataflowName: string) => (
    [...environmentQueryKeys.monitoring(environmentId), "lineage-dataflow-runs", dataflowId, dataflowName] as const
  ),
};

export function lineageGraphOptions(environmentId: number) {
  return queryOptions({
    queryKey: lineageQueryKeys.graph(environmentId),
    queryFn: () => api.getLineage(environmentId),
    staleTime: structuralStaleTime,
  });
}

export function lineageLatestStatusOptions(environmentId: number) {
  return queryOptions({
    queryKey: lineageQueryKeys.latestStatus(environmentId),
    queryFn: () => api.getLatestStatus(environmentId),
    staleTime: structuralStaleTime,
  });
}

export function lineageDataflowRunsOptions(environmentId: number, dataflowId: string, dataflowName: string) {
  return queryOptions({
    queryKey: lineageQueryKeys.dataflowRuns(environmentId, dataflowId, dataflowName),
    queryFn: async () => {
      const response = await api.getMonitoringDataflows(environmentId, {
        search: dataflowId,
        range: "all",
        limit: 25,
        offset: 0,
        sortBy: "start_time",
        sortDir: "desc",
      });
      return response.records.filter((row) => row.dataflow_id === dataflowId || row.dataflow_name === dataflowName);
    },
    staleTime: structuralStaleTime,
  });
}

export function useLineageGraph(environmentId: number) {
  return useQuery(lineageGraphOptions(environmentId));
}

export function useLineageLatestStatus(environmentId: number, enabled: boolean) {
  return useQuery({
    ...lineageLatestStatusOptions(environmentId),
    enabled,
  });
}
