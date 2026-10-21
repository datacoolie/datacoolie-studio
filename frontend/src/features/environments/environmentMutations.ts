import type { QueryClient } from "@tanstack/react-query";
import type { Dispatch, SetStateAction } from "react";

import { projectQueryKeys } from "../projects/projectQueries";
import { api } from "../../shared/api/client";
import { toErrorMessage } from "../../shared/lib/errors";

type EnvironmentMutationContext = {
  projectId: number | null;
  queryClient: QueryClient;
  setBusy: Dispatch<SetStateAction<boolean>>;
  setError: Dispatch<SetStateAction<string | null>>;
};

export function createEnvironmentMutations(context: EnvironmentMutationContext) {
  const { projectId, queryClient, setBusy, setError } = context;
  const route = { projectId };

  async function createEnvironment(name: string, projectIdOverride?: number): Promise<number> {
    const pid = projectIdOverride ?? route.projectId;
    if (!pid) return 0;
    setBusy(true);
    setError(null);
    try {
      const environment = await api.createEnvironment(pid, { name });
      await queryClient.invalidateQueries({ queryKey: projectQueryKeys.summaries() });
      return environment.id;
    } catch (err) {
      setError(toErrorMessage(err));
      return 0;
    } finally {
      setBusy(false);
    }
  }

  async function deleteEnvironment(environmentId: number) {
    setBusy(true);
    setError(null);
    try {
      await api.deleteEnvironment(environmentId);
      queryClient.removeQueries({ queryKey: ["environments", environmentId] });
      await queryClient.invalidateQueries({ queryKey: projectQueryKeys.summaries() });
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
      await api.renameEnvironment(environmentId, { name });
      await queryClient.invalidateQueries({ queryKey: projectQueryKeys.summaries() });
    } catch (err) {
      setError(toErrorMessage(err));
      throw err;
    } finally {
      setBusy(false);
    }
  }

  return { createEnvironment, renameEnvironment, deleteEnvironment };
}
