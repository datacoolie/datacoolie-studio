import { api } from "../shared/api/client";
import type { SourcePath } from "../shared/api/domainTypes";

/** Source lists are owned by the Sources module, not the shared Environment header. */
export interface EnvironmentSourcesData {
  metadataSources: SourcePath[];
  logPaths: SourcePath[];
  codeArtifacts: SourcePath[];
}

export async function fetchEnvironmentSources(environmentId: number): Promise<EnvironmentSourcesData> {
  const [metadataSources, logPaths, codeArtifacts] = await Promise.all([
    api.listMetadataSources(environmentId),
    api.listLogSources(environmentId),
    api.listCodeArtifacts(environmentId),
  ]);
  return { metadataSources, logPaths, codeArtifacts };
}
