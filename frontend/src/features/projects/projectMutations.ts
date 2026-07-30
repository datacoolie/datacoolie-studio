import type { Dispatch, SetStateAction } from "react";

import { projectDefaultSection } from "../../app/moduleRegistry";
import type { StudioRouter } from "../../app/useStudioRouter";
import {
  addProjectSummary,
  changeProjectReferenceMappingCount,
  renameProjectSummary,
} from "../../app/projectSummaryMutations";
import { api } from "../../shared/api/client";
import type { Environment, Project, ProjectSummary, ReferenceType, TargetIdentifierKind } from "../../shared/api/domainTypes";
import { toErrorMessage } from "../../shared/lib/errors";

type ProjectMutationContext = {
  projectId: number | null;
  environmentId: number | null;
  setStudioRoute: StudioRouter["setStudioRoute"];
  setProjects: Dispatch<SetStateAction<Project[]>>;
  setEnvironments: Dispatch<SetStateAction<Environment[]>>;
  setBusy: Dispatch<SetStateAction<boolean>>;
  setError: Dispatch<SetStateAction<string | null>>;
  updateProjectSummaries: (updater: (items: ProjectSummary[]) => ProjectSummary[]) => void;
  clearEnvironmentSources: () => void;
  onEnvironmentChanged?: (environmentId: number) => Promise<void> | void;
};

export function createProjectMutations(context: ProjectMutationContext) {
  const {
    projectId,
    environmentId,
    setStudioRoute,
    setProjects,
    setEnvironments,
    setBusy,
    setError,
    updateProjectSummaries,
    clearEnvironmentSources,
    onEnvironmentChanged,
  } = context;
  const route = { projectId, environmentId };
  const options = { onEnvironmentChanged };

  async function createProject(name: string) {
    setBusy(true);
    setError(null);
    try {
      const project = await api.createProject({ name });
      setProjects((current) => [...current, project].sort((a, b) => a.name.localeCompare(b.name)));
      updateProjectSummaries((current) => addProjectSummary(current, project));
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
      if (route.projectId === projectId) {
        setStudioRoute({ projectId: null, environmentId: null, module: "projects" });
      }
      setProjects((current) => current.filter((project) => project.id !== projectId));
      updateProjectSummaries((current) => current.filter((project) => project.id !== projectId));
      setEnvironments([]);
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
      const project = await api.renameProject(projectId, { name });
      setProjects((current) => current
        .map((item) => item.id === project.id ? project : item)
        .sort((left, right) => left.name.localeCompare(right.name)));
      updateProjectSummaries((current) => renameProjectSummary(current, project));
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
      if (route.environmentId) await options?.onEnvironmentChanged?.(route.environmentId);
      updateProjectSummaries((current) => changeProjectReferenceMappingCount(current, projectId, 1));
      return mapping;
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusy(false);
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
      if (route.environmentId) await options?.onEnvironmentChanged?.(route.environmentId);
      return mapping;
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusy(false);
    }
  }

  async function deleteProjectReferenceMapping(mappingId: number) {
    if (!route.projectId) throw new Error("Select a project before removing a mapping.");
    const projectId = route.projectId;
    setBusy(true);
    setError(null);
    try {
      await api.deleteProjectReferenceMapping(projectId, mappingId);
      if (route.environmentId) await options?.onEnvironmentChanged?.(route.environmentId);
      updateProjectSummaries((current) => changeProjectReferenceMappingCount(current, projectId, -1));
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusy(false);
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
