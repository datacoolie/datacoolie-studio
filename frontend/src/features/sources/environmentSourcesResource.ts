import { api } from "../../shared/api/client";
import type { SourcePath } from "../../shared/api/domainTypes";
import type { SourceSyncStatus } from "../../shared/api/contractTypes";

/** Source lists are owned by the Sources module, not the shared Environment header. */
export interface EnvironmentSourcesData {
  metadataSources: SourcePath[];
  sqlSources: SourcePath[];
  logBasePaths: SourcePath[];
  codeArtifacts: SourcePath[];
  statuses: Record<string, SourceSyncStatus>;
}

export async function fetchEnvironmentSources(environmentId: number, signal?: AbortSignal): Promise<EnvironmentSourcesData> {
  const workspace = await api.getSourcesWorkspace(environmentId, signal);
  return {
    metadataSources: workspace.metadata_sources,
    sqlSources: workspace.sql_sources,
    logBasePaths: workspace.log_sources,
    codeArtifacts: workspace.code_artifacts,
    statuses: Object.fromEntries(
      workspace.statuses.map((status) => [
        `${status.source_kind}:${status.source_id}`,
        status,
      ]),
    ),
  };
}

/**
 * Load the compact, log-only source projection used by monitoring pages.
 * It intentionally returns the canonical EnvironmentSourcesData shape while
 * selecting only the log-source view at the transport boundary.
 */
export async function fetchEnvironmentLogSourcesSummary(
  environmentId: number,
  signal?: AbortSignal,
): Promise<EnvironmentSourcesData> {
  const summary = await api.getSourcesWorkspace(environmentId, signal, "logs-summary");
  return {
    metadataSources: [],
    sqlSources: [],
    logBasePaths: summary.log_sources,
    codeArtifacts: [],
    statuses: Object.fromEntries(
      summary.statuses.map((status) => [
        `${status.source_kind}:${status.source_id}`,
        status,
      ]),
    ),
  };
}
