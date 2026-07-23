import { ArrowLeft, Check, ChevronDown, ChevronRight, Code2, Copy, LocateFixed, LogIn, LogOut, PencilLine, TriangleAlert, Workflow, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import type {
  AssetDefinitionResponse,
  AssetInventoryItem,
  AssetReferenceGroupItem,
  AssetReferenceOccurrenceItem,
  LatestStatusResponse,
  LineageAsset,
  LineageDataflow,
  LineageDependency,
  LineageReference,
  MonitoringRecord,
} from "../../../shared/api/domainTypes";
import { StatusPill } from "../../../shared/components/StatusPill";
import { useDrawerEscape } from "../../../shared/hooks/useDrawerEscape";
import { latestRun } from "../model/flow";
import { groupRelationsByNeighbor, isAttentionResolutionStatus, referenceNeighborAttentionStatus, type LineageGraphIndex, type Relation } from "../model/graphIndex";
import { assetIconKind, assetTypeTone, presentLineageAsset, referenceTypeAssetType } from "../model/presentation";
import { isLineageAsset, type LineageEntity, type LineageFocus, type LineageSelection } from "../model/types";
import { highlightStructuredValue } from "../../metadata-explorer/MetadataStructuredCell";
import { LineageFormatIcon } from "./LineageFormatIcon";
import { LineageCodeDialog } from "./LineageCodeDialog";
import { LineageEntityIcon } from "./LineageEntityIcon";
import { ReferenceMappingDrawer } from "../../reference-mappings/ReferenceMappingDrawer";
import { ReferenceMappingClearAction } from "../../reference-mappings/ReferenceMappingClearAction";
import { referenceMappingAction, referenceMappingActionLabel, type ReferenceMappingPayload } from "../../reference-mappings/referenceMappingModel";
import { assetDetailOptions, assetSourceOptions, referenceDetailOptions } from "../../assets/assetsQueries";
import { lineageDataflowRunsOptions } from "../lineageQueries";

type InspectorTone = "selected" | "input" | "output";
type RelationshipTone = Exclude<InspectorTone, "selected">;
type OpenRelated = (focus: LineageFocus, tone: RelationshipTone) => void;
type OpenDataflowDetails = (dataflow: LineageDataflow) => void;
type InspectorPage = LineageFocus & { view: "summary" | "full"; tone: InspectorTone };
const LINEAGE_DRAWER_HISTORY_KEY = "datacoolieLineageDrawer";

type LineageDrawerHistoryState =
  | { kind: "details"; depth: number }
  | { kind: "mapping"; depth: number; referenceId: string };

function lineageDrawerHistoryFromState(state: unknown): LineageDrawerHistoryState | null {
  if (!state || typeof state !== "object") return null;
  const value = (state as Record<string, unknown>)[LINEAGE_DRAWER_HISTORY_KEY];
  if (!value || typeof value !== "object") return null;
  const record = value as Record<string, unknown>;
  const depth = record.depth;
  if (typeof depth !== "number" || !Number.isInteger(depth) || depth <= 0) return null;
  if (record.kind === "details") return { kind: "details", depth };
  if (record.kind === "mapping" && typeof record.referenceId === "string" && record.referenceId) {
    return { kind: "mapping", depth, referenceId: record.referenceId };
  }
  return null;
}

export function LineageDetailsDrawer({
  environmentId,
  selection,
  index,
  latestStatus,
  metadataDataflowIds,
  suspended = false,
  onClose,
  onFocusItem,
  onOpenDataflowDetails,
  mappingAssets,
  mappingBusy = false,
  onCreateReferenceMapping,
  onUpdateReferenceMapping,
  onDeleteReferenceMapping,
  onRefreshReferenceMappings,
}: {
  environmentId: number;
  selection: LineageSelection;
  index: LineageGraphIndex;
  latestStatus: LatestStatusResponse | null;
  metadataDataflowIds: ReadonlySet<string>;
  suspended?: boolean;
  onClose: () => void;
  onFocusItem: (focus: LineageFocus) => void;
  onOpenDataflowDetails: OpenDataflowDetails;
  mappingAssets: AssetInventoryItem[];
  mappingBusy?: boolean;
  onCreateReferenceMapping: (payload: ReferenceMappingPayload) => Promise<unknown>;
  onUpdateReferenceMapping: (mappingId: number, payload: ReferenceMappingPayload) => Promise<unknown>;
  onDeleteReferenceMapping: (mappingId: number) => Promise<unknown>;
  onRefreshReferenceMappings: () => Promise<void>;
}) {
  const [pages, setPages] = useState<InspectorPage[]>([]);
  const pagesRef = useRef<InspectorPage[]>([]);
  const historyDepthRef = useRef(0);
  const suppressNextPopRef = useRef(false);
  const selectionKeyRef = useRef<string | null>(null);
  const [mappingReferenceId, setMappingReferenceId] = useState<string | null>(null);
  const mappingReferenceIdRef = useRef<string | null>(null);
  const [clearReferenceId, setClearReferenceId] = useState<string | null>(null);
  const [mappingActionError, setMappingActionError] = useState<{ referenceId: string; message: string } | null>(null);

  function updatePages(next: InspectorPage[]) {
    pagesRef.current = next;
    setPages(next);
  }

  function updateMappingReference(next: string | null) {
    mappingReferenceIdRef.current = next;
    setMappingReferenceId(next);
  }

  function unwindDrawerHistory() {
    const depth = historyDepthRef.current;
    historyDepthRef.current = 0;
    if (depth <= 0) return;
    suppressNextPopRef.current = true;
    window.history.go(-depth);
  }

  function closeDrawer() {
    updatePages([]);
    unwindDrawerHistory();
    onClose();
  }

  useDrawerEscape(closeDrawer, Boolean(selection) && !suspended);

  useEffect(() => {
    const selectionKey = selection ? `${selection.kind}:${selection.id}` : null;
    if (selectionKeyRef.current !== selectionKey && historyDepthRef.current > 0) unwindDrawerHistory();
    selectionKeyRef.current = selectionKey;
    updateMappingReference(null);
    setClearReferenceId(null);
    setMappingActionError(null);
    updatePages(selection ? [{ ...selection, view: "summary", tone: "selected" }] : []);
  }, [selection?.kind, selection?.id]);

  useEffect(() => {
    function handlePopState(event: PopStateEvent) {
      if (suppressNextPopRef.current) {
        suppressNextPopRef.current = false;
        return;
      }
      const nextHistory = lineageDrawerHistoryFromState(event.state);
      const nextDepth = nextHistory?.depth ?? 0;
      const removedDepth = historyDepthRef.current - nextDepth;
      const currentMappingReferenceId = mappingReferenceIdRef.current;
      const nextMappingReferenceId = nextHistory?.kind === "mapping" ? nextHistory.referenceId : null;
      historyDepthRef.current = nextDepth;
      if (currentMappingReferenceId || nextMappingReferenceId) {
        updateMappingReference(nextMappingReferenceId);
        if (currentMappingReferenceId !== nextMappingReferenceId) return;
      }
      if (removedDepth <= 0 || pagesRef.current.length <= 1) return;
      updatePages(pagesRef.current.slice(0, Math.max(1, pagesRef.current.length - removedDepth)));
    }
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  const page = pages.at(-1) ?? null;
  if (!selection || !page) return null;

  const push = (next: InspectorPage) => {
    const nextDepth = historyDepthRef.current + 1;
    const historyState = window.history.state && typeof window.history.state === "object"
      ? { ...(window.history.state as Record<string, unknown>) }
      : {};
    window.history.pushState({ ...historyState, [LINEAGE_DRAWER_HISTORY_KEY]: { kind: "details", depth: nextDepth } }, "", window.location.href);
    historyDepthRef.current = nextDepth;
    updatePages([...pagesRef.current, next]);
  };
  const openRelated: OpenRelated = (focus, tone) => push({ ...focus, view: "summary", tone });
  const backPage = () => {
    if (historyDepthRef.current > 0) {
      window.history.back();
      return;
    }
    if (pagesRef.current.length > 1) updatePages(pagesRef.current.slice(0, -1));
  };

  const openReferenceMapping = (referenceId: string) => {
    const nextDepth = historyDepthRef.current + 1;
    const historyState = window.history.state && typeof window.history.state === "object"
      ? { ...(window.history.state as Record<string, unknown>) }
      : {};
    window.history.pushState({
      ...historyState,
      [LINEAGE_DRAWER_HISTORY_KEY]: { kind: "mapping", depth: nextDepth, referenceId },
    }, "", window.location.href);
    historyDepthRef.current = nextDepth;
    updateMappingReference(referenceId);
  };

  const backFromReferenceMapping = () => {
    const currentHistory = lineageDrawerHistoryFromState(window.history.state);
    if (currentHistory?.kind === "mapping" && currentHistory.referenceId === mappingReferenceIdRef.current) {
      window.history.back();
      return;
    }
    updateMappingReference(null);
  };

  async function clearReferenceMapping(reference: LineageReference) {
    const mappingId = reference.manual_mapping?.mapping_id;
    if (!mappingId) return;
    if (clearReferenceId !== reference.id) {
      setClearReferenceId(reference.id);
      setMappingActionError(null);
      return;
    }
    setMappingActionError(null);
    try {
      await onDeleteReferenceMapping(mappingId);
      await onRefreshReferenceMappings();
      setClearReferenceId(null);
    } catch (error) {
      setMappingActionError({
        referenceId: reference.id,
        message: error instanceof Error ? error.message : "Mapping could not be cleared.",
      });
    }
  }

  return (
    <aside className={`lineage-detail-drawer selection-${page.tone}${suspended ? " is-suspended" : ""}`} aria-label="Lineage details" aria-hidden={suspended || undefined}>
      <div className="lineage-detail-nav">
        {mappingReferenceId ? (
          <button type="button" aria-label="Back to reference details" onClick={backFromReferenceMapping}>
            <ArrowLeft size={16} />
          </button>
        ) : pages.length > 1 ? (
          <button type="button" aria-label="Back to previous details" onClick={backPage}>
            <ArrowLeft size={16} />
          </button>
        ) : <span />}
        <button type="button" aria-label="Close lineage details" onClick={closeDrawer}><X size={16} /></button>
      </div>
      {mappingReferenceId ? (() => {
        const reference = index.entityById.get(mappingReferenceId);
        if (!reference || isLineageAsset(reference)) return null;
        return <div className="lineage-reference-mapping-overlay">
          <ReferenceMappingDrawer
            className="lineage-reference-mapping-drawer"
            reference={lineageReferenceAsAssetReference(reference)}
            assets={mappingAssets}
            busy={mappingBusy}
            onCreate={onCreateReferenceMapping}
            onUpdate={onUpdateReferenceMapping}
            onDelete={onDeleteReferenceMapping}
            onRefresh={onRefreshReferenceMappings}
            onBack={backFromReferenceMapping}
          />
        </div>;
      })() : <InspectorContent
        environmentId={environmentId}
        page={page}
        index={index}
        latestStatus={latestStatus}
        metadataDataflowIds={metadataDataflowIds}
        onToggleDetails={() => setPages((current) => current.map((item, itemIndex) => itemIndex === current.length - 1
          ? { ...item, view: item.view === "full" ? "summary" : "full" }
          : item))}
        onOpenRelated={openRelated}
        onOpenDataflowDetails={onOpenDataflowDetails}
        onFocus={() => onFocusItem({ kind: page.kind, id: page.id })}
        onOpenReferenceMapping={openReferenceMapping}
        mappingBusy={mappingBusy}
        clearReferenceId={clearReferenceId}
        mappingActionError={mappingActionError}
        onClearReferenceMapping={clearReferenceMapping}
        onDismissClearReference={() => setClearReferenceId(null)}
      />}
    </aside>
  );
}

function InspectorContent({ environmentId, page, index, latestStatus, metadataDataflowIds, onToggleDetails, onOpenRelated, onOpenDataflowDetails, onFocus, onOpenReferenceMapping, mappingBusy, clearReferenceId, mappingActionError, onClearReferenceMapping, onDismissClearReference }: {
  environmentId: number;
  page: InspectorPage;
  index: LineageGraphIndex;
  latestStatus: LatestStatusResponse | null;
  metadataDataflowIds: ReadonlySet<string>;
  onToggleDetails: () => void;
  onOpenRelated: OpenRelated;
  onOpenDataflowDetails: OpenDataflowDetails;
  onFocus: () => void;
  onOpenReferenceMapping: (referenceId: string) => void;
  mappingBusy: boolean;
  clearReferenceId: string | null;
  mappingActionError: { referenceId: string; message: string } | null;
  onClearReferenceMapping: (reference: LineageReference) => Promise<void>;
  onDismissClearReference: () => void;
}) {
  if (page.kind === "asset" || page.kind === "reference") {
    const entity = index.entityById.get(page.id);
    if (!entity) return <MissingDetails />;
    return isLineageAsset(entity)
      ? <AssetDetails environmentId={environmentId} asset={entity} full={page.view === "full"} index={index} onToggleDetails={onToggleDetails} onOpenRelated={onOpenRelated} onFocus={onFocus} />
      : <ReferenceDetails environmentId={environmentId} reference={entity} full={page.view === "full"} index={index} onToggleDetails={onToggleDetails} onOpenRelated={onOpenRelated} onFocus={onFocus} onOpenMapping={() => onOpenReferenceMapping(entity.id)} mappingBusy={mappingBusy} clearConfirming={clearReferenceId === entity.id} mappingActionError={mappingActionError?.referenceId === entity.id ? mappingActionError.message : null} onClearMapping={onClearReferenceMapping} onDismissClear={onDismissClearReference} />;
  }
  if (page.kind === "dataflow") {
    const dataflow = index.dataflowById.get(page.id);
    return dataflow
      ? <DataflowDetails environmentId={environmentId} dataflow={dataflow} full={page.view === "full"} index={index} run={latestRun(latestStatus, dataflow.dataflow_id, dataflow.name)} canOpenDataflowDetails={metadataDataflowIds.has(dataflow.id)} onToggleDetails={onToggleDetails} onOpenRelated={onOpenRelated} onOpenDataflowDetails={onOpenDataflowDetails} onFocus={onFocus} />
      : <MissingDetails />;
  }
  const dependency = index.dependencyById.get(page.id);
  return dependency
    ? <DependencyDetails environmentId={environmentId} dependency={dependency} full={page.view === "full"} index={index} onToggleDetails={onToggleDetails} onOpenRelated={onOpenRelated} onFocus={onFocus} />
    : <MissingDetails />;
}

function AssetDetails({ environmentId, asset, full, index, onToggleDetails, onOpenRelated, onFocus }: {
  environmentId: number; asset: LineageAsset; full: boolean; index: LineageGraphIndex;
  onToggleDetails: () => void; onOpenRelated: OpenRelated; onFocus: () => void;
}) {
  const detail = useQuery({ ...assetDetailOptions(environmentId, asset.id), enabled: full });
  const presentation = presentLineageAsset(asset);
  const incoming = index.incoming.get(asset.id) ?? [];
  const outgoing = index.outgoing.get(asset.id) ?? [];
  const informationRows = assetInformationRows(asset);
  return (
    <>
      <InspectorHeader
        eyebrow={`Asset · ${humanize(asset.asset_type)}`}
        title={presentation.locator}
        subtitle={presentation.fullIdentity}
        icon={<LineageFormatIcon kind={presentation.iconKind} label={presentation.badge} size={20} />}
      />
      <IdentityField label="Asset identity" value={asset.id} />
      <PrimaryActions full={full} onToggleDetails={onToggleDetails} onFocus={onFocus} />
      <section className="lineage-asset-information">
        <span className="lineage-detail-section-title">Asset information</span>
        <DetailRows rows={informationRows} />
      </section>
      <LineageAssetDefinition key={asset.id} environmentId={environmentId} asset={asset} />
      <RelationshipSummary incoming={incoming} outgoing={outgoing} index={index} onOpenRelated={onOpenRelated} />
      {full ? <AssetProvenance asset={detail.data?.asset ?? asset} loading={detail.isFetching} /> : null}
    </>
  );
}

function LineageAssetDefinition({ environmentId, asset }: { environmentId: number; asset: LineageAsset }) {
  const supported = asset.asset_type === "sql_query" || asset.asset_type === "python_function";
  const [dialogOpen, setDialogOpen] = useState(false);
  const source = useQuery({ ...assetSourceOptions(environmentId, asset.id), enabled: supported && dialogOpen });
  const definition = source.data ?? null;
  const error = source.error instanceof Error ? source.error.message : source.error ? "Unable to load the code definition." : null;

  if (!supported) return null;

  const content = lineageDefinitionContent(asset, definition);
  const label = asset.asset_type === "sql_query" ? "SQL definition" : "Python definition";
  const summary = lineageDefinitionSummary(asset, definition, content);
  const status = source.isFetching
    ? "Loading details…"
    : error
      ? "Unable to load"
      : definition
        ? humanize(definition.status)
        : "Ready to view";

  return (
    <section className={`lineage-asset-definition asset-tone-${assetTypeTone(asset.asset_type)}`}>
      <div className="lineage-asset-definition-heading">
        <span className="lineage-detail-section-title">Definition</span>
        <small>{label}</small>
      </div>
      <div className="lineage-asset-definition-summary">
        <div>
          <strong>{status}</strong>
          <small>{summary}</small>
        </div>
        <button type="button" onClick={() => { setDialogOpen(true); if (source.isError) void source.refetch(); }}>
          <Code2 size={13} />
          {error ? "Retry" : "View code"}
        </button>
      </div>
      {dialogOpen ? <LineageCodeDialog asset={asset} definition={definition} loading={source.isFetching || (!definition && !error)} error={error} onClose={() => setDialogOpen(false)} /> : null}
    </section>
  );
}

function lineageDefinitionContent(asset: LineageAsset, definition: AssetDefinitionResponse | null) {
  if (asset.asset_type === "python_function") {
    return definition?.source?.trim() || definition?.formatted?.trim() || definition?.raw?.trim() || "";
  }
  return definition?.formatted?.trim() || definition?.raw?.trim() || asset.query?.trim() || "";
}

function lineageDefinitionSummary(asset: LineageAsset, definition: AssetDefinitionResponse | null, content: string) {
  const lineCount = definition?.line_count || (content ? content.split(/\r?\n/u).length : 0);
  return [
    asset.asset_type === "python_function" ? definition?.relative_path || definition?.function_path || asset.python_function : "SQL",
    lineCount ? `${lineCount} ${lineCount === 1 ? "line" : "lines"}` : null,
  ].filter(Boolean).join(" · ");
}

function AssetProvenance({ asset, loading }: { asset: Pick<LineageAsset, "observations" | "identifiers">; loading: boolean }) {
  const observations = asset.observations ?? [];
  const metadataSources = [...new Set(observations.map(metadataSourceTitle))];
  return <section className="lineage-asset-provenance">
    <span className="lineage-detail-section-title">Origin and provenance</span>
    {loading ? <p className="lineage-detail-note">Loading provenance…</p> : metadataSources.length ? <MetadataSourceList sources={metadataSources} /> : <p className="lineage-detail-note">No source observation is available for this asset.</p>}
    {asset.identifiers?.length ? <CodeBlock label={`Canonical identifiers (${asset.identifiers.length})`} value={JSON.stringify(asset.identifiers, null, 2)} kind="json" /> : null}
    {observations.length ? <CodeBlock label="Raw observations" value={JSON.stringify(observations, null, 2)} kind="json" /> : null}
  </section>;
}

function MetadataSourceList({ sources }: { sources: string[] }) {
  return <div className="lineage-metadata-source-summary">
    <span>Metadata source</span>
    <div>{sources.map((source) => <span key={source} title={source}>{source}</span>)}</div>
  </div>;
}

function assetInformationRows(asset: LineageAsset) {
  const common: Array<[string, string | null | undefined]> = [
    ["Roles", asset.roles?.join(" · ")],
    ["Connection", asset.connection_name],
    ["Connection type", asset.connection_type],
    ["Format", asset.format]
  ];
  const representative: Array<[string, string | null | undefined]> = asset.asset_type === "table"
    ? [["Catalog", asset.catalog], ["Database", asset.database], ["Schema", asset.schema_name], ["Table", asset.table]]
    : asset.asset_type === "path"
      ? [["Path", asset.path || asset.endpoint_locator]]
      : asset.asset_type === "python_function"
        ? [["Python function", asset.python_function || asset.endpoint_locator]]
        : asset.asset_type === "api"
          ? [["Endpoint", asset.endpoint_locator], ["Endpoint kind", asset.endpoint_kind]]
          : [];
  return compactRows([...common, ...representative]);
}

function metadataSourceTitle(observation: Record<string, unknown>, index: number) {
  const explicitName = stringValue(observation.metadata_source_name);
  if (explicitName) return explicitName;
  const uri = stringValue(observation.metadata_source_uri);
  if (uri) {
    const segments = uri.split(/[\\/]+/).filter(Boolean);
    const fileName = segments.at(-1) || uri;
    const parent = segments.at(-2);
    return parent && ["connection", "connections", "dataflow", "dataflows", "schema_hint", "schema_hints"].includes(parent.toLowerCase())
      ? `${parent}/${fileName}`
      : fileName;
  }
  return observation.metadata_source_id ? `Metadata source ${String(observation.metadata_source_id)}` : `Observation ${index + 1}`;
}

function ReferenceDetails({ environmentId, reference, full, index, onToggleDetails, onOpenRelated, onFocus, onOpenMapping, mappingBusy, clearConfirming, mappingActionError, onClearMapping, onDismissClear }: {
  environmentId: number; reference: LineageReference; full: boolean; index: LineageGraphIndex;
  onToggleDetails: () => void; onOpenRelated: OpenRelated; onFocus: () => void; onOpenMapping: () => void;
  mappingBusy: boolean; clearConfirming: boolean; mappingActionError: string | null;
  onClearMapping: (reference: LineageReference) => Promise<void>; onDismissClear: () => void;
}) {
  const detail = useQuery({ ...referenceDetailOptions(environmentId, reference.id), enabled: full });
  const resolvedIds = reference.resolved_asset_ids.length ? reference.resolved_asset_ids : reference.resolved_asset_id ? [reference.resolved_asset_id] : [];
  const occurrences = detail.data?.occurrences ?? [];
  const usageRelations = index.relations.filter((relation) => relation.dependency?.reference_id === reference.id);
  const referenceObjectType = referenceTypeAssetType(reference.reference_type);
  const referenceBadge = humanize(reference.reference_type);
  const mappingAction = referenceMappingAction(lineageReferenceAsAssetReference(reference));
  const mappingActionLabel = referenceMappingActionLabel(mappingAction);
  const informationRows = [
    ["Normalized value", reference.normalized_value],
    ["Provenance", reference.provenances.map(humanize).join(" · ") || "not available"],
    ["Consumers", String(new Set(reference.consumer_asset_ids).size)],
    ["Occurrences", String(reference.occurrence_count)],
    ["Dependencies", String(reference.dependency_count)]
  ] satisfies Array<[string, string]>;
  return (
    <>
      <InspectorHeader
        eyebrow={`Reference · ${referenceBadge}`}
        title={reference.display_name}
        status={reference.resolution.state}
        statusPlacement="second-line"
        statusVariant="reference-badge"
        icon={<LineageEntityIcon iconKind={assetIconKind(referenceObjectType)} badge={referenceBadge} referenceType={reference.reference_type} size={20} />}
        iconClassName={`is-reference asset-tone-${assetTypeTone(referenceObjectType)}`}
      />
      <IdentityField label="Reference identity" value={reference.id} />
      <PrimaryActions full={full} onToggleDetails={onToggleDetails} onFocus={onFocus} />
      {mappingActionLabel ? (
        <div className="lineage-reference-mapping-actions">
          <button className={`text-action ${mappingAction === "edit" ? "reference-mapping-action-edit" : "reference-mapping-action-map"}`} type="button" disabled={mappingBusy} onClick={onOpenMapping}>{mappingActionLabel}</button>
          {reference.manual_mapping?.mapping_id ? <ReferenceMappingClearAction confirming={clearConfirming} disabled={mappingBusy} onClear={() => void onClearMapping(reference)} onDismiss={onDismissClear} /> : null}
          {mappingActionError ? <small className="reference-mapping-action-error" role="alert">{mappingActionError}</small> : null}
        </div>
      ) : null}
      <section className="lineage-reference-information">
        <span className="lineage-detail-section-title">Reference information</span>
        <DetailRows rows={informationRows} />
      </section>
      {resolvedIds.length || reference.candidate_asset_ids.length ? <section className="lineage-reference-resolution">
        <span className="lineage-detail-section-title">Resolution</span>
        {resolvedIds.length ? <ObjectList title="Resolved targets" ids={resolvedIds} kind="asset" index={index} onOpenRelated={onOpenRelated} /> : null}
        {reference.candidate_asset_ids.length ? <ObjectList title="Candidates" ids={reference.candidate_asset_ids} kind="asset" index={index} onOpenRelated={onOpenRelated} /> : null}
      </section> : null}
      <section className="lineage-reference-outputs">
        <RelationshipGroup title="Outputs" tone="output" relations={usageRelations} direction="target" index={index} emptyText="No consumer asset." onOpenRelated={onOpenRelated} />
      </section>
      {full ? <>
        {detail.isFetching ? <p className="lineage-detail-note">Loading usage evidence…</p> : <OccurrenceList occurrences={occurrences} />}
      </> : null}
    </>
  );
}

function lineageReferenceAsAssetReference(reference: LineageReference): AssetReferenceGroupItem {
  return {
    ...reference,
    occurrence_ids: [],
    observations: [],
    resolved_asset: null,
    candidate_assets: [],
    consumer_assets: [],
    dataflow_ids: [],
    attention_count: 0,
    attention_items: [],
  };
}


function DataflowDetails({ environmentId, dataflow, full, index, run, canOpenDataflowDetails, onToggleDetails, onOpenRelated, onOpenDataflowDetails, onFocus }: {
  environmentId: number; dataflow: LineageDataflow; full: boolean; index: LineageGraphIndex; run: MonitoringRecord | null;
  canOpenDataflowDetails: boolean;
  onToggleDetails: () => void; onOpenRelated: OpenRelated; onOpenDataflowDetails: OpenDataflowDetails; onFocus: () => void;
}) {
  const status = typeof run?.status === "string" ? run.status : undefined;
  const informationRows: Array<[string, string]> = [
    ["Stage", humanize(dataflow.stage)],
    ["Load type", humanize(dataflow.load_type)],
    ["Latest run", formatTimestamp(firstValue(run, "completed_at", "end_time", "started_at", "start_time"))],
    ["Duration", formatDuration(firstValue(run, "duration_seconds"))]
  ];
  return (
    <>
      <InspectorHeader
        eyebrow="Dataflow"
        title={dataflow.name}
        status={status}
        emptyStatusLabel="No run data"
        statusPlacement="second-line"
        icon={<Workflow size={20} aria-hidden="true" />}
        iconClassName="is-dataflow"
      />
      <IdentityField label="Dataflow identity" value={dataflow.dataflow_id} />
      <PrimaryActions full={full} onToggleDetails={onToggleDetails} onFocus={onFocus}>
        {canOpenDataflowDetails ? <button type="button" onClick={() => onOpenDataflowDetails(dataflow)}><PencilLine size={14} />Open dataflow</button> : null}
      </PrimaryActions>
      <section className="lineage-dataflow-information">
        <span className="lineage-detail-section-title">Dataflow information</span>
        <DetailRows rows={informationRows} />
      </section>
      <section className="lineage-dataflow-endpoints">
        <EndpointGroup title="Input" tone="input" entityId={dataflow.source_asset_id} index={index} onOpenRelated={onOpenRelated} />
        <EndpointGroup title="Output" tone="output" entityId={dataflow.destination_asset_id} index={index} onOpenRelated={onOpenRelated} />
      </section>
      {full ? <RunHistory key={dataflow.id} environmentId={environmentId} dataflow={dataflow} /> : null}
      {full ? <DetailRows rows={[["Metadata source", dataflow.metadata_source_uri]]} /> : null}
      {full ? <IdentityField label="Lineage dataflow identity" value={dataflow.id} /> : null}
    </>
  );
}

function DependencyDetails({ environmentId, dependency, full, index, onToggleDetails, onOpenRelated, onFocus }: {
  environmentId: number; dependency: LineageDependency; full: boolean; index: LineageGraphIndex;
  onToggleDetails: () => void; onOpenRelated: OpenRelated; onFocus: () => void;
}) {
  const detail = useQuery({ ...referenceDetailOptions(environmentId, dependency.reference_id), enabled: full });
  const occurrence = detail.data?.occurrences.find((item) => item.id === dependency.reference_occurrence_id);
  const sourceId = dependency.resolved_asset_id || dependency.reference_id;
  const sourceEntity = index.entityById.get(sourceId);
  const targetEntity = index.entityById.get(dependency.target_asset_id);
  const referenceEntity = index.entityById.get(dependency.reference_id);
  const dependencyTitle = `${sourceEntity ? entityLabel(sourceEntity) : sourceId} → ${targetEntity ? entityLabel(targetEntity) : dependency.target_asset_id}`;
  return (
    <>
      <InspectorHeader
        eyebrow="Dependency"
        title={dependencyTitle}
        status={dependency.resolution.state}
        statusPlacement="second-line"
        statusVariant="reference-badge"
        statusLabel={`${humanize(dependency.provenance)} · ${humanize(dependency.kind)} · resolution`}
        icon={<LogIn size={20} aria-hidden="true" />}
        iconClassName="is-dependency"
      />
      <IdentityField label="Dependency identity" value={dependency.id} />
      <PrimaryActions full={full} onToggleDetails={onToggleDetails} onFocus={onFocus} />
      <section className="lineage-dependency-information">
        <span className="lineage-detail-section-title">Dependency information</span>
        <dl>
          <div><dt>Kind</dt><dd>{humanize(dependency.kind)}</dd></div>
          <div><dt>Provenance</dt><dd>{humanize(dependency.provenance)}</dd></div>
          <div><dt>Resolution method</dt><dd>{humanize(dependency.resolution_method)}</dd></div>
          <DependencyReferenceField reference={referenceEntity} fallback={dependency.reference_id} onOpenRelated={onOpenRelated} />
        </dl>
      </section>
      <section className="lineage-dependency-endpoints">
        <EndpointGroup title="Input" tone="input" entityId={sourceId} index={index} onOpenRelated={onOpenRelated} />
        <EndpointGroup title="Output" tone="output" entityId={dependency.target_asset_id} index={index} onOpenRelated={onOpenRelated} />
      </section>
      {full ? <section className="lineage-dependency-details">
        <span className="lineage-detail-section-title">Usage evidence</span>
        {detail.isFetching
          ? <p className="lineage-detail-note">Loading usage evidence…</p>
          : detail.isError
            ? <p className="lineage-detail-note">Unable to load usage evidence.</p>
            : occurrence
              ? <DependencyOccurrenceDetails occurrence={occurrence} />
              : <p className="lineage-detail-note">Occurrence evidence is not available.</p>}
      </section> : null}
    </>
  );
}

function InspectorHeader({ eyebrow, title, subtitle, status, statusLabel, emptyStatusLabel, statusPlacement = "title", statusVariant = "pill", icon, iconClassName }: { eyebrow: string; title: string; subtitle?: string; status?: string; statusLabel?: string; emptyStatusLabel?: string; statusPlacement?: "title" | "second-line"; statusVariant?: "pill" | "reference-badge"; icon?: ReactNode; iconClassName?: string }) {
  const secondLineStatus = statusPlacement === "second-line" && (status || emptyStatusLabel)
    ? statusVariant === "reference-badge" && status
      ? <span className="lineage-inspector-second-line-status">{statusLabel ? <span className="lineage-inspector-status-label">{statusLabel}</span> : null}<span className={`lineage-inspector-reference-status lineage-node-badge ${status}`}>{humanize(status)}</span></span>
      : <span className="lineage-inspector-second-line-status">{status ? <StatusPill status={status} /> : <span className="lineage-no-run-status">{emptyStatusLabel}</span>}</span>
    : null;
  return <header className="lineage-inspector-header">
    <span className="eyebrow">{eyebrow}</span>
    <div className={`lineage-inspector-main${icon ? " has-icon" : ""}`}>
      {icon ? <span className={`lineage-inspector-icon${iconClassName ? ` ${iconClassName}` : ""}`}>{icon}</span> : null}
      <div className="lineage-inspector-copy">
        <div className="lineage-detail-title"><h3>{title}</h3>{status && statusPlacement === "title" ? <StatusPill status={status} /> : null}</div>
        {secondLineStatus}
        {subtitle ? <p>{subtitle}</p> : null}
      </div>
    </div>
  </header>;
}

function PrimaryActions({ full, onToggleDetails, onFocus, children }: { full: boolean; onToggleDetails: () => void; onFocus: () => void; children?: ReactNode }) {
  return <div className="lineage-detail-actions"><button type="button" onClick={onToggleDetails}>{full ? "Hide details" : "Full details"}</button><button type="button" onClick={onFocus}><LocateFixed size={14} />Trace lineage</button>{children}</div>;
}

function RelationshipSummary({ incoming, outgoing, index, onOpenRelated }: { incoming: Relation[]; outgoing: Relation[]; index: LineageGraphIndex; onOpenRelated: OpenRelated }) {
  return <section className="lineage-relationship-summary"><RelationshipGroup title="Inputs" tone="input" relations={incoming} direction="source" attentionCount={attentionReferenceCount(incoming)} index={index} onOpenRelated={onOpenRelated} /><RelationshipGroup title="Outputs" tone="output" relations={outgoing} direction="target" index={index} onOpenRelated={onOpenRelated} /></section>;
}

function EndpointGroup({ title, tone, entityId, index, onOpenRelated }: { title: string; tone: RelationshipTone; entityId: string; index: LineageGraphIndex; onOpenRelated: OpenRelated }) {
  return <div className={`lineage-relationship-group tone-${tone}`}>
    <RelationshipGroupHeader title={title} count={1} />
    <EntityButton entity={index.entityById.get(entityId)} fallback={entityId} tone={tone} onOpenRelated={onOpenRelated} />
  </div>;
}

function RelationshipGroupHeader({ title, count, attentionCount = 0 }: { title: string; count: number; attentionCount?: number }) {
  return <strong>
    <span className="lineage-relationship-heading">{title}<span className="lineage-relationship-count">{count}</span></span>
    {attentionCount ? <span className="lineage-relationship-attention" title={`${attentionCount} input reference${attentionCount === 1 ? "" : "s"} requiring attention`}><TriangleAlert size={11} />{attentionCount}</span> : null}
  </strong>;
}

function RelationshipGroup({ title, tone, relations, direction, attentionCount = 0, index, emptyText = "None", onOpenRelated }: { title: string; tone: RelationshipTone; relations: Relation[]; direction: "source" | "target"; attentionCount?: number; index: LineageGraphIndex; emptyText?: string; onOpenRelated: OpenRelated }) {
  const groups = groupRelationsByNeighbor(relations, direction);
  return <div className={`lineage-relationship-group tone-${tone}`}><RelationshipGroupHeader title={title} count={groups.length} attentionCount={attentionCount} />{groups.length ? groups.map((group) => {
    const entity = index.entityById.get(group.entityId);
    return <div className="lineage-neighbor-group" key={group.entityId}>
      <EntityButton entity={entity} fallback={group.entityId} tone={tone} statusOverride={referenceNeighborAttentionStatus(entity, group.relations)} onOpenRelated={onOpenRelated} />
      <div className="lineage-via-list">{group.relations.map((relation) => {
        const attentionStatusValue = attentionStatus(relation.dependency?.resolution.state);
        return <div className="lineage-via-row" key={relation.id}><button className={`lineage-via-button${attentionStatusValue ? ` has-attention status-${attentionStatusValue}` : ""}`} type="button" onClick={() => onOpenRelated({ kind: relation.type, id: relation.id }, tone)}>
        <span className="lineage-via-direction" title={`${title} ${relation.type}`}>{tone === "input" ? <LogIn size={14} /> : <LogOut size={14} />}</span>
        <span className="lineage-via-copy">
          <strong>{relationTitle(relation)}</strong>
          <span className="lineage-via-meta">
            <small>{relationSubtitle(relation)}</small>
            {attentionStatusValue ? <span className={`lineage-via-status lineage-node-badge ${attentionStatusValue}`}>{humanize(attentionStatusValue)}</span> : null}
          </span>
        </span>
        <ChevronRight size={13} />
      </button></div>})}</div>
    </div>;
  }) : <small>{emptyText}</small>}</div>;
}

function ObjectList({ title, ids, kind, index, empty, onOpenRelated }: { title: string; ids: string[]; kind: "asset"; index: LineageGraphIndex; empty?: string; onOpenRelated: OpenRelated }) {
  return <section className="lineage-object-list"><strong>{title}<span>{ids.length}</span></strong>{ids.length ? ids.map((id) => <EntityButton key={id} entity={index.entityById.get(id)} fallback={id} forcedKind={kind} tone="output" onOpenRelated={onOpenRelated} />) : <small>{empty}</small>}</section>;
}

function EntityButton({ entity, fallback, forcedKind, tone, statusOverride, onOpenRelated }: { entity: LineageEntity | undefined; fallback: string; forcedKind?: "asset"; tone: RelationshipTone; statusOverride?: string | null; onOpenRelated: OpenRelated }) {
  const presentation = entityPresentation(entity, fallback);
  const kind = forcedKind || (isLineageAsset(entity) ? "asset" : "reference");
  const referenceObjectType = presentation.referenceType ? referenceTypeAssetType(presentation.referenceType) : null;
  const referenceAttentionStatus = attentionStatus(statusOverride) ?? attentionStatus(presentation.referenceStatus);
  return <button className="lineage-neighbor-button" type="button" disabled={!entity} onClick={() => entity && onOpenRelated({ kind, id: entity.id }, tone)}>
    <span className={`lineage-neighbor-icon${referenceObjectType ? ` is-reference asset-tone-${assetTypeTone(referenceObjectType)}` : ""}`}><LineageEntityIcon iconKind={presentation.iconKind} badge={presentation.badge} referenceType={presentation.referenceType} size={17} /></span>
    <span className="lineage-neighbor-copy">
      <strong>{presentation.title}</strong>
      <span className="lineage-neighbor-meta">
        <small>{presentation.subtitle}</small>
        {referenceAttentionStatus ? <span className={`lineage-neighbor-status lineage-node-badge ${referenceAttentionStatus}`}>{humanize(referenceAttentionStatus)}</span> : null}
      </span>
    </span>
    {entity ? <ChevronRight size={13} /> : null}
  </button>;
}

function DependencyReferenceField({ reference, fallback, onOpenRelated }: { reference: LineageEntity | undefined; fallback: string; onOpenRelated: OpenRelated }) {
  if (!reference) return <div><dt>Reference</dt><dd>{fallback}</dd></div>;
  const presentation = entityPresentation(reference, fallback);
  const referenceObjectType = presentation.referenceType ? referenceTypeAssetType(presentation.referenceType) : null;
  return <div className="lineage-dependency-reference"><dt>Reference</dt><dd><button type="button" onClick={() => onOpenRelated({ kind: "reference", id: reference.id }, "input")}>
    <span className={`lineage-dependency-reference-icon${referenceObjectType ? ` asset-tone-${assetTypeTone(referenceObjectType)}` : ""}`}><LineageEntityIcon iconKind={presentation.iconKind} badge={presentation.badge} referenceType={presentation.referenceType} size={16} /></span>
    <span className="lineage-dependency-reference-copy"><strong>{presentation.title}</strong><small>{presentation.subtitle}</small></span>
    <ChevronRight size={13} />
  </button></dd></div>;
}

function DependencyOccurrenceDetails({ occurrence }: { occurrence: AssetReferenceOccurrenceItem }) {
  const locationText = occurrenceLocationText(occurrence.source_location);
  return <DetailRows rows={[
    ["Raw value", occurrence.raw_value],
    ["Normalized value", occurrence.normalized_value],
    ["Provenance", humanize(occurrence.provenance)],
    ["Resolution method", humanize(occurrence.resolution_method)],
    ["Context scope", occurrence.context_scope || "not available"],
    ["Source location", locationText || "not available"],
    ["Occurrence identity", occurrence.id]
  ]} />;
}

function OccurrenceList({ occurrences }: { occurrences: AssetReferenceOccurrenceItem[] }) {
  return <section className="lineage-object-list"><strong>Usage evidence<span>{occurrences.length}</span></strong>{occurrences.map((occurrence) => <OccurrenceDetails key={occurrence.id} occurrence={occurrence} />)}</section>;
}

function OccurrenceDetails({ occurrence }: { occurrence: AssetReferenceOccurrenceItem }) {
  const locationText = occurrenceLocationText(occurrence.source_location);
  return <div className="lineage-occurrence"><div><span>{occurrence.raw_value}</span><StatusPill status={occurrence.resolution.state} /></div><small>{occurrence.provenance} · {humanize(occurrence.resolution_method)}{locationText ? ` · ${locationText}` : ""}</small>{occurrence.observations.length ? <CodeBlock label="Evidence" value={JSON.stringify(occurrence.observations, null, 2)} kind="json" /> : null}</div>;
}

function RunHistory({ environmentId, dataflow }: { environmentId: number; dataflow: LineageDataflow }) {
  const [open, setOpen] = useState(false);
  const runs = useQuery({ ...lineageDataflowRunsOptions(environmentId, dataflow.dataflow_id, dataflow.name), enabled: open });
  return <section className="lineage-run-history"><button type="button" onClick={() => setOpen((value) => !value)}>{open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}Run history</button>{open ? <div>{runs.isFetching ? <small>Loading run history…</small> : runs.error ? <small className="error">{runs.error instanceof Error ? runs.error.message : "Unable to load run history."}</small> : runs.data?.length ? runs.data.map((run, index) => <div className="lineage-run-row" key={String(run.dataflow_run_id ?? run.started_at ?? index)}><StatusPill status={typeof run.status === "string" ? run.status : "unknown"} /><span>{formatTimestamp(firstValue(run, "completed_at", "end_time", "started_at", "start_time"))}</span><small>{formatDuration(run.duration_seconds)}</small></div>) : <small>No run history available.</small>}</div> : null}</section>;
}

function DetailRows({ rows }: { rows: Array<[string, string]> }) { return <dl>{rows.map(([label, value]) => <div key={label}><dt>{label}</dt><dd title={value}>{value}</dd></div>)}</dl>; }

function IdentityField({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  async function copyIdentity() { await navigator.clipboard.writeText(value); setCopied(true); window.setTimeout(() => setCopied(false), 1400); }
  return <div className="lineage-canonical-identity"><span>{label}</span><code title={value}>{value}</code><button type="button" aria-label={`Copy ${label.toLowerCase()}`} onClick={copyIdentity}>{copied ? <Check size={13} /> : <Copy size={13} />}</button></div>;
}

function CodeBlock({ label, value, kind = "text" }: { label: string; value: string; kind?: "json" | "text" }) {
  return <section className={`lineage-detail-code${kind === "json" ? " is-json" : ""}`}><span>{label}</span><pre>{kind === "json" ? highlightStructuredValue(value, "object") : value}</pre></section>;
}
function MissingDetails() { return <p className="lineage-detail-note">This object is no longer available in the current lineage result.</p>; }
function entityLabel(entity: LineageAsset | LineageReference | undefined) { if (!entity) return "unknown"; if (isLineageAsset(entity)) { const p = presentLineageAsset(entity); return `${p.connection} - ${p.locator}`; } return entity.display_name; }
function entityPresentation(entity: LineageEntity | undefined, fallback: string) {
  if (isLineageAsset(entity)) {
    const presentation = presentLineageAsset(entity);
    return { title: presentation.locator, subtitle: presentation.connection, iconKind: presentation.iconKind, badge: presentation.badge, referenceType: null, referenceStatus: null };
  }
  if (entity) {
    const objectType = referenceTypeAssetType(entity.reference_type);
    return {
      title: entity.display_name,
      subtitle: [humanize(entity.reference_type), entity.provenances.join(" + ")].filter(Boolean).join(" · "),
      iconKind: assetIconKind(objectType),
      badge: humanize(entity.reference_type),
      referenceType: entity.reference_type,
      referenceStatus: entity.resolution.state
    };
  }
  return { title: fallback, subtitle: "Entity unavailable", iconKind: assetIconKind("unresolved"), badge: "Unavailable entity", referenceType: null, referenceStatus: null };
}
function relationTitle(relation: Relation) {
  if (relation.type === "dataflow") return relation.dataflow?.name || "Dataflow";
  return `${humanize(relation.dependency?.provenance)} · ${humanize(relation.dependency?.kind)}`;
}
function relationSubtitle(relation: Relation) {
  if (relation.type === "dataflow") return [relation.dataflow?.stage, relation.dataflow?.load_type].filter(Boolean).map((value) => humanize(value)).join(" · ") || "dataflow";
  if (attentionStatus(relation.dependency?.resolution.state)) return humanize(relation.dependency?.resolution_method);
  return [relation.dependency?.resolution.state, relation.dependency?.resolution_method].filter(Boolean).map((value) => humanize(value)).join(" · ") || "dependency";
}
function attentionReferenceCount(relations: Relation[]) { return new Set(relations.flatMap((relation) => attentionStatus(relation.dependency?.resolution.state) && relation.dependency?.reference_id ? [relation.dependency.reference_id] : [])).size; }
function attentionStatus(value: string | null | undefined) { return isAttentionResolutionStatus(value) ? value! : null; }
function humanize(value: string | null | undefined) { return value ? value.replace(/_/g, " ") : "not available"; }
function occurrenceLocationText(location: AssetReferenceOccurrenceItem["source_location"]) { return [location?.path || location?.module, location?.function_path, location?.line ? `line ${location.line}` : null].filter(Boolean).join(" · "); }
function stringValue(value: unknown) { return typeof value === "string" && value.trim() ? value.trim() : null; }
function compactRows(rows: Array<[string, string | null | undefined]>): Array<[string, string]> { return rows.filter((row): row is [string, string] => Boolean(row[1])); }
function firstValue(record: MonitoringRecord | null, ...keys: string[]) { if (!record) return null; for (const key of keys) { const value = record[key]; if (value !== null && value !== undefined && value !== "") return value; } return null; }
function formatTimestamp(value: unknown) { if (!value) return "not available"; const date = new Date(String(value)); if (Number.isNaN(date.getTime())) return String(value); return new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date); }
function formatDuration(value: unknown) { const duration = Number(value); return Number.isFinite(duration) ? `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(duration)} s` : "not available"; }
