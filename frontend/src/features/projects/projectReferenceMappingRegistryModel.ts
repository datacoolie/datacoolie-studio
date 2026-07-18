import type {
  AssetInventoryItem,
  AssetReferenceGroupItem,
  Environment,
  ProjectReferenceMapping,
  ReferenceGroupStatus,
  ReferenceType,
} from "../../shared/api/types";
import { presentReferenceResolution, type ReferenceResolutionState } from "../../shared/referenceResolutionPresentation";
import {
  buildReferenceMappingTargets,
  referenceMappingTargetIdentifier,
  referenceMappingTargetKey,
  type ReferenceMappingTarget,
} from "../reference-mappings/referenceMappingModel";

export interface ProjectAssetsSnapshot {
  environment: Pick<Environment, "id" | "name">;
  assets: AssetInventoryItem[];
  referenceGroups: AssetReferenceGroupItem[];
}

export interface ProjectMappingTarget extends ReferenceMappingTarget {
  assetIds: string[];
  environmentIds: number[];
  environmentNames: string[];
}

export interface ProjectReferenceEnvironment {
  environmentId: number;
  environmentName: string;
  groupStatus: ReferenceGroupStatus;
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

export type ProjectMappingState =
  | "needs_mapping"
  | "manual"
  | "partial"
  | "missing_target"
  | "inactive"
  | "stored_only"
  | "automatic"
  | "review";

export type ProjectMappingAction = "map" | "resolve" | "complete" | "repair" | "edit" | "review" | null;

export interface ProjectReferenceRegistryRow {
  id: string;
  referenceType: ReferenceType;
  normalizedValue: string;
  mapping: ProjectReferenceMapping | null;
  environments: ProjectReferenceEnvironment[];
  candidateAssetIds: string[];
  resolvedAssetIds: string[];
  target: ProjectMappingTarget | null;
  observedTargets: ProjectObservedTarget[];
  targetCoverage: ProjectTargetCoverage;
  state: ProjectMappingState;
  action: ProjectMappingAction;
}

export interface ProjectMappingRegistry {
  rows: ProjectReferenceRegistryRow[];
  targets: ProjectMappingTarget[];
}

export function buildProjectMappingRegistry(
  snapshots: ProjectAssetsSnapshot[],
  mappings: ProjectReferenceMapping[],
  projectId?: number,
): ProjectMappingRegistry {
  const scopedMappings = projectId === undefined
    ? mappings
    : mappings.filter((mapping) => mapping.project_id === projectId);
  const targets = buildProjectMappingTargets(snapshots);
  const targetsByKey = new Map(targets.map((target) => [target.id, target]));
  const targetIdsByEnvironmentAsset = buildTargetIdsByEnvironmentAsset(snapshots);
  const mappingsByReference = new Map(
    scopedMappings.map((mapping) => [referenceKey(mapping.reference_type, mapping.reference_normalized_value), mapping]),
  );
  const rowsByReference = new Map<string, Omit<ProjectReferenceRegistryRow, "target" | "observedTargets" | "targetCoverage" | "state" | "action">>();

  for (const snapshot of snapshots) {
    for (const reference of snapshot.referenceGroups) {
      const normalizedValue = reference.normalized_value.trim();
      if (!normalizedValue) continue;
      const id = referenceKey(reference.reference_type, normalizedValue);
      const existing = rowsByReference.get(id);
      const environment = environmentFromReference(snapshot.environment, reference, targetIdsByEnvironmentAsset);
      if (existing) {
        existing.environments.push(environment);
        appendUnique(existing.candidateAssetIds, reference.candidate_asset_ids);
        appendUnique(existing.resolvedAssetIds, reference.resolved_asset_ids);
        if (reference.resolved_asset_id) appendUnique(existing.resolvedAssetIds, [reference.resolved_asset_id]);
        continue;
      }
      const resolvedAssetIds = [...reference.resolved_asset_ids];
      if (reference.resolved_asset_id) appendUnique(resolvedAssetIds, [reference.resolved_asset_id]);
      rowsByReference.set(id, {
        id,
        referenceType: reference.reference_type,
        normalizedValue,
        mapping: mappingsByReference.get(id) ?? null,
        environments: [environment],
        candidateAssetIds: [...reference.candidate_asset_ids],
        resolvedAssetIds,
      });
    }
  }

  for (const mapping of scopedMappings) {
    const id = referenceKey(mapping.reference_type, mapping.reference_normalized_value);
    if (rowsByReference.has(id)) continue;
    rowsByReference.set(id, {
      id,
      referenceType: mapping.reference_type,
      normalizedValue: mapping.reference_normalized_value,
      mapping,
      environments: [],
      candidateAssetIds: [],
      resolvedAssetIds: [],
    });
  }

  const rows = [...rowsByReference.values()]
    .map((row) => {
      const target = row.mapping
        ? targetsByKey.get(referenceMappingTargetKey(row.mapping.target_identifier_kind, row.mapping.target_normalized_value)) ?? null
        : null;
      const observedTargets = buildObservedTargets(row.environments, targetsByKey);
      const targetCoverage = projectTargetCoverage(row, target);
      const registryRow: ProjectReferenceRegistryRow = {
        ...row,
        target,
        observedTargets,
        targetCoverage,
        state: "needs_mapping",
        action: null,
      };
      registryRow.state = projectMappingState(registryRow);
      registryRow.action = projectMappingAction(registryRow);
      return registryRow;
    })
    .sort((left, right) => {
      const stateDifference = projectMappingDisplayRank(left.state) - projectMappingDisplayRank(right.state);
      if (stateDifference) return stateDifference;
      const referenceDifference = left.normalizedValue.localeCompare(right.normalizedValue);
      return referenceDifference || left.referenceType.localeCompare(right.referenceType);
    });

  return { rows, targets };
}

export function buildProjectMappingTargets(snapshots: ProjectAssetsSnapshot[]): ProjectMappingTarget[] {
  const targets = new Map<string, ProjectMappingTarget>();
  for (const snapshot of snapshots) {
    for (const asset of snapshot.assets) {
      const identifier = referenceMappingTargetIdentifier(asset);
      if (!identifier) continue;
      const id = referenceMappingTargetKey(identifier.kind, identifier.value);
      const current = targets.get(id);
      if (current) {
        appendUnique(current.assetIds, [asset.id]);
        appendUnique(current.environmentIds, [snapshot.environment.id]);
        appendUnique(current.environmentNames, [snapshot.environment.name]);
        continue;
      }
      const target = buildReferenceMappingTargets([asset])[0];
      if (!target) continue;
      targets.set(id, {
        ...target,
        assetIds: [asset.id],
        environmentIds: [snapshot.environment.id],
        environmentNames: [snapshot.environment.name],
      });
    }
  }
  return [...targets.values()].sort((left, right) => left.displayName.localeCompare(right.displayName));
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

export function projectTargetCoverage(
  row: Pick<ProjectReferenceRegistryRow, "environments">,
  target: Pick<ProjectMappingTarget, "environmentIds" | "environmentNames"> | null,
): ProjectTargetCoverage {
  const affectedEnvironments = row.environments;
  const targetEnvironmentIds = new Set(target?.environmentIds ?? []);
  const availableEnvironmentNames = affectedEnvironments
    .filter((environment) => targetEnvironmentIds.has(environment.environmentId))
    .map((environment) => environment.environmentName);
  const missingEnvironmentNames = affectedEnvironments
    .filter((environment) => !targetEnvironmentIds.has(environment.environmentId))
    .map((environment) => environment.environmentName);
  return {
    availableEnvironmentNames,
    missingEnvironmentNames,
    available: availableEnvironmentNames.length,
    total: affectedEnvironments.length,
  };
}

export function projectMappingState(row: Pick<ProjectReferenceRegistryRow, "mapping" | "environments">): ProjectMappingState {
  if (!row.environments.length) return "stored_only";
  if (hasUnstableResolution(row.environments)) return "review";
  const mapping = row.mapping;
  const manualEnvironmentCount = row.environments.filter((environment) => mappingAppliedInEnvironment(environment, mapping)).length;
  const missingTargetCount = row.environments.filter((environment) => mappingTargetMissingInEnvironment(environment, mapping)).length;

  if (mapping) {
    if (missingTargetCount) return "missing_target";
    if (manualEnvironmentCount === row.environments.length) return "manual";
    if (manualEnvironmentCount) return "partial";
    return "inactive";
  }

  if (row.environments.every((environment) => environment.groupStatus === "resolved_single")) return "automatic";
  return "needs_mapping";
}

export function projectMappingAction(row: Pick<ProjectReferenceRegistryRow, "mapping" | "environments" | "state">): ProjectMappingAction {
  if (row.mapping) return row.state === "missing_target" ? "repair" : "edit";
  if (row.state === "automatic") return "edit";
  if (row.environments.some((environment) => environment.groupStatus === "resolved_mixed")) return "edit";
  if (row.environments.some((environment) => environment.groupStatus === "partially_resolved")) return "complete";
  if (row.environments.some((environment) => environment.groupStatus === "ambiguous")) return "resolve";
  if (row.environments.some((environment) => environment.groupStatus === "unresolved")) return "map";
  return "edit";
}

export function projectMappingStateLabel(state: ProjectMappingState) {
  return presentReferenceResolution(state).label;
}

export function projectMappingActionLabel(action: ProjectMappingAction) {
  const labels: Record<Exclude<ProjectMappingAction, null>, string> = {
    map: "Map",
    resolve: "Resolve",
    complete: "Complete",
    repair: "Repair",
    edit: "Edit",
    review: "Review",
  };
  return action ? labels[action] : "View";
}

/**
 * Project mappings may replace automatic outcomes for the canonical reference.
 */
export function canCreateProjectMapping(row: Pick<ProjectReferenceRegistryRow, "mapping" | "state" | "action">) {
  return !row.mapping;
}

export function canEditProjectMapping(row: Pick<ProjectReferenceRegistryRow, "mapping" | "state">) {
  return Boolean(row.mapping);
}

export function projectMappingDisplayRank(state: ProjectMappingState) {
  if (state === "manual") return 0;
  if (state === "automatic") return 2;
  return 1;
}

export function projectMappingTargetBusinessKey(row: Pick<ProjectReferenceRegistryRow, "mapping">) {
  if (!row.mapping) return "No saved mapping";
  return `${row.mapping.target_identifier_kind} · ${row.mapping.target_normalized_value}`;
}

export function projectEnvironmentResolutionLabel(
  environment: ProjectReferenceEnvironment,
  mapping: ProjectReferenceMapping | null,
) {
  return presentReferenceResolution(projectEnvironmentResolutionState(environment, mapping)).label;
}

function projectEnvironmentResolutionState(
  environment: ProjectReferenceEnvironment,
  mapping: ProjectReferenceMapping | null,
): ReferenceResolutionState {
  if (environment.groupStatus === "resolved_mixed" || environment.groupStatus === "partially_resolved") return "review";
  if (mappingAppliedInEnvironment(environment, mapping)) return "manual";
  if (mappingTargetMissingInEnvironment(environment, mapping)) return "missing_target";
  if (environment.groupStatus === "resolved_single") return "automatic";
  if (environment.groupStatus === "mapping_target_missing") return "missing_target";
  return "needs_mapping";
}

export function projectMappingResolutionSummary(row: Pick<ProjectReferenceRegistryRow, "environments" | "mapping">) {
  const counts = new Map<ReferenceResolutionState, number>();
  for (const environment of row.environments) {
    const state = projectEnvironmentResolutionState(environment, row.mapping);
    counts.set(state, (counts.get(state) ?? 0) + 1);
  }
  if (counts.size === 1) {
    const [state, count] = [...counts.entries()][0];
    return projectMappingResolutionDetail(state, count);
  }
  return [...counts.entries()].map(([state, count]) => `${presentReferenceResolution(state).label} ${count}`).join(" · ");
}

function projectMappingResolutionDetail(state: ReferenceResolutionState, count: number) {
  const environments = `${count} environment${count === 1 ? "" : "s"}`;
  if (state === "manual") return `Applied in ${environments}`;
  if (state === "automatic") return `${environments} resolved`;
  if (state === "needs_mapping") return `${environments} pending`;
  if (state === "missing_target") return `Target missing in ${environments}`;
  if (state === "review" || state === "partial") return `${environments} ${count === 1 ? "needs" : "need"} review`;
  if (state === "inactive") return "Saved rule not applied";
  return "Saved rule only";
}

export function projectMappingTargetLabel(row: Pick<ProjectReferenceRegistryRow, "mapping" | "target" | "observedTargets">) {
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

function environmentFromReference(
  environment: Pick<Environment, "id" | "name">,
  reference: AssetReferenceGroupItem,
  targetIdsByEnvironmentAsset: Map<string, string>,
): ProjectReferenceEnvironment {
  const resolvedAssetIds = [...reference.resolved_asset_ids];
  if (reference.resolved_asset_id) appendUnique(resolvedAssetIds, [reference.resolved_asset_id]);
  const observedTargetIds = resolvedAssetIds.flatMap((assetId) => {
    const targetId = targetIdsByEnvironmentAsset.get(environmentAssetKey(environment.id, assetId));
    return targetId ? [targetId] : [];
  });
  return {
    environmentId: environment.id,
    environmentName: environment.name,
    groupStatus: reference.group_status,
    resolvedAssetId: reference.resolved_asset_id ?? null,
    resolvedAssetIds,
    observedTargetIds: [...new Set(observedTargetIds)],
    candidateAssetIds: [...reference.candidate_asset_ids],
    manualMappingId: reference.manual_mapping?.mapping_id ?? null,
    manualMappingStatus: reference.manual_mapping?.status ?? null,
    occurrenceCount: reference.occurrence_ids.length || reference.dependency_count,
    consumerCount: reference.consumer_asset_ids.length,
  };
}

function buildTargetIdsByEnvironmentAsset(snapshots: ProjectAssetsSnapshot[]) {
  const targetIds = new Map<string, string>();
  for (const snapshot of snapshots) {
    for (const asset of snapshot.assets) {
      const identifier = referenceMappingTargetIdentifier(asset);
      if (!identifier) continue;
      targetIds.set(
        environmentAssetKey(snapshot.environment.id, asset.id),
        referenceMappingTargetKey(identifier.kind, identifier.value),
      );
    }
  }
  return targetIds;
}

function buildObservedTargets(
  environments: ProjectReferenceEnvironment[],
  targetsByKey: Map<string, ProjectMappingTarget>,
): ProjectObservedTarget[] {
  const observed = new Map<string, ProjectObservedTarget>();
  for (const environment of environments) {
    for (const targetId of environment.observedTargetIds) {
      const target = targetsByKey.get(targetId);
      if (!target) continue;
      const current = observed.get(targetId);
      if (current) {
        appendUnique(current.environmentIds, [environment.environmentId]);
        appendUnique(current.environmentNames, [environment.environmentName]);
        continue;
      }
      observed.set(targetId, {
        target,
        environmentIds: [environment.environmentId],
        environmentNames: [environment.environmentName],
      });
    }
  }
  return [...observed.values()].sort((left, right) => left.target.displayName.localeCompare(right.target.displayName));
}

function environmentAssetKey(environmentId: number, assetId: string) {
  return `${environmentId}\u001f${assetId}`;
}

function mappingAppliedInEnvironment(environment: ProjectReferenceEnvironment, mapping: ProjectReferenceMapping | null) {
  return Boolean(
    mapping
    && environment.manualMappingId === mapping.id
    && environment.manualMappingStatus !== "target_missing"
    && environment.groupStatus !== "mapping_target_missing",
  );
}

function mappingTargetMissingInEnvironment(environment: ProjectReferenceEnvironment, mapping: ProjectReferenceMapping | null) {
  return Boolean(
    mapping
    && environment.manualMappingId === mapping.id
    && (environment.manualMappingStatus === "target_missing" || environment.groupStatus === "mapping_target_missing"),
  );
}

function hasUnstableResolution(environments: ProjectReferenceEnvironment[]) {
  return environments.some(
    (environment) => environment.groupStatus === "resolved_mixed" || environment.groupStatus === "partially_resolved",
  );
}

function referenceKey(referenceType: ReferenceType, normalizedValue: string) {
  return `${referenceType}\u001f${normalizedValue}`;
}

function appendUnique<T>(target: T[], values: T[]) {
  for (const value of values) {
    if (!target.includes(value)) target.push(value);
  }
}
