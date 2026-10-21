import { queryOptions, useQuery, type QueryClient } from "@tanstack/react-query";
import { api } from "../../shared/api/client";
import { shouldRetryQueryError } from "../../shared/lib/errors";

export const projectQueryKeys = {
  all: ["projects"] as const,
  summaries: () => [...projectQueryKeys.all, "summaries"] as const,
  registry: (projectId: number) => [...projectQueryKeys.all, projectId, "reference-registry"] as const,
};

export function projectRegistryOptions(projectId: number) {
  return queryOptions({ queryKey: projectQueryKeys.registry(projectId), queryFn: ({ signal }) => api.getProjectReferenceRegistry(projectId, signal), staleTime: 60_000, retry: shouldRetryQueryError });
}

export async function invalidateProjectRegistry(queryClient: QueryClient, projectId: number) {
  const queryKey = projectQueryKeys.registry(projectId);
  await queryClient.cancelQueries({ queryKey, exact: true });
  await queryClient.invalidateQueries({ queryKey, exact: true });
}

export function projectSummariesOptions() {
  return queryOptions({
    queryKey: projectQueryKeys.summaries(),
    queryFn: api.listProjectSummaries,
    staleTime: Number.POSITIVE_INFINITY,
    refetchOnWindowFocus: false,
    retry: shouldRetryQueryError,
  });
}

export function useProjectSummariesQuery(enabled = true) {
  return useQuery({
    ...projectSummariesOptions(),
    enabled,
  });
}
