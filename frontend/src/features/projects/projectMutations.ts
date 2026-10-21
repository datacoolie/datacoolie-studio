import type { QueryClient } from "@tanstack/react-query";
import type { Dispatch, SetStateAction } from "react";

import { projectDefaultSection } from "../../app/moduleRegistry";
import type { StudioRouter } from "../../app/useStudioRouter";
import { invalidateProjectRegistry, projectQueryKeys } from "./projectQueries";
import { api } from "../../shared/api/client";
import type { ReferenceType, TargetIdentifierKind } from "../../shared/api/domainTypes";
import { toErrorMessage } from "../../shared/lib/errors";

type ProjectMutationContext = {
  projectId: number | null;
  environmentId: number | null;
  queryClient: QueryClient;
  setStudioRoute: StudioRouter["setStudioRoute"];
  setBusy: Dispatch<SetStateAction<boolean>>;
  setError: Dispatch<SetStateAction<string | null>>;
  clearEnvironmentSources: () => void;
  onEnvironmentChanged?: (environmentId: number) => Promise<void> | void;
  isActiveProject?: (projectId: number) => boolean;
};

export function createProjectMutations(context: ProjectMutationContext) {
  const {
    projectId,
    environmentId,
    queryClient,
    setStudioRoute,
    setBusy,
    setError,
    clearEnvironmentSources,
    onEnvironmentChanged,
  } = context;
  const route = { projectId, environmentId };
  const options = { onEnvironmentChanged };

  function invalidateProjectSummaries() {
    return queryClient.invalidateQueries({ queryKey: projectQueryKeys.summaries() });
  }

  async function createProject(name: string) {
    setBusy(true);
    setError(null);
    try {
      const project = await api.createProject({ name });
      await invalidateProjectSummaries();
      setStudioRoute({ projectId: project.id, environmentId: null, module: "projects", projectSection: projectDefaultSection });
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusy(false);
    }
  }

  async function deleteProject(projectId: number) {
    setBusy(true);
    setError(null);
    try {
      await api.deleteProject(projectId);
      const summaries = queryClient.getQueryData<Array<{ id: number; environments: Array<{ id: number }> }>>(projectQueryKeys.summaries());
      for (const environment of summaries?.find(project => project.id === projectId)?.environments ?? []) {
        queryClient.removeQueries({ queryKey: ["environments", environment.id] });
      }
      queryClient.removeQueries({ queryKey: ["projects", projectId] });
      if (route.projectId === projectId) {
        setStudioRoute({ projectId: null, environmentId: null, module: "projects" });
      }
      await invalidateProjectSummaries();
      clearEnvironmentSources();
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusy(false);
    }
  }

  async function renameProject(projectId: number, name: string) {
    setBusy(true);
    setError(null);
    try {
      await api.renameProject(projectId, { name });
      await invalidateProjectSummaries();
      if (route.environmentId) await options?.onEnvironmentChanged?.(route.environmentId);
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusy(false);
    }
  }

  async function createProjectReferenceMapping(payload: {
    reference_type: ReferenceType;
    reference_value: string;
    target_identifier_kind: TargetIdentifierKind;
    target_value: string;
    target_display_value?: string | null;
    note?: string | null;
  }) {
    if (!route.projectId) throw new Error("Select a project before creating a mapping.");
    const projectId = route.projectId;
    setBusy(true);
    setError(null);
    try {
      const mapping = await api.createProjectReferenceMapping(projectId, payload);
      await invalidateProjectRegistry(queryClient, projectId);
      if (route.environmentId) await options?.onEnvironmentChanged?.(route.environmentId);
      await invalidateProjectSummaries();
      return mapping;
    } catch (err) {
      if (context.isActiveProject?.(projectId) ?? true) setError(toErrorMessage(err));
      throw err;
    } finally {
      if (context.isActiveProject?.(projectId) ?? true) setBusy(false);
    }
  }

  async function updateProjectReferenceMapping(
    mappingId: number,
    payload: {
      reference_type?: ReferenceType;
      reference_value?: string | null;
      target_identifier_kind?: TargetIdentifierKind;
      target_value?: string | null;
      target_display_value?: string | null;
      note?: string | null;
    }
  ) {
    if (!route.projectId) throw new Error("Select a project before updating a mapping.");
    const projectId = route.projectId;
    setBusy(true);
    setError(null);
    try {
      const mapping = await api.updateProjectReferenceMapping(projectId, mappingId, payload);
      await invalidateProjectRegistry(queryClient, projectId);
      if (route.environmentId) await options?.onEnvironmentChanged?.(route.environmentId);
      return mapping;
    } catch (err) {
      if (context.isActiveProject?.(projectId) ?? true) setError(toErrorMessage(err));
      throw err;
    } finally {
      if (context.isActiveProject?.(projectId) ?? true) setBusy(false);
    }
  }

  async function deleteProjectReferenceMapping(mappingId: number) {
    if (!route.projectId) throw new Error("Select a project before removing a mapping.");
    const projectId = route.projectId;
    setBusy(true);
    setError(null);
    try {
      await api.deleteProjectReferenceMapping(projectId, mappingId);
      await invalidateProjectRegistry(queryClient, projectId);
      if (route.environmentId) await options?.onEnvironmentChanged?.(route.environmentId);
      await invalidateProjectSummaries();
    } catch (err) {
      if (context.isActiveProject?.(projectId) ?? true) setError(toErrorMessage(err));
      throw err;
    } finally {
      if (context.isActiveProject?.(projectId) ?? true) setBusy(false);
    }
  }

  return {
    createProject,
    renameProject,
    deleteProject,
    createProjectReferenceMapping,
    updateProjectReferenceMapping,
    deleteProjectReferenceMapping,
  };
}
