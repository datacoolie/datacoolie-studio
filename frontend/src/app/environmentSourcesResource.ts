import { api } from "../shared/api/client";
import type { SourcePath } from "../shared/api/domainTypes";
import type { SourceSyncStatus } from "../shared/api/contractTypes";

/** Source lists are owned by the Sources module, not the shared Environment header. */
export interface EnvironmentSourcesData {
  metadataSources: SourcePath[];
  logPaths: SourcePath[];
  codeArtifacts: SourcePath[];
  statuses: Record<string, SourceSyncStatus>;
}

export async function fetchEnvironmentSources(environmentId: number): Promise<EnvironmentSourcesData> {
  const workspace = await api.getSourcesWorkspace(environmentId);
  return {
    metadataSources: workspace.metadata_sources,
    logPaths: workspace.log_sources,
    codeArtifacts: workspace.code_artifacts,
    statuses: Object.fromEntries(
      workspace.statuses.map((status) => [
        `${status.source_kind}:${status.source_id}`,
        status,
      ]),
    ),
  };
}
