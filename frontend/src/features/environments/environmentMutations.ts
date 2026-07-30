import type { Dispatch, SetStateAction } from "react";
import type { QueryClient } from "@tanstack/react-query";

import {
  addEnvironmentToProject,
  removeEnvironmentFromProject,
  renameEnvironmentInProject,
} from "../../app/projectSummaryMutations";
import { api } from "../../shared/api/client";
import type { Environment, ProjectSummary } from "../../shared/api/domainTypes";
import { toErrorMessage } from "../../shared/lib/errors";

type EnvironmentMutationContext = {
  projectId: number | null;
  queryClient: QueryClient;
  setEnvironments: Dispatch<SetStateAction<Environment[]>>;
  setBusy: Dispatch<SetStateAction<boolean>>;
  setError: Dispatch<SetStateAction<string | null>>;
  updateProjectSummaries: (updater: (items: ProjectSummary[]) => ProjectSummary[]) => void;
};

export function createEnvironmentMutations(context: EnvironmentMutationContext) {
  const { projectId, queryClient, setEnvironments, setBusy, setError, updateProjectSummaries } = context;
  const route = { projectId };

  async function createEnvironment(name: string, projectIdOverride?: number): Promise<number> {
    const pid = projectIdOverride ?? route.projectId;
    if (!pid) return 0;
    setBusy(true);
    setError(null);
    try {
      const environment = await api.createEnvironment(pid, { name });
      updateProjectSummaries((current) => addEnvironmentToProject(current, pid, environment));
      setEnvironments((current) => [...current.filter((item) => item.id !== environment.id), environment]
        .sort((left, right) => left.name.localeCompare(right.name)));
      return environment.id;
    } catch (err) {
      setError(toErrorMessage(err));
      return 0;
    } finally {
      setBusy(false);
    }
  }

  async function deleteEnvironment(environmentId: number) {
    const pid = route.projectId;
    setBusy(true);
    setError(null);
    try {
      await api.deleteEnvironment(environmentId);
      queryClient.removeQueries({ queryKey: ["environments", environmentId] });
      updateProjectSummaries((current) => removeEnvironmentFromProject(current, pid, environmentId));
      setEnvironments((current) => current.filter((environment) => environment.id !== environmentId));
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusy(false);
    }
  }

  async function renameEnvironment(environmentId: number, name: string) {
    setBusy(true);
    setError(null);
    try {
      const environment = await api.renameEnvironment(environmentId, { name });
      setEnvironments((current) => current
        .map((item) => item.id === environment.id ? environment : item)
        .sort((left, right) => left.name.localeCompare(right.name)));
      updateProjectSummaries((current) => renameEnvironmentInProject(current, environment));
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusy(false);
    }
  }

  return { createEnvironment, renameEnvironment, deleteEnvironment };
}
