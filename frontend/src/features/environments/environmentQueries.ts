import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../shared/api/client";
import type { EnvironmentContext, MetadataEditorWorkspace } from "../../shared/api/domainTypes";

export const environmentQueryKeys = {
  all: ["environments"] as const,
  context: (environmentId: number) => [...environmentQueryKeys.all, environmentId, "context"] as const,
  overview: (environmentId: number) => [...environmentQueryKeys.all, environmentId, "overview"] as const,
  sources: (environmentId: number) => [...environmentQueryKeys.all, environmentId, "sources"] as const,
  metadata: (environmentId: number) => [...environmentQueryKeys.all, environmentId, "metadata"] as const,
  metadataWorkspace: (environmentId: number) => [...environmentQueryKeys.metadata(environmentId), "workspace"] as const,
  metadataBackups: (environmentId: number) => [...environmentQueryKeys.metadata(environmentId), "backups"] as const,
  assets: (environmentId: number) => [...environmentQueryKeys.all, environmentId, "assets"] as const,
  lineage: (environmentId: number) => [...environmentQueryKeys.all, environmentId, "lineage"] as const,
  monitoring: (environmentId: number) => [...environmentQueryKeys.all, environmentId, "monitoring"] as const,
  monitoringReport: (environmentId: number, page: string, params: Record<string, unknown>) =>
    [...environmentQueryKeys.monitoring(environmentId), "report", page, params] as const,
  monitoringFilterOptions: (environmentId: number) =>
    [...environmentQueryKeys.monitoring(environmentId), "filter-options"] as const,
  metadataBackupDocument: (backupId: number) => ["metadata-backups", backupId, "document"] as const,
};

export type EnvironmentInvalidationTarget = "sources" | "metadata" | "assets" | "lineage" | "monitoring" | "overview";

export function metadataWorkspaceSatisfiesCatalogVersion(
  workspace: MetadataEditorWorkspace | undefined,
  metadataCatalogVersion: string,
) {
  return workspace?.metadata_catalog_version === metadataCatalogVersion;
}

export function environmentInvalidationTargets(
  before: EnvironmentContext["versions"],
  current: EnvironmentContext["versions"],
): EnvironmentInvalidationTarget[] {
  const targets = new Set<EnvironmentInvalidationTarget>();
  const changed = (name: keyof EnvironmentContext["versions"]) => current[name] !== before[name];
  if (changed("source_registry")) targets.add("sources");
  if (changed("metadata_catalog")) targets.add("metadata");
  if (changed("metadata_catalog") || changed("code_catalog") || changed("reference_mappings")) {
    targets.add("assets");
    targets.add("lineage");
  }
  if (changed("operations")) targets.add("monitoring");
  if (Object.keys(current).some((name) => changed(name as keyof EnvironmentContext["versions"]))) {
    targets.add("overview");
  }
  return [...targets];
}

export function useEnvironmentContextQuery(
  environmentId: number | null,
  staleTimeMs: number,
  refetchSignal?: unknown,
) {
  const queryClient = useQueryClient();
  const previous = useRef<EnvironmentContext | null>(null);
  const query = useQuery({
    queryKey: environmentId ? environmentQueryKeys.context(environmentId) : ["environments", "no-context"],
    queryFn: () => api.getEnvironmentContext(environmentId!),
    enabled: environmentId !== null,
    staleTime: staleTimeMs,
  });

  // Refresh header freshness when the user switches tabs. The backend caches the
  // expensive log-folder check for the source-check interval (TTL), so fetching on
  // navigation is cheap and there is no idle background polling.
  useEffect(() => {
    if (environmentId === null) return;
    if (query.data) void query.refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refetchSignal]);

  useEffect(() => {
    const current = query.data;
    const before = previous.current;
    previous.current = current ?? null;
    if (!current || !before || current.environment.id !== before.environment.id) return;

    const id = current.environment.id;
    for (const target of environmentInvalidationTargets(before.versions, current.versions)) {
      if (target === "metadata") {
        const workspace = queryClient.getQueryData<MetadataEditorWorkspace>(
          environmentQueryKeys.metadataWorkspace(id),
        );
        if (metadataWorkspaceSatisfiesCatalogVersion(workspace, current.versions.metadata_catalog)) continue;
        void queryClient.invalidateQueries({
          queryKey: environmentQueryKeys.metadataWorkspace(id),
          exact: true,
        });
        continue;
      }
      void queryClient.invalidateQueries({ queryKey: environmentQueryKeys[target](id) });
    }
  }, [query.data, queryClient]);

  return query;
}

export function useEnvironmentOverviewQuery(environmentId: number) {
  return useQuery({
    queryKey: environmentQueryKeys.overview(environmentId),
    queryFn: () => api.getEnvironmentOverview(environmentId),
    staleTime: Number.POSITIVE_INFINITY,
  });
}
