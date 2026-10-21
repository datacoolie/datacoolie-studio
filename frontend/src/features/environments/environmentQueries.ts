import { useEffect } from "react";
import { useQuery, type QueryClient } from "@tanstack/react-query";
import { api } from "../../shared/api/client";
import type { EnvironmentContext, MetadataEditorWorkspace } from "../../shared/api/domainTypes";
import { shouldRetryQueryError } from "../../shared/lib/errors";

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
  monitoringFilterOptions: (environmentId: number, params: Record<string, unknown>) =>
    [...environmentQueryKeys.monitoring(environmentId), "filter-options", params] as const,
  metadataBackupDocument: (environmentId: number, backupId: number) => ["metadata", environmentId, "backups", backupId, "document"] as const,
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
  // Monitoring destination identity can fall back to the published metadata
  // catalog.  A metadata save/refresh/delete/enable change therefore expires
  // registry tables and in-flight drawer/history reads through the same
  // environment version fence as the metadata and Assets consumers.
  if (changed("metadata_catalog")) targets.add("monitoring");
  if (changed("operations")) targets.add("monitoring");
  if (Object.keys(current).some((name) => changed(name as keyof EnvironmentContext["versions"]))) {
    targets.add("overview");
  }
  return [...targets];
}

/** App lifetime subscription observes Context results even off the active route. */
export function installEnvironmentVersionReconciliation(queryClient: QueryClient) {
  const ledger = new Map<number, EnvironmentContext["versions"]>();
  function reconcile(current: EnvironmentContext) {
    const id = current.environment.id;
    const before = ledger.get(id);
    ledger.set(id, current.versions);
    const targets = before
      ? environmentInvalidationTargets(before, current.versions)
      : ["sources", "metadata", "assets", "lineage", "monitoring", "overview"] as EnvironmentInvalidationTarget[];
    for (const target of targets) {
      const queryKey = target === "metadata" ? environmentQueryKeys.metadataWorkspace(id) : environmentQueryKeys[target](id);
      if (target === "metadata" && metadataWorkspaceSatisfiesCatalogVersion(queryClient.getQueryData(queryKey), current.versions.metadata_catalog)) continue;
      if (!before) {
        // The first Context result establishes the version fence for this
        // environment. Active Monitoring reads may already be using that same
        // environment, so mark them stale without cancelling/refetching them.
        // A later version change still uses the cancel-then-invalidate path.
        void queryClient.invalidateQueries({
          queryKey,
          exact: target === "metadata",
          refetchType: "none",
        });
        continue;
      }
      // Inactive/disabled consumers remain stale until they are demanded again.
      // Invalidation alone can join a cold in-flight promise. Cancel it first
      // so a response from the preceding catalog cannot become fresh again.
      void queryClient.cancelQueries({ queryKey, exact: target === "metadata" }).then(() =>
        queryClient.invalidateQueries({ queryKey, exact: target === "metadata" }),
      );
    }
  }
  const cache = queryClient.getQueryCache();
  const unsubscribe = cache.subscribe(event => {
    const key = event.query.queryKey;
    if (key.length !== 3 || key[0] !== "environments" || key[2] !== "context" || typeof key[1] !== "number") return;
    if (event.type === "removed") ledger.delete(key[1]);
    if (event.type === "updated" && event.action.type === "success" && event.query.state.data) reconcile(event.query.state.data as EnvironmentContext);
  });
  for (const query of cache.findAll({ queryKey: environmentQueryKeys.all })) {
    if (query.queryKey.length === 3 && query.queryKey[2] === "context" && query.state.data) reconcile(query.state.data as EnvironmentContext);
  }
  return () => { unsubscribe(); ledger.clear(); };
}

export function useEnvironmentContextQuery(
  environmentId: number | null,
  staleTimeMs: number,
  refetchSignal?: unknown,
) {
  const query = useQuery({
    queryKey: environmentId ? environmentQueryKeys.context(environmentId) : ["environments", "no-context"],
    queryFn: () => api.getEnvironmentContext(environmentId!),
    enabled: environmentId !== null,
    staleTime: staleTimeMs,
    refetchInterval: staleTimeMs,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: false,
  });

  // Header reads are DB-only. Navigation refreshes immediately and visible idle
  // polling follows the configured fixed/adaptive ceiling without cloud I/O.
  useEffect(() => {
    if (environmentId === null) return;
    if (query.data) void query.refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refetchSignal]);

  return query;
}

export function useEnvironmentOverviewQuery(environmentId: number) {
  return useQuery({
    queryKey: environmentQueryKeys.overview(environmentId),
    queryFn: ({ signal }) => api.getEnvironmentOverview(environmentId, signal),
    staleTime: Number.POSITIVE_INFINITY,
    retry: shouldRetryQueryError,
  });
}
