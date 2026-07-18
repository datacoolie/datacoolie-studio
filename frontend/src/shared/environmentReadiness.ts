import type { ProjectEnvironmentSummary } from "./api/types";

export type EnvironmentReadiness = "ready" | "needs-metadata";

export function environmentReadiness(environment: ProjectEnvironmentSummary): EnvironmentReadiness {
  return environment.metadata_source_count > 0 ? "ready" : "needs-metadata";
}

export function environmentReadinessLabel(readiness: EnvironmentReadiness) {
  return readiness === "ready" ? "Ready" : "Needs metadata";
}

export function environmentReadinessReason(readiness: EnvironmentReadiness) {
  return readiness === "needs-metadata" ? "No metadata source" : null;
}

export function projectReadinessSummary(environments: ProjectEnvironmentSummary[]) {
  const ready = environments.filter((environment) => environmentReadiness(environment) === "ready").length;
  return {
    total: environments.length,
    ready,
    needsMetadata: environments.length - ready,
  };
}
