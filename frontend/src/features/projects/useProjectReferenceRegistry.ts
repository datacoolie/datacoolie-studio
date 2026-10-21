import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { ProjectReferenceRegistryResponse } from "../../shared/api/domainTypes";
import { toErrorMessage } from "../../shared/lib/errors";
import { invalidateProjectRegistry, projectRegistryOptions } from "./projectQueries";

export function mergeProjectReferenceRegistry(
  _previous: ProjectReferenceRegistryResponse | null,
  response: ProjectReferenceRegistryResponse,
): ProjectReferenceRegistryResponse {
  return response;
}

export function useProjectReferenceRegistry(projectId: number | null, enabled: boolean) {
  const queryClient = useQueryClient();
  const query = useQuery({ ...projectRegistryOptions(projectId ?? 0), enabled: enabled && projectId !== null });
  async function reload() {
    if (projectId === null) return;
    await invalidateProjectRegistry(queryClient, projectId);
    await queryClient.fetchQuery(projectRegistryOptions(projectId));
  }
  return { data: query.data ?? null, loading: query.isFetching, loaded: Boolean(query.data), error: query.error ? toErrorMessage(query.error) : null, reload };
}
