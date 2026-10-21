import { useQuery } from "@tanstack/react-query";
import {
  fetchEnvironmentLogSourcesSummary,
  fetchEnvironmentSources,
} from "./environmentSourcesResource";
import { environmentQueryKeys } from "../environments/environmentQueries";
import { sourceSyncStatusPollInterval } from "./sourceWorkspaceModel";

type EnvironmentSourcesQueryOptions = {
  environmentId: number | null;
  enabled: boolean;
};

export function useEnvironmentSourcesQuery({
  environmentId,
  enabled,
}: EnvironmentSourcesQueryOptions) {
  return useQuery({
    queryKey: environmentId
      ? environmentQueryKeys.sources(environmentId)
      : ["environments", "no-sources"],
    queryFn: ({ signal }) => fetchEnvironmentSources(environmentId!, signal),
    enabled: environmentId !== null && enabled,
    staleTime: 0,
    refetchInterval: (current) =>
      sourceSyncStatusPollInterval(current.state.data?.statuses ?? {}),
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: "always",
  });
}

type EnvironmentLogSourcesSummaryQueryOptions = {
  environmentId: number | null;
  enabled: boolean;
};

export function useDiagnosticsLogSourcesQuery({
  environmentId,
  enabled,
}: EnvironmentLogSourcesSummaryQueryOptions) {
  return useQuery({
    queryKey: environmentId
      ? [...environmentQueryKeys.sources(environmentId), "logs-summary"]
      : ["environments", "no-log-sources-summary"],
    queryFn: ({ signal }) => fetchEnvironmentLogSourcesSummary(environmentId!, signal),
    enabled: environmentId !== null && enabled,
    staleTime: 0,
    refetchInterval: (current) =>
      sourceSyncStatusPollInterval(current.state.data?.statuses ?? {}),
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: "always",
  });
}
