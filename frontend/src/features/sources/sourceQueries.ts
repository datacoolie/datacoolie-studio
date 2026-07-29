import { useQuery } from "@tanstack/react-query";
import { fetchEnvironmentSources } from "../../app/environmentSourcesResource";
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
    queryFn: () => fetchEnvironmentSources(environmentId!),
    enabled: environmentId !== null && enabled,
    staleTime: 0,
    refetchInterval: (current) =>
      sourceSyncStatusPollInterval(current.state.data?.statuses ?? {}),
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: "always",
  });
}
