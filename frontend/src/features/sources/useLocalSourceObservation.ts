import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { fetchEnvironmentSources } from "../../app/environmentSourcesResource";
import { api } from "../../shared/api/client";
import { toErrorMessage } from "../../shared/lib/errors";
import { sourceKey, type SourceKind } from "../../shared/lib/sources";
import { environmentQueryKeys } from "../environments/environmentQueries";
import { shouldStartLocalObservation } from "./sourceWorkspaceModel";

type LocalSourceObservationOptions = {
  environmentId: number | null;
  activationKey: string;
  isActive: (environmentId: number) => boolean;
  onEnvironmentChanged?: (environmentId: number) => Promise<void> | void;
  onError: (message: string) => void;
};

export function useLocalSourceObservation({
  environmentId,
  activationKey,
  isActive,
  onEnvironmentChanged,
  onError,
}: LocalSourceObservationOptions) {
  const queryClient = useQueryClient();
  const isActiveRef = useRef(isActive);
  const onEnvironmentChangedRef = useRef(onEnvironmentChanged);
  const onErrorRef = useRef(onError);
  const observationState = useRef(new Map<
    number,
    { inFlight: Promise<void> | null; lastStartedAt: number }
  >());
  isActiveRef.current = isActive;
  onEnvironmentChangedRef.current = onEnvironmentChanged;
  onErrorRef.current = onError;

  useEffect(() => {
    if (environmentId === null) return;

    const observe = () => {
      const now = Date.now();
      const current = observationState.current.get(environmentId);
      if (!shouldStartLocalObservation(
        document.visibilityState,
        current
          ? {
            inFlight: current.inFlight !== null,
            lastStartedAt: current.lastStartedAt,
          }
          : undefined,
        now,
      )) return;

      const entry = {
        inFlight: null as Promise<void> | null,
        lastStartedAt: now,
      };
      let request: Promise<void>;
      request = api.observeLocalSources(environmentId)
        .then(async (result) => {
          queryClient.setQueryData(
            environmentQueryKeys.sources(environmentId),
            (
              cached: Awaited<ReturnType<typeof fetchEnvironmentSources>> | undefined,
            ) => {
              if (!cached) return cached;
              const statuses = { ...cached.statuses };
              for (const outcome of result.outcomes) {
                statuses[
                  sourceKey(
                    outcome.source_kind as SourceKind,
                    outcome.source_id,
                  )
                ] = outcome.status;
              }
              return { ...cached, statuses };
            },
          );
          if (result.changed > 0) {
            await onEnvironmentChangedRef.current?.(environmentId);
          }
        })
        .catch((error) => {
          if (isActiveRef.current(environmentId)) {
            onErrorRef.current(toErrorMessage(error));
          }
        })
        .finally(() => {
          const latest = observationState.current.get(environmentId);
          if (latest?.inFlight === request) latest.inFlight = null;
        });
      entry.inFlight = request;
      observationState.current.set(environmentId, entry);
    };

    observe();
    const observeWhenVisible = () => {
      if (document.visibilityState === "visible") observe();
    };
    document.addEventListener("visibilitychange", observeWhenVisible);
    window.addEventListener("focus", observeWhenVisible);
    return () => {
      document.removeEventListener("visibilitychange", observeWhenVisible);
      window.removeEventListener("focus", observeWhenVisible);
    };
  }, [activationKey, environmentId, queryClient]);
}
