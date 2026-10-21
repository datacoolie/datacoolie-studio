import type { ProjectReferenceRegistryResponseTransport } from "./contractTypes";
import { toReferenceResolution } from "./assetAdapters";
import {
  asRecord,
  nullableString,
  numberArray,
  numberValue,
  optionalNumber,
  recordArray,
  stringArray,
  stringValue,
} from "./transportValues";
import type {
  AssetInventoryItem,
  ProjectReferenceMapping,
  ProjectReferenceRegistryEnvironment,
  ProjectReferenceRegistryFailure,
  ProjectReferenceRegistryObservedTarget,
  ProjectReferenceRegistryResponse,
  ProjectReferenceRegistryRow,
  ProjectReferenceRegistryTarget,
  ProjectReferenceRegistryTargetCoverage,
  ReferenceType,
  TargetIdentifierKind,
} from "./domainTypes";

const REFERENCE_TYPES = [
  "table_reference",
  "path_reference",
  "api_endpoint_reference",
  "unknown",
] as const;
const TARGET_KINDS = ["logical_table", "physical_path", "api_endpoint"] as const;
const ASSET_TYPES = ["table", "path", "sql_query", "python_function", "api", "unresolved"] as const;

export function toProjectReferenceRegistryResponse(
  response: ProjectReferenceRegistryResponseTransport,
): ProjectReferenceRegistryResponse {
  return {
    project_id: response.project_id,
    mappings: response.mappings.map(toProjectReferenceMapping),
    rows: response.rows.map(toProjectReferenceRegistryRow),
    targets: response.targets.map(toProjectReferenceRegistryTarget),
    failures: response.failures.map(toProjectReferenceRegistryFailure),
  };
}

function toProjectReferenceRegistryRow(value: unknown): ProjectReferenceRegistryRow {
  const row = asRecord(value);
  return {
    id: stringValue(row.id),
    reference_type: toReferenceType(row.reference_type),
    normalized_value: stringValue(row.normalized_value),
    mapping: toOptionalMapping(row.mapping),
    resolution: toReferenceResolution(row.resolution),
    environments: recordArray(row.environments).map(toProjectReferenceRegistryEnvironment),
    candidate_asset_ids: stringArray(row.candidate_asset_ids),
    resolved_asset_ids: stringArray(row.resolved_asset_ids),
    target: toOptionalTarget(row.target),
    observed_targets: recordArray(row.observed_targets).map(toObservedTarget),
    target_coverage: toTargetCoverage(row.target_coverage),
  };
}

function toProjectReferenceRegistryTarget(value: unknown): ProjectReferenceRegistryTarget {
  const target = asRecord(value);
  return {
    id: stringValue(target.id),
    asset_id: stringValue(target.asset_id),
    asset_ids: stringArray(target.asset_ids),
    environment_ids: numberArray(target.environment_ids),
    environment_names: stringArray(target.environment_names),
    asset_type: toAssetType(target.asset_type),
    format: nullableString(target.format),
    connection_name: stringValue(target.connection_name),
    display_name: stringValue(target.display_name, stringValue(target.value)),
    context: nullableString(target.context),
    kind: toTargetKind(target.kind),
    value: stringValue(target.value),
    display: stringValue(target.display, stringValue(target.value)),
  };
}

function toProjectReferenceRegistryEnvironment(value: unknown): ProjectReferenceRegistryEnvironment {
  const environment = asRecord(value);
  return {
    environment_id: numberValue(environment.environment_id),
    environment_name: stringValue(environment.environment_name),
    resolution: toReferenceResolution(environment.resolution),
    resolved_asset_id: nullableString(environment.resolved_asset_id),
    resolved_asset_ids: stringArray(environment.resolved_asset_ids),
    observed_target_ids: stringArray(environment.observed_target_ids),
    candidate_asset_ids: stringArray(environment.candidate_asset_ids),
    manual_mapping_id: optionalNumber(environment.manual_mapping_id),
    manual_mapping_status: nullableString(environment.manual_mapping_status),
    occurrence_count: numberValue(environment.occurrence_count),
    consumer_count: numberValue(environment.consumer_count),
  };
}

function toObservedTarget(value: unknown): ProjectReferenceRegistryObservedTarget {
  const observed = asRecord(value);
  return {
    target: toProjectReferenceRegistryTarget(observed.target),
    environment_ids: numberArray(observed.environment_ids),
    environment_names: stringArray(observed.environment_names),
  };
}

function toTargetCoverage(value: unknown): ProjectReferenceRegistryTargetCoverage {
  const coverage = asRecord(value);
  return {
    available_environment_names: stringArray(coverage.available_environment_names),
    missing_environment_names: stringArray(coverage.missing_environment_names),
    available: numberValue(coverage.available),
    total: numberValue(coverage.total),
  };
}

function toProjectReferenceRegistryFailure(value: unknown): ProjectReferenceRegistryFailure {
  const failure = asRecord(value);
  return {
    environment_id: numberValue(failure.environment_id),
    environment_name: stringValue(failure.environment_name),
    message: stringValue(failure.message),
  };
}

function toOptionalMapping(value: unknown): ProjectReferenceMapping | null | undefined {
  if (value === undefined) return undefined;
  if (value === null) return null;
  return toProjectReferenceMapping(value);
}

function toProjectReferenceMapping(value: unknown): ProjectReferenceMapping {
  const mapping = asRecord(value);
  return {
    id: numberValue(mapping.id),
    project_id: numberValue(mapping.project_id),
    reference_type: toReferenceType(mapping.reference_type),
    reference_normalized_value: stringValue(mapping.reference_normalized_value),
    reference_signature: asRecord(mapping.reference_signature),
    target_identifier_kind: toTargetKind(mapping.target_identifier_kind),
    target_normalized_value: stringValue(mapping.target_normalized_value),
    target_display_value: stringValue(mapping.target_display_value),
    note: nullableString(mapping.note),
    created_at: stringValue(mapping.created_at),
    updated_at: stringValue(mapping.updated_at),
  };
}

function toOptionalTarget(value: unknown): ProjectReferenceRegistryTarget | null | undefined {
  if (value === undefined) return undefined;
  if (value === null) return null;
  return toProjectReferenceRegistryTarget(value);
}

function toReferenceType(value: unknown): ReferenceType {
  return REFERENCE_TYPES.includes(value as typeof REFERENCE_TYPES[number])
    ? value as ReferenceType
    : "unknown";
}

function toTargetKind(value: unknown): TargetIdentifierKind {
  return TARGET_KINDS.includes(value as typeof TARGET_KINDS[number])
    ? value as TargetIdentifierKind
    : "logical_table";
}

function toAssetType(value: unknown): AssetInventoryItem["asset_type"] {
  return ASSET_TYPES.includes(value as typeof ASSET_TYPES[number])
    ? value as AssetInventoryItem["asset_type"]
    : "unresolved";
}
