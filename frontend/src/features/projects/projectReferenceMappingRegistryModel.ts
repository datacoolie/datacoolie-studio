import type {
  ProjectReferenceMapping,
  ProjectReferenceRegistryEnvironment as ApiRegistryEnvironment,
  ProjectReferenceRegistryResponse,
  ProjectReferenceRegistryRow as ApiRegistryRow,
  ProjectReferenceRegistryTarget as ApiRegistryTarget,
  ReferenceType,
  ResolutionState,
} from "../../shared/api/types";
import { presentReferenceResolution } from "../../shared/referenceResolutionPresentation";
import type { ReferenceMappingTarget } from "../reference-mappings/referenceMappingModel";

export interface ProjectMappingTarget extends ReferenceMappingTarget {
  assetIds: string[];
  environmentIds: number[];
  environmentNames: string[];
}

export interface ProjectReferenceEnvironment {
  environmentId: number;
  environmentName: string;
  resolution: ApiRegistryEnvironment["resolution"];
  resolvedAssetId: string | null;
  resolvedAssetIds: string[];
  observedTargetIds: string[];
  candidateAssetIds: string[];
  manualMappingId: number | null;
  manualMappingStatus: string | null;
  occurrenceCount: number;
  consumerCount: number;
}

export interface ProjectObservedTarget {
  target: ProjectMappingTarget;
  environmentIds: number[];
  environmentNames: string[];
}

export interface ProjectTargetCoverage {
  availableEnvironmentNames: string[];
  missingEnvironmentNames: string[];
  available: number;
  total: number;
}

export type ProjectMappingState = ResolutionState;

export interface ProjectReferenceRegistryRow {
  id: string;
  referenceType: ReferenceType;
  normalizedValue: string;
  mapping: ProjectReferenceMapping | null;
  resolution: ApiRegistryRow["resolution"];
  environments: ProjectReferenceEnvironment[];
  candidateAssetIds: string[];
  resolvedAssetIds: string[];
  target: ProjectMappingTarget | null;
  observedTargets: ProjectObservedTarget[];
  targetCoverage: ProjectTargetCoverage;
  state: ProjectMappingState;
}

export interface ProjectMappingRegistry {
  rows: ProjectReferenceRegistryRow[];
  targets: ProjectMappingTarget[];
}

export function buildProjectMappingRegistry(
  response: ProjectReferenceRegistryResponse | null,
): ProjectMappingRegistry {
  if (!response) return { rows: [], targets: [] };
  const targetsById = new Map(
    response.targets.map((target) => [target.id, projectMappingTarget(target)]),
  );
  return {
    targets: [...targetsById.values()],
    rows: response.rows.map((row) => projectRegistryRow(row, targetsById)),
  };
}

export function filterProjectMappingTargets(
  row: ProjectReferenceRegistryRow,
  targets: ProjectMappingTarget[],
  options: { query: string; connectionName: string },
) {
  const needle = options.query.trim().toLowerCase();
  const candidateAssetIds = new Set(row.candidateAssetIds);
  return targets
    .filter((target) => {
      if (options.connectionName && target.connectionName !== options.connectionName) return false;
      if (!needle) return true;
      return [
        target.displayName,
        target.context,
        target.connectionName,
        target.assetType,
        target.kind,
        target.value,
        target.environmentNames.join(" "),
      ].join(" ").toLowerCase().includes(needle);
    })
    .sort((left, right) => {
      const leftCandidate = left.assetIds.some((id) => candidateAssetIds.has(id)) ? 0 : 1;
      const rightCandidate = right.assetIds.some((id) => candidateAssetIds.has(id)) ? 0 : 1;
      if (leftCandidate !== rightCandidate) return leftCandidate - rightCandidate;
      return left.displayName.localeCompare(right.displayName);
    });
}

export function projectMappingStateLabel(state: ProjectMappingState) {
  return presentReferenceResolution(state).label;
}

export function projectTargetCoverage(
  row: Pick<ProjectReferenceRegistryRow, "environments">,
  target: Pick<ProjectMappingTarget, "environmentIds" | "environmentNames"> | null,
): ProjectTargetCoverage {
  const targetEnvironmentIds = new Set(target?.environmentIds ?? []);
  const availableEnvironmentNames = row.environments
    .filter((environment) => targetEnvironmentIds.has(environment.environmentId))
    .map((environment) => environment.environmentName);
  const missingEnvironmentNames = row.environments
    .filter((environment) => !targetEnvironmentIds.has(environment.environmentId))
    .map((environment) => environment.environmentName);
  return {
    availableEnvironmentNames,
    missingEnvironmentNames,
    available: availableEnvironmentNames.length,
    total: row.environments.length,
  };
}

export function canCreateProjectMapping(row: Pick<ProjectReferenceRegistryRow, "mapping">) {
  return !row.mapping;
}

export function canEditProjectMapping(row: Pick<ProjectReferenceRegistryRow, "mapping">) {
  return Boolean(row.mapping);
}

export function projectMappingTargetBusinessKey(row: Pick<ProjectReferenceRegistryRow, "mapping">) {
  if (!row.mapping) return "No saved mapping";
  return `${row.mapping.target_identifier_kind} · ${row.mapping.target_normalized_value}`;
}

export function projectMappingResolutionSummary(
  row: Pick<ProjectReferenceRegistryRow, "environments" | "resolution">,
) {
  if (!row.environments.length) {
    return presentReferenceResolution(row.resolution).detail || "No current observations";
  }
  const counts = new Map<ResolutionState, number>();
  for (const environment of row.environments) {
    const state = environment.resolution.state;
    counts.set(state, (counts.get(state) ?? 0) + 1);
  }
  if (counts.size === 1) {
    const [state, count] = [...counts.entries()][0];
    const environments = `${count} environment${count === 1 ? "" : "s"}`;
    if (state === "manual") return `Applied in ${environments}`;
    if (state === "automatic") return `${environments} resolved automatically`;
    return `${environments} unresolved`;
  }
  return [...counts.entries()]
    .map(([state, count]) => `${presentReferenceResolution(state).label} ${count}`)
    .join(" · ");
}

export function projectMappingTargetLabel(
  row: Pick<ProjectReferenceRegistryRow, "mapping" | "target" | "observedTargets">,
) {
  if (row.target) return row.target.displayName;
  if (!row.mapping && row.observedTargets.length === 1) return row.observedTargets[0].target.displayName;
  if (!row.mapping && row.observedTargets.length > 1) return `Multiple automatic targets · ${row.observedTargets.length}`;
  if (!row.mapping) return "No observed target";
  return row.mapping.target_display_value || row.mapping.target_normalized_value;
}

export function projectMappingInitialTargetId(
  row: Pick<ProjectReferenceRegistryRow, "target" | "observedTargets">,
) {
  if (row.target) return row.target.id;
  return row.observedTargets.length === 1 ? row.observedTargets[0].target.id : null;
}

export function projectEnvironmentResolutionLabel(environment: ProjectReferenceEnvironment) {
  return presentReferenceResolution(environment.resolution).label;
}

function projectRegistryRow(
  row: ApiRegistryRow,
  targetsById: Map<string, ProjectMappingTarget>,
): ProjectReferenceRegistryRow {
  return {
    id: row.id,
    referenceType: row.reference_type,
    normalizedValue: row.normalized_value,
    mapping: row.mapping ?? null,
    resolution: row.resolution,
    environments: row.environments.map(projectEnvironment),
    candidateAssetIds: [...row.candidate_asset_ids],
    resolvedAssetIds: [...row.resolved_asset_ids],
    target: row.target ? targetsById.get(row.target.id) ?? projectMappingTarget(row.target) : null,
    observedTargets: row.observed_targets.map((item) => ({
      target: targetsById.get(item.target.id) ?? projectMappingTarget(item.target),
      environmentIds: [...item.environment_ids],
      environmentNames: [...item.environment_names],
    })),
    targetCoverage: {
      availableEnvironmentNames: [...row.target_coverage.available_environment_names],
      missingEnvironmentNames: [...row.target_coverage.missing_environment_names],
      available: row.target_coverage.available,
      total: row.target_coverage.total,
    },
    state: row.resolution.state,
  };
}

function projectEnvironment(environment: ApiRegistryEnvironment): ProjectReferenceEnvironment {
  return {
    environmentId: environment.environment_id,
    environmentName: environment.environment_name,
    resolution: environment.resolution,
    resolvedAssetId: environment.resolved_asset_id ?? null,
    resolvedAssetIds: [...environment.resolved_asset_ids],
    observedTargetIds: [...environment.observed_target_ids],
    candidateAssetIds: [...environment.candidate_asset_ids],
    manualMappingId: environment.manual_mapping_id ?? null,
    manualMappingStatus: environment.manual_mapping_status ?? null,
    occurrenceCount: environment.occurrence_count,
    consumerCount: environment.consumer_count,
  };
}

function projectMappingTarget(target: ApiRegistryTarget): ProjectMappingTarget {
  return {
    id: target.id,
    assetId: target.asset_id,
    assetIds: [...target.asset_ids],
    environmentIds: [...target.environment_ids],
    environmentNames: [...target.environment_names],
    assetType: target.asset_type,
    format: target.format ?? null,
    connectionName: target.connection_name,
    displayName: target.display_name,
    context: target.context ?? null,
    kind: target.kind,
    value: target.value,
    display: target.display,
  };
}
