import type {
  LineageAsset,
  LineageDataflow,
  LineageDependency,
  LineageReference,
  LineageReferenceOccurrence,
  LineageResponse
} from "../../../shared/api/types";
import { lineageNodeSearchValues, presentLineageAsset } from "./presentation";
import type {
  LineageEntity,
  LineageFilters,
  LineageFocus,
  LineageSearchResult,
  TraceDirection,
  VisibleLineage
} from "./types";
import { isLineageAsset } from "./types";
import type { LineageDataflowFocusTarget } from "../../../shared/lineageNavigation";

export type Relation = {
  id: string;
  source: string;
  target: string;
  type: "dataflow" | "dependency";
  dataflow?: LineageDataflow;
  dependency?: LineageDependency;
};

export interface RelationNeighborGroup {
  entityId: string;
  relations: Relation[];
}

const ATTENTION_RESOLUTION_STATUSES = new Set(["ambiguous", "unresolved", "mapping_target_missing"]);

export function isAttentionResolutionStatus(value: string | null | undefined) {
  return Boolean(value && ATTENTION_RESOLUTION_STATUSES.has(value));
}

export interface LineageGraphIndex {
  assets: LineageAsset[];
  references: LineageReference[];
  dataflows: LineageDataflow[];
  dependencies: LineageDependency[];
  entities: LineageEntity[];
  entityById: Map<string, LineageEntity>;
  dataflowById: Map<string, LineageDataflow>;
  dependencyById: Map<string, LineageDependency>;
  occurrenceById: Map<string, LineageReferenceOccurrence>;
  relations: Relation[];
  incoming: Map<string, Relation[]>;
  outgoing: Map<string, Relation[]>;
  searchResults: LineageSearchResult[];
}

export interface LineageFilterOptions {
  connections: string[];
  stages: string[];
  formats: string[];
  resolutions: string[];
}

export function groupRelationsByNeighbor(relations: Relation[], endpoint: "source" | "target"): RelationNeighborGroup[] {
  const groups = new Map<string, Relation[]>();
  for (const relation of relations) {
    const entityId = relation[endpoint];
    const group = groups.get(entityId);
    if (group) group.push(relation);
    else groups.set(entityId, [relation]);
  }
  return Array.from(groups, ([entityId, groupedRelations]) => ({ entityId, relations: groupedRelations }));
}

export function findLineageDataflowByMetadataIdentity(
  index: LineageGraphIndex,
  target: LineageDataflowFocusTarget | null,
) {
  if (!target) return null;
  const sourceId = target.metadataSourceId ?? null;
  const candidates = index.dataflows.filter((dataflow) => sourceId === null || dataflow.metadata_source_id === sourceId);
  const dataflowId = normalizeDataflowIdentity(target.dataflowId);
  if (dataflowId) {
    const matches = candidates.filter((dataflow) => normalizeDataflowIdentity(dataflow.dataflow_id) === dataflowId);
    if (matches.length === 1) return matches[0];
  }
  const name = normalizeDataflowIdentity(target.name);
  if (name) {
    const matches = candidates.filter((dataflow) => normalizeDataflowIdentity(dataflow.name) === name);
    if (matches.length === 1) return matches[0];
  }
  return null;
}

function normalizeDataflowIdentity(value: string | null | undefined) {
  return value?.trim().toLocaleLowerCase() || "";
}

export function referenceNeighborAttentionStatus(entity: LineageEntity | undefined, relations: Relation[]) {
  if (!entity || isLineageAsset(entity)) return null;
  for (const status of ["mapping_target_missing", "ambiguous", "unresolved"]) {
    if (relations.some((relation) => relation.dependency?.resolution_status === status)) return status;
  }
  return null;
}

export function createLineageGraphIndex(lineage: LineageResponse | null): LineageGraphIndex {
  const assets = lineage?.assets ?? [];
  const references = lineage?.references ?? [];
  const dataflows = lineage?.dataflows ?? [];
  const dependencies = lineage?.dependencies ?? [];
  const occurrences = lineage?.reference_occurrences ?? [];
  const entities: LineageEntity[] = [...assets, ...references];
  const entityById = new Map(entities.map((entity) => [entity.id, entity]));
  const relations: Relation[] = [
    ...dataflows.map((dataflow) => ({
      id: dataflow.id,
      source: dataflow.source_asset_id,
      target: dataflow.destination_asset_id,
      type: "dataflow" as const,
      dataflow
    })),
    ...dependencies.map((dependency) => ({
      id: dependency.id,
      source: dependency.resolved_asset_id || dependency.reference_id,
      target: dependency.target_asset_id,
      type: "dependency" as const,
      dependency
    }))
  ];
  const incoming = new Map<string, Relation[]>();
  const outgoing = new Map<string, Relation[]>();
  for (const relation of relations) {
    append(incoming, relation.target, relation);
    append(outgoing, relation.source, relation);
  }

  const assetResults = assets.map<LineageSearchResult>((asset) => {
    const presentation = presentLineageAsset(asset);
    return {
      id: asset.id,
      kind: "asset",
      title: presentation.locator,
      subtitle: `${presentation.fullIdentity} · ${presentation.badge}`,
      identity: asset.id,
      searchText: lineageNodeSearchValues(asset).join(" ").toLowerCase()
    };
  });
  const referenceResults = references.map<LineageSearchResult>((reference) => ({
    id: reference.id,
    kind: "reference",
    title: reference.display_name,
    subtitle: `${reference.group_status} · ${reference.reference_type}`,
    identity: reference.id,
    searchText: [
      reference.id,
      reference.display_name,
      reference.normalized_value,
      reference.reference_type,
      reference.group_status,
      ...reference.provenances
    ].join(" ").toLowerCase()
  }));
  const dataflowResults = dataflows.map<LineageSearchResult>((dataflow) => ({
    id: dataflow.id,
    kind: "dataflow",
    title: dataflow.name,
    subtitle: [dataflow.stage, dataflow.load_type, "dataflow"].filter(Boolean).join(" · "),
    identity: dataflow.dataflow_id,
    searchText: [
      dataflow.id,
      dataflow.name,
      dataflow.dataflow_id,
      dataflow.stage,
      dataflow.load_type,
      ...assetSearchValues(entityById.get(dataflow.source_asset_id)),
      ...assetSearchValues(entityById.get(dataflow.destination_asset_id))
    ].filter(Boolean).join(" ").toLowerCase()
  }));
  const dependencyResults = dependencies.map<LineageSearchResult>((dependency) => ({
    id: dependency.id,
    kind: "dependency",
    title: `${dependency.provenance.replace(/_/g, " ")} ${dependency.kind}`,
    subtitle: `${dependency.resolution_status} · dependency`,
    identity: dependency.id,
    searchText: [
      dependency.id,
      dependency.kind,
      dependency.provenance,
      dependency.resolution_status,
      dependency.resolution_method,
      dependency.reference_id,
      dependency.resolved_asset_id,
      ...assetSearchValues(entityById.get(dependency.resolved_asset_id || "")),
      ...referenceSearchValues(entityById.get(dependency.reference_id)),
      ...assetSearchValues(entityById.get(dependency.target_asset_id))
    ].filter(Boolean).join(" ").toLowerCase()
  }));

  return {
    assets,
    references,
    dataflows,
    dependencies,
    entities,
    entityById,
    dataflowById: new Map(dataflows.map((item) => [item.id, item])),
    dependencyById: new Map(dependencies.map((item) => [item.id, item])),
    occurrenceById: new Map(occurrences.map((item) => [item.id, item])),
    relations,
    incoming,
    outgoing,
    searchResults: [...assetResults, ...dataflowResults, ...dependencyResults, ...referenceResults]
  };
}

export function lineageFilterOptions(index: LineageGraphIndex): LineageFilterOptions {
  return {
    connections: uniqueSorted(index.assets.map((asset) => asset.connection_name).filter(isPresent)),
    stages: uniqueSorted(index.dataflows.map((dataflow) => dataflow.stage).filter(isPresent)),
    formats: uniqueSorted(index.assets.map((asset) => asset.format || asset.endpoint_kind).filter(isPresent)),
    resolutions: uniqueSorted([
      ...index.assets.map((asset) => asset.declaration_status),
      ...index.references.map((reference) => reference.group_status),
      ...index.dependencies.map((dependency) => dependency.resolution_status)
    ])
  };
}

export function searchLineage(index: LineageGraphIndex, query: string, limit = 8): LineageSearchResult[] {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return [];
  const tokens = normalized.split(/\s+/).filter(Boolean);
  return index.searchResults
    .filter((result) => tokens.every((token) => result.searchText.includes(token)))
    .sort((left, right) => searchRank(left, normalized) - searchRank(right, normalized)
      || left.title.localeCompare(right.title))
    .slice(0, limit);
}

export function selectVisibleLineage(
  index: LineageGraphIndex,
  filters: LineageFilters,
  focuses: LineageFocus[],
  direction: TraceDirection,
  showReferences: boolean
): VisibleLineage {
  const filtersActive = filters.connections.length > 0
    || filters.stages.length > 0
    || filters.formats.length > 0
    || filters.resolutions.length > 0;
  const allowed = index.relations.filter((relation) => relationMatchesFilters(index, relation, filters));
  const allowedIds = new Set(allowed.map((relation) => relation.id));

  if (!focuses.length) {
    const fullRelations = showReferences
      ? allowed
      : allowed.filter((relation) => relation.type === "dataflow"
        || Boolean(relation.dependency?.resolved_asset_id));
    return visibleFromRelations(index, fullRelations, new Set(), new Set(), filtersActive, false);
  }

  const visibleIds = new Set<string>();
  const focusNodeIds = new Set<string>();
  const focusEdgeIds = new Set<string>();
  for (const focus of focuses) {
    traceFocus(index, focus, direction, allowedIds, visibleIds, focusNodeIds, focusEdgeIds);
  }

  const relations = allowed.filter((relation) => visibleIds.has(relation.id));
  const visible = visibleFromRelations(index, relations, focusNodeIds, focusEdgeIds, filtersActive, true);
  for (const focus of focuses) {
    if (focus.kind === "asset" || focus.kind === "reference") {
      const entity = index.entityById.get(focus.id);
      if (entity && !visible.entities.some((item) => item.id === focus.id)) visible.entities.push(entity);
    }
  }
  return visible;
}

function traceFocus(
  index: LineageGraphIndex,
  focus: LineageFocus,
  direction: TraceDirection,
  allowedIds: Set<string>,
  visibleIds: Set<string>,
  focusNodeIds: Set<string>,
  focusEdgeIds: Set<string>
) {
  if (focus.kind === "dataflow") {
    const dataflow = index.dataflowById.get(focus.id);
    if (!dataflow || !allowedIds.has(dataflow.id)) return;
    visibleIds.add(dataflow.id);
    focusEdgeIds.add(dataflow.id);
    focusNodeIds.add(dataflow.source_asset_id);
    focusNodeIds.add(dataflow.destination_asset_id);
    if (direction !== "downstream") {
      walk(index, dataflow.source_asset_id, "incoming", allowedIds, visibleIds);
    }
    if (direction !== "upstream") {
      walk(index, dataflow.destination_asset_id, "outgoing", allowedIds, visibleIds);
    }
  } else if (focus.kind === "dependency") {
    const dependency = index.dependencyById.get(focus.id);
    if (!dependency || !allowedIds.has(dependency.id)) return;
    visibleIds.add(dependency.id);
    focusEdgeIds.add(dependency.id);
    focusNodeIds.add(dependency.resolved_asset_id || dependency.reference_id);
    focusNodeIds.add(dependency.target_asset_id);
    if (direction !== "downstream") {
      walk(index, dependency.resolved_asset_id || dependency.reference_id, "incoming", allowedIds, visibleIds);
    }
    if (direction !== "upstream") {
      walk(index, dependency.target_asset_id, "outgoing", allowedIds, visibleIds);
    }
  } else {
    if (!index.entityById.has(focus.id)) return;
    focusNodeIds.add(focus.id);
    traceEntity(index, focus.id, direction, allowedIds, visibleIds);
  }
}

function traceEntity(
  index: LineageGraphIndex,
  entityId: string,
  direction: TraceDirection,
  allowedIds: Set<string>,
  visibleIds: Set<string>
) {
  if (direction !== "downstream") walk(index, entityId, "incoming", allowedIds, visibleIds);
  if (direction !== "upstream") walk(index, entityId, "outgoing", allowedIds, visibleIds);
}

function walk(
  index: LineageGraphIndex,
  startId: string,
  direction: "incoming" | "outgoing",
  allowedIds: Set<string>,
  visibleIds: Set<string>
) {
  const queue = [startId];
  const visited = new Set(queue);
  for (let cursor = 0; cursor < queue.length; cursor += 1) {
    const entityId = queue[cursor];
    const relations = direction === "incoming" ? index.incoming.get(entityId) : index.outgoing.get(entityId);
    for (const relation of relations ?? []) {
      if (!allowedIds.has(relation.id)) continue;
      visibleIds.add(relation.id);
      const nextId = direction === "incoming" ? relation.source : relation.target;
      if (visited.has(nextId)) continue;
      visited.add(nextId);
      queue.push(nextId);
    }
  }
}

function visibleFromRelations(
  index: LineageGraphIndex,
  relations: Relation[],
  focusNodeIds: Set<string>,
  focusEdgeIds: Set<string>,
  filtersActive: boolean,
  traceActive: boolean
): VisibleLineage {
  const entityIds = new Set<string>(focusNodeIds);
  for (const relation of relations) {
    entityIds.add(relation.source);
    entityIds.add(relation.target);
  }
  const relationIds = new Set(relations.map((relation) => relation.id));
  const attentionReferenceIdsByAsset = new Map<string, Set<string>>();
  for (const dependency of index.dependencies) {
    if (!entityIds.has(dependency.target_asset_id)) continue;
    if (!isAttentionResolutionStatus(dependency.resolution_status)) continue;
    const referenceIds = attentionReferenceIdsByAsset.get(dependency.target_asset_id) ?? new Set<string>();
    referenceIds.add(dependency.reference_id);
    attentionReferenceIdsByAsset.set(dependency.target_asset_id, referenceIds);
  }
  const issueCountByAsset = new Map(
    Array.from(attentionReferenceIdsByAsset, ([assetId, referenceIds]) => [assetId, referenceIds.size])
  );
  return {
    entities: index.entities.filter((entity) => entityIds.has(entity.id)),
    dataflows: index.dataflows.filter((item) => relationIds.has(item.id)),
    dependencies: index.dependencies.filter((item) => relationIds.has(item.id)),
    focusNodeIds,
    focusEdgeIds,
    issueCountByAsset,
    filtersActive,
    traceActive
  };
}

function relationMatchesFilters(index: LineageGraphIndex, relation: Relation, filters: LineageFilters) {
  const source = index.entityById.get(relation.source);
  const target = index.entityById.get(relation.target);
  const assets = [source, target].filter(isLineageAsset);
  const matchesConnection = !filters.connections.length
    || assets.some((asset) => asset.connection_name && filters.connections.includes(asset.connection_name));
  const matchesStage = !filters.stages.length
    || (relation.type === "dataflow" && relation.dataflow?.stage && filters.stages.includes(relation.dataflow.stage));
  const matchesFormat = !filters.formats.length
    || assets.some((asset) => [asset.format, asset.endpoint_kind].some((value) => value && filters.formats.includes(value)));
  const matchesResolution = !filters.resolutions.length
    || assets.some((asset) => filters.resolutions.includes(asset.declaration_status))
    || (relation.dependency?.resolution_status && filters.resolutions.includes(relation.dependency.resolution_status))
    || (source?.entity_type === "reference" && filters.resolutions.includes(source.group_status));
  return matchesConnection && matchesStage && matchesFormat && matchesResolution;
}

function emptyVisible(filtersActive: boolean, traceActive: boolean): VisibleLineage {
  return {
    entities: [],
    dataflows: [],
    dependencies: [],
    focusNodeIds: new Set(),
    focusEdgeIds: new Set(),
    issueCountByAsset: new Map(),
    filtersActive,
    traceActive
  };
}

function append(map: Map<string, Relation[]>, key: string, relation: Relation) {
  map.set(key, [...(map.get(key) ?? []), relation]);
}

function searchRank(result: LineageSearchResult, query: string) {
  const title = result.title.toLowerCase();
  const identity = result.identity.toLowerCase();
  if (identity === query) return 0;
  if (title === query) return 1;
  if (identity.startsWith(query)) return 2;
  if (title.startsWith(query)) return 3;
  if (result.kind === "asset") return 4;
  if (result.kind === "dataflow") return 5;
  return 6;
}

function assetSearchValues(entity: LineageEntity | undefined) {
  return isLineageAsset(entity) ? lineageNodeSearchValues(entity) : [];
}

function referenceSearchValues(entity: LineageEntity | undefined) {
  if (!entity || isLineageAsset(entity)) return [];
  return [
    entity.id,
    entity.display_name,
    entity.normalized_value,
    entity.reference_type,
    entity.group_status,
    ...entity.provenances,
  ];
}

function uniqueSorted(values: string[]) {
  return [...new Set(values)].sort((left, right) => left.localeCompare(right));
}

function isPresent(value: string | null | undefined): value is string {
  return Boolean(value);
}
