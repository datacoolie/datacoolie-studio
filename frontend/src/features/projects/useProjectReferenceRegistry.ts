import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../shared/api/client";
import type { ProjectReferenceRegistryResponse } from "../../shared/api/types";
import { ResourceCache } from "../../shared/data/resourceCache";

const REGISTRY_TTL_MS = 60_000;

interface RegistryState {
  projectId: number | null;
  data: ProjectReferenceRegistryResponse | null;
  loading: boolean;
  error: string | null;
}

export function mergeProjectReferenceRegistry(
  previous: ProjectReferenceRegistryResponse | null,
  response: ProjectReferenceRegistryResponse,
): ProjectReferenceRegistryResponse {
  if (previous?.project_id !== response.project_id) return response;
  return response;
}

export function useProjectReferenceRegistry(projectId: number | null, enabled: boolean) {
  const cache = useRef(new ResourceCache<number, ProjectReferenceRegistryResponse>(Date.now, { maxEntries: 4 }));
  const dataByProject = useRef(new Map<number, ProjectReferenceRegistryResponse>());
  const activeProjectId = useRef(projectId);
  const [state, setState] = useState<RegistryState>({
    projectId: null,
    data: null,
    loading: false,
    error: null,
  });
  activeProjectId.current = projectId;

  const load = useCallback(async (requestedProjectId: number, force = false) => {
    setState((current) => ({
      projectId: requestedProjectId,
      data: dataByProject.current.get(requestedProjectId)
        ?? (current.projectId === requestedProjectId ? current.data : null),
      loading: true,
      error: null,
    }));
    try {
      const result = await cache.current.load(
        requestedProjectId,
        () => api.getProjectReferenceRegistry(requestedProjectId),
        { ttlMs: REGISTRY_TTL_MS, force },
      );
      if (!result.current || activeProjectId.current !== requestedProjectId) return;
      const merged = mergeProjectReferenceRegistry(
        dataByProject.current.get(requestedProjectId) ?? null,
        result.data,
      );
      dataByProject.current.set(requestedProjectId, merged);
      setState((current) => ({
        projectId: requestedProjectId,
        data: merged,
        loading: false,
        error: null,
      }));
    } catch (cause) {
      if (activeProjectId.current === requestedProjectId) {
        setState((current) => ({
          projectId: requestedProjectId,
          data: dataByProject.current.get(requestedProjectId)
            ?? (current.projectId === requestedProjectId ? current.data : null),
          loading: false,
          error: cause instanceof Error ? cause.message : "The project reference registry could not be loaded.",
        }));
      }
      throw cause;
    }
  }, []);

  useEffect(() => {
    if (!enabled || !projectId) return;
    void load(projectId).catch(() => undefined);
  }, [enabled, load, projectId]);

  const reload = useCallback(async () => {
    if (!projectId) return;
    cache.current.invalidate(projectId);
    await load(projectId, true);
  }, [load, projectId]);

  const data = state.projectId === projectId ? state.data : null;
  const error = state.projectId === projectId ? state.error : null;
  const loading = state.projectId === projectId
    ? state.loading
    : Boolean(enabled && projectId);

  return {
    data,
    loading,
    loaded: Boolean(data),
    error,
    reload,
  };
}
