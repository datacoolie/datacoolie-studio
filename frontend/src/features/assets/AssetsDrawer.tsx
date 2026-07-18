import {
  ArrowLeft,
  ArrowDownToLine,
  ArrowUpFromLine,
  Braces,
  Check,
  ChevronDown,
  ChevronRight,
  Code2,
  Copy,
  Database,
  FileCode2,
  GitBranch,
  Loader2,
  LogIn,
  LogOut,
  MapPin,
  Maximize2,
  Minimize2,
  WrapText,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { createPortal } from "react-dom";
import type {
  AssetBrief,
  AssetDefinitionResponse,
  AssetDependsOnItem,
  AssetDetailResponse,
  AssetFlow,
  AssetInventoryItem,
  AssetUsedByItem,
} from "../../shared/api/types";
import { useDrawerEscape } from "../../shared/hooks/useDrawerEscape";
import { metadataNavigationTarget, type MetadataNavigationTarget } from "../../shared/metadataNavigation";
import { LineageFormatIcon } from "../lineage/components/LineageFormatIcon";
import { assetIconKind, assetTypeTone, type AssetIconKind } from "../lineage/model/presentation";
import { assetRelationshipGroups, type AssetRelationshipGroup, type AssetRelationshipVia } from "./assetRelationshipModel";
import { attentionContextLine, metadataQueryForAsset, presentAsset } from "./assetsPresentation";
import { highlightedSourceCode, sourceCodeLanguage } from "./sourceCode";


interface AssetsDrawerProps {
  asset: AssetInventoryItem;
  detail: AssetDetailResponse | null;
  loading: boolean;
  error: string | null;
  canGoBack: boolean;
  onBack: () => void;
  onClose: () => void;
  onSelectDataflow: (flow: AssetFlow) => void;
  onSelectReference: (referenceId: string) => void;
  attentionMappingLabels?: Record<string, string>;
  onOpenReferenceMapping?: (referenceId: string) => void;
  onSelectRelatedAsset: (assetId: string) => void;
  onFocusInLineage: (assetId: string) => void;
  onOpenMetadata: (target: MetadataNavigationTarget) => void;
  onLoadDefinition: () => Promise<AssetDefinitionResponse>;
  onNavigateAway?: () => void;
}

export function AssetsDrawer({
  asset,
  detail,
  loading,
  error,
  canGoBack,
  onBack,
  onClose,
  onSelectDataflow,
  onSelectReference,
  attentionMappingLabels = {},
  onOpenReferenceMapping,
  onSelectRelatedAsset,
  onFocusInLineage,
  onOpenMetadata,
  onLoadDefinition,
  onNavigateAway,
}: AssetsDrawerProps) {
  const presentation = presentAsset(asset);
  const metadataQuery = metadataQueryForAsset(asset);
  const lineageUpstream = detail?.direct_relationships.upstream_assets ?? asset.upstream_count;
  const lineageDownstream = detail?.direct_relationships.downstream_assets ?? asset.downstream_count;
  const lineageInput = detail?.direct_relationships.input_flows ?? asset.input_dataflow_count;
  const lineageOutput = detail?.direct_relationships.output_flows ?? asset.output_dataflow_count;
  const dependsOnCount = detail?.direct_relationships.depends_on_count ?? asset.depends_on_count;
  const usedByCount = detail?.direct_relationships.used_by_count ?? asset.used_by_count;
  const dependsOn = detail?.depends_on ?? [];
  const usedBy = detail?.used_by ?? [];
  const dependsOnMappedEdges = detail?.direct_relationships.depends_on_mapped_count
    ?? dependsOn.filter((item) => Boolean(item.resolved_asset_id || item.resolved_asset?.id)).length;
  const dependsOnUnmappedEdges = detail?.direct_relationships.depends_on_unmapped_count
    ?? Math.max(0, dependsOn.length - dependsOnMappedEdges);
  const lineagePosition = humanize(detail?.direct_relationships.position || positionFromCounts(asset.upstream_count, asset.downstream_count));
  const primaryRole = asset.roles.length ? asset.roles.join(", ") : "-";
  const attentionLabel = asset.attention_count > 0 ? `${asset.attention_count} open` : "clean";
  const focusAssetId = asset.id;
  const [copied, setCopied] = useState(false);
  const [loadedDefinition, setLoadedDefinition] = useState<AssetDefinitionResponse | null>(null);
  const detailRows = assetDetailRows(asset, primaryRole);
  const detailColumns = splitRowsIntoColumns(detailRows, 2);
  const definition = assetDefinition(asset, loadedDefinition ?? detail?.definition ?? null, loading);
  const upstreamRelationships = assetRelationshipGroups(detail, "upstream");
  const downstreamRelationships = assetRelationshipGroups(detail, "downstream");
  const metadataTarget = metadataNavigationTarget(
    [...(detail?.input_flows ?? []), ...(detail?.output_flows ?? [])].map((flow) => flow.dataflow_id),
    metadataQuery,
  );

  useDrawerEscape(onClose);

  useEffect(() => {
    setLoadedDefinition(null);
  }, [asset.id]);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1200);
    return () => window.clearTimeout(timer);
  }, [copied]);

  async function copyAssetId() {
    if (!navigator.clipboard?.writeText) return;
    await navigator.clipboard.writeText(asset.id);
    setCopied(true);
  }

  return createPortal(
    <div className="metadata-drawer-backdrop assets-detail-backdrop" onMouseDown={onClose}>
      <aside className="metadata-drawer assets-drawer asset-detail-drawer" aria-label="Asset details" onMouseDown={(event) => event.stopPropagation()}>
        <header className="metadata-drawer-header assets-drawer-header">
          {canGoBack ? (
            <button
              className="icon-action small assets-drawer-back-icon"
              type="button"
              title="Back"
              aria-label="Back"
              onClick={onBack}
            >
              <ArrowLeft size={14} />
            </button>
          ) : null}

          <div className="assets-drawer-title">
            <span className="eyebrow">Asset</span>
            <div className="assets-drawer-title-line">
              <h2>
                <LineageFormatIcon kind={presentation.iconKind} label={presentation.badge} size={20} />
                <span>{presentation.friendlyName}</span>
              </h2>
              <button className={`icon-action small assets-copy-icon${copied ? " copied" : ""}`} type="button" title={copied ? "Copied" : "Copy id"} aria-label={copied ? "Copied" : "Copy asset id"} onClick={() => void copyAssetId()}>
                {copied ? <Check size={14} /> : <Copy size={14} />}
              </button>
            </div>
            <small title={presentation.fullIdentity}>{presentation.fullIdentity}</small>
            <div className="assets-drawer-header-chips">
              <span className={`assets-header-chip asset-tone-${assetTypeTone(asset.asset_type)}`}>{typeLabel(asset.asset_type)}</span>
              <span className="assets-header-chip">{asset.connection_name || asset.connection_type || "-"}</span>
              <span className="assets-header-chip">{primaryRole}</span>
              <span className={asset.attention_count > 0 ? "assets-attention-count has-attention" : "assets-attention-count"}>
                {attentionLabel}
              </span>
            </div>
          </div>

          <div className="assets-drawer-header-actions">
            <button className="icon-action small" type="button" title="Close" onClick={onClose}>
              <X size={14} />
            </button>
          </div>
        </header>

        <div className="metadata-drawer-body assets-drawer-body">
          {loading ? (
            <div className="assets-drawer-inline-status">
              <Loader2 size={14} className="spin" />
              Loading direct relationship context...
            </div>
          ) : null}

          {error ? (
            <div className="assets-drawer-inline-status assets-drawer-inline-status-error">
              {error}
            </div>
          ) : null}

          <section className="assets-drawer-stats" aria-label="Asset overview">
            <DrawerStat
              label="Upstream"
              value={lineageUpstream}
              detail="assets"
              tone="connectivity"
              description="Direct upstream canonical assets."
            />
            <DrawerStat
              label="Downstream"
              value={lineageDownstream}
              detail="assets"
              tone="connectivity"
              description="Direct downstream canonical assets."
            />
            <DrawerStat
              label="Read By"
              value={lineageOutput}
              detail="metadata flows"
              tone="dataflow"
              description="Dataflows that read this asset as an input."
            />
            <DrawerStat
              label="Written By"
              value={lineageInput}
              detail="metadata flows"
              tone="dataflow"
              description="Dataflows that write or produce this asset."
            />
            <DrawerStat
              label="Depends On"
              value={dependsOnCount}
              detail={`${dependsOnMappedEdges} mapped · ${dependsOnUnmappedEdges} unresolved`}
              tone="reference"
              description="Reference relationships this asset reads or uses."
            />
            <DrawerStat
              label="Used By"
              value={usedByCount}
              detail="resolved references"
              tone="reference"
              description="Assets that use this asset through resolved SQL/Python references."
            />
            <DrawerStat
              label="Position"
              value={lineagePosition}
              detail="direct graph"
              tone="state"
              description="Entry, transit, exit, or isolated in direct graph scope."
            />
            <DrawerStat
              label="Attention"
              value={asset.attention_count}
              detail={asset.attention_count > 0 ? "needs review" : "clean"}
              tone="state"
              warning={asset.attention_count > 0}
              description="Review signals currently linked to this asset."
            />
          </section>

          {(asset.attention_items ?? []).length ? (
            <AttentionSection
              items={asset.attention_items ?? []}
              onSelectReference={onSelectReference}
              mappingLabels={attentionMappingLabels}
              onOpenReferenceMapping={onOpenReferenceMapping}
            />
          ) : null}

          <section className="assets-drawer-section asset-details-section">
            <h3>Asset Details</h3>
            <div className="assets-detail-columns">
              {detailColumns.map((column, columnIndex) => (
                <dl className="assets-detail-list" key={`asset-detail-column-${columnIndex}`}>
                  {column.map((row) => (
                    <div key={row.label}>
                      <dt>{row.label}</dt>
                      <dd>{row.code ? <code>{row.value}</code> : row.value}</dd>
                    </div>
                  ))}
                </dl>
              ))}
            </div>
          </section>

          {definition ? (
            <AssetDefinitionSection
              definition={definition}
              onLoad={async () => {
                const next = await onLoadDefinition();
                setLoadedDefinition(next);
                return next;
              }}
            />
          ) : null}

          <section className="assets-drawer-section assets-provenance-section">
            <h3>Provenance</h3>
            <ProvenanceSection
              identifiers={asset.identifiers ?? []}
              observations={asset.observations ?? []}
              sources={asset.metadata_sources ?? []}
            />
          </section>

          <section className="assets-drawer-section assets-lineage-section">
            <h3>Lineage</h3>
            <div className="assets-drawer-subgrid">
              <RelationshipGroup tone="upstream" title="Upstream" count={upstreamRelationships.length} icon={<ArrowUpFromLine size={12} />}>
                <AssetRelationshipList
                  direction="upstream"
                  emptyText="No direct upstream relationship."
                  groups={upstreamRelationships}
                  onSelectDataflow={onSelectDataflow}
                  onSelectReference={onSelectReference}
                  onSelectRelatedAsset={onSelectRelatedAsset}
                />
              </RelationshipGroup>
              <RelationshipGroup tone="downstream" title="Downstream" count={downstreamRelationships.length} icon={<ArrowDownToLine size={12} />}>
                <AssetRelationshipList
                  direction="downstream"
                  emptyText="No direct downstream relationship."
                  groups={downstreamRelationships}
                  onSelectDataflow={onSelectDataflow}
                  onSelectReference={onSelectReference}
                  onSelectRelatedAsset={onSelectRelatedAsset}
                />
              </RelationshipGroup>
            </div>
          </section>

          <section className="assets-drawer-section assets-references-section">
            <h3>References</h3>
            <div className="assets-drawer-subgrid assets-reference-groups">
              <RelationshipGroup tone="depends-on" title="Depends On" note={`${dependsOnMappedEdges} mapped · ${dependsOnUnmappedEdges} unresolved`}>
                <DependsOnList
                  items={dependsOn}
                  onSelectReference={onSelectReference}
                  onSelectRelatedAsset={onSelectRelatedAsset}
                />
              </RelationshipGroup>
              <RelationshipGroup tone="used-by" title="Used By" note="Assets that use this asset through resolved SQL/Python references">
                <UsedByList
                  items={usedBy}
                  onSelectReference={onSelectReference}
                  onSelectRelatedAsset={onSelectRelatedAsset}
                />
              </RelationshipGroup>
            </div>
          </section>

          {(asset.attention_items ?? []).length ? null : (
            <AttentionSection
              items={asset.attention_items ?? []}
              onSelectReference={onSelectReference}
              mappingLabels={attentionMappingLabels}
              onOpenReferenceMapping={onOpenReferenceMapping}
            />
          )}
        </div>

        <footer className="assets-drawer-footer">
          <button
            className="text-action primary"
            type="button"
            disabled={!focusAssetId}
            onClick={() => {
              if (!focusAssetId) return;
              onFocusInLineage(focusAssetId);
              onNavigateAway?.();
            }}
          >
            <GitBranch size={14} />
            Focus In Lineage
          </button>
          <button
            className="text-action assets-metadata-action"
            type="button"
            disabled={loading}
            onClick={() => {
              onOpenMetadata(metadataTarget);
              onNavigateAway?.();
            }}
          >
            <Database size={14} />
            Open in Metadata
          </button>
        </footer>
      </aside>
    </div>,
    document.body,
  );
}

function AssetDefinitionSection({ definition, onLoad }: {
  definition: AssetDefinitionResponse;
  onLoad: () => Promise<AssetDefinitionResponse>;
}) {
  const defaultTallCode = definition.kind === "sql_query" || definition.kind === "python_function";
  const [expanded, setExpanded] = useState(false);
  const [mode, setMode] = useState(definitionDefaultMode(definition));
  const [wrapped, setWrapped] = useState(definition.kind !== "python_function");
  const [tallCode, setTallCode] = useState(defaultTallCode);
  const [copied, setCopied] = useState(false);
  const [loadingSource, setLoadingSource] = useState(false);
  const [sourceError, setSourceError] = useState<string | null>(null);
  const modes = definitionModes(definition);
  const content = definitionContent(definition, mode);
  const diagnostics = definition.diagnostics ?? [];
  const visibleDiagnostics = diagnostics.slice(0, 3);
  const hiddenDiagnostics = Math.max(0, diagnostics.length - visibleDiagnostics.length);

  useEffect(() => {
    setMode(definitionDefaultMode(definition));
    setWrapped(definition.kind !== "python_function");
    setTallCode(defaultTallCode);
    setCopied(false);
  }, [defaultTallCode, definition.kind, definition.raw, definition.formatted, definition.source, definition.status]);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1200);
    return () => window.clearTimeout(timer);
  }, [copied]);

  async function copyDefinition() {
    if (!content || !navigator.clipboard?.writeText) return;
    await navigator.clipboard.writeText(content);
    setCopied(true);
  }

  function toggleExpanded() {
    const opening = !expanded;
    setExpanded(opening);
    if (!opening || content || loadingSource) return;
    setLoadingSource(true);
    setSourceError(null);
    void onLoad()
      .catch((error: unknown) => setSourceError(error instanceof Error ? error.message : "Definition could not be loaded."))
      .finally(() => setLoadingSource(false));
  }

  return (
    <section className={`assets-drawer-section assets-definition-section assets-definition-${definition.kind}`}>
      <button
        className="assets-definition-summary"
        type="button"
        aria-expanded={expanded}
        onClick={toggleExpanded}
      >
        <span className="assets-definition-summary-icon">{definitionIcon(definition.kind)}</span>
        <span className="assets-definition-summary-copy">
          <strong>{definitionTitle(definition)}</strong>
          <small>{definitionSummary(definition)}</small>
        </span>
        <span className={`assets-definition-status status-${definition.status}`}>{humanize(definition.status)}</span>
        {expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
      </button>

      {expanded ? (
        <div className="assets-definition-panel">
          {loadingSource ? <div className="assets-empty-inline">Loading definition...</div> : null}
          {sourceError ? <div className="reference-mapping-error" role="alert">{sourceError}</div> : null}
          <div className="assets-definition-toolbar">
            {modes.length > 1 ? (
              <div className="assets-definition-tabs" role="tablist" aria-label="Definition view">
                {modes.map((item) => (
                  <button
                    key={item.key}
                    className={mode === item.key ? "is-active" : ""}
                    type="button"
                    role="tab"
                    aria-selected={mode === item.key}
                    onClick={() => setMode(item.key)}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            ) : (
              <span className="assets-definition-toolbar-label">{definitionToolbarLabel(definition, mode)}</span>
            )}
            <div className="assets-definition-actions">
              {content ? (
                <>
                  <button
                    className="icon-action small"
                    type="button"
                    title={wrapped ? "No wrap" : "Wrap"}
                    aria-label={wrapped ? "No wrap" : "Wrap"}
                    onClick={() => setWrapped((value) => !value)}
                  >
                    <WrapText size={13} />
                  </button>
                  <button
                    className="icon-action small"
                    type="button"
                    title={tallCode ? "Compact height" : "Expand height"}
                    aria-label={tallCode ? "Compact height" : "Expand height"}
                    onClick={() => setTallCode((value) => !value)}
                  >
                    {tallCode ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
                  </button>
                  <button
                    className={`icon-action small${copied ? " copied" : ""}`}
                    type="button"
                    title={copied ? "Copied" : "Copy"}
                    aria-label={copied ? "Copied" : "Copy"}
                    onClick={() => void copyDefinition()}
                  >
                    <Copy size={13} />
                  </button>
                </>
              ) : null}
            </div>
          </div>

          {content ? (
            <pre className={`assets-definition-code${wrapped ? " is-wrapped" : ""}${tallCode ? " is-tall" : ""}`}>
              <code
                className={`hljs language-${sourceCodeLanguage(definition.language || definition.kind)}`}
                dangerouslySetInnerHTML={{
                  __html: highlightedSourceCode(content, definition.language || definition.kind),
                }}
              />
            </pre>
          ) : (
            <DefinitionEmpty definition={definition} />
          )}

          {definition.kind === "python_function" ? <PythonDefinitionMeta definition={definition} /> : null}
          {definition.kind === "unresolved" ? <UnresolvedDefinitionMeta definition={definition} /> : null}

          {visibleDiagnostics.length ? (
            <ul className="assets-definition-diagnostics">
              {visibleDiagnostics.map((diagnostic, index) => (
                <li key={`${diagnostic.code}-${index}`}>
                  <strong className={`assets-severity assets-severity-${diagnostic.severity}`}>{diagnostic.severity}</strong>
                  <span>{diagnostic.message}</span>
                </li>
              ))}
              {hiddenDiagnostics ? (
                <li>
                  <strong className="assets-severity assets-severity-info">info</strong>
                  <span>{hiddenDiagnostics} more source notes</span>
                </li>
              ) : null}
            </ul>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

function DefinitionEmpty({ definition }: { definition: AssetDefinitionResponse }) {
  return (
    <div className="assets-definition-empty">
      <strong>{humanize(definition.status)}</strong>
      <span>{definitionEmptyMessage(definition)}</span>
    </div>
  );
}

function PythonDefinitionMeta({ definition }: { definition: AssetDefinitionResponse }) {
  const rows = [
    ["Function", definition.function_path],
    ["Module", definition.module_name],
    ["File", definition.relative_path],
    ["Source", pickUnknownString(definition, "source_uri")],
    ["Lines", pythonLineRange(definition)],
  ].filter(([, value]) => value);
  const matches = Array.isArray(definition.matches) ? definition.matches : [];
  if (!rows.length && !matches.length) return null;
  return (
    <div className="assets-definition-meta">
      {rows.length ? (
        <dl>
          {rows.map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{String(value)}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {matches.length ? (
        <div className="assets-definition-match-list">
          {matches.map((match, index) => (
            <div key={`python-match-${index}`}>
              <strong>{pickUnknownString(match, "relative_path") || pickUnknownString(match, "module_name") || `match ${index + 1}`}</strong>
              <small>{pickUnknownString(match, "source_uri") || pickUnknownString(match, "source_id") || "-"}</small>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function UnresolvedDefinitionMeta({ definition }: { definition: AssetDefinitionResponse }) {
  const rows = definitionDetailRows(definition);
  if (!rows.length) return null;
  return (
    <div className="assets-definition-meta">
      <dl>
        {rows.map((row) => (
          <div key={row.label}>
            <dt>{row.label}</dt>
            <dd>{row.value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function RelationshipGroup({
  title,
  count,
  note,
  icon,
  tone,
  children,
}: {
  title: string;
  count?: number;
  note?: string;
  icon?: ReactNode;
  tone?: "upstream" | "downstream" | "depends-on" | "used-by" | "reads" | "writes";
  children: ReactNode;
}) {
  return (
    <div className={`assets-relationship-group${tone ? ` tone-${tone}` : ""}`}>
      <h4>{icon}<span>{title}</span>{count !== undefined ? <em className="assets-relationship-count">{count}</em> : null}</h4>
      {note ? <small className="assets-section-note">{note}</small> : null}
      {children}
    </div>
  );
}

function AssetRelationshipList({
  groups,
  direction,
  emptyText,
  onSelectDataflow,
  onSelectReference,
  onSelectRelatedAsset,
}: {
  groups: AssetRelationshipGroup[];
  direction: "upstream" | "downstream";
  emptyText: string;
  onSelectDataflow: (flow: AssetFlow) => void;
  onSelectReference: (referenceId: string) => void;
  onSelectRelatedAsset: (assetId: string) => void;
}) {
  if (!groups.length) {
    return <div className="assets-empty-inline">{emptyText}</div>;
  }
  return (
    <ul className="assets-list assets-lineage-relationship-list">
      {groups.map((group) => (
        <li key={group.asset.id}>
          <button
            className="assets-neighbor-button assets-lineage-neighbor-button"
            type="button"
            onClick={() => onSelectRelatedAsset(group.asset.id)}
          >
            <span className="assets-lineage-neighbor-icon">
              <LineageFormatIcon kind={assetBriefIconKind(group.asset)} label={assetBriefBadge(group.asset)} size={16} />
            </span>
            <span className="assets-neighbor-copy">
              <span className="assets-neighbor-title-row">
                <strong title={assetBriefTooltip(group.asset)}>{assetBriefTitle(group.asset)}</strong>
                <span className="assets-neighbor-meta">{group.via.length} via</span>
              </span>
              <AssetRowSubtitle asset={group.asset} />
            </span>
            <ChevronRight className="assets-lineage-open-icon" size={13} />
          </button>
          <div className="assets-lineage-via-list">
            {group.via.length ? group.via.map((via) => (
              <AssetRelationshipViaRow
                key={`${via.kind}-${via.id}`}
                direction={direction}
                via={via}
                onSelectDataflow={onSelectDataflow}
                onSelectReference={onSelectReference}
              />
            )) : <small className="assets-lineage-via-empty">Relationship evidence is unavailable.</small>}
          </div>
        </li>
      ))}
    </ul>
  );
}

function AssetRelationshipViaRow({
  via,
  direction,
  onSelectDataflow,
  onSelectReference,
}: {
  via: AssetRelationshipVia;
  direction: "upstream" | "downstream";
  onSelectDataflow: (flow: AssetFlow) => void;
  onSelectReference: (referenceId: string) => void;
}) {
  const icon = direction === "upstream" ? <LogIn size={14} /> : <LogOut size={14} />;
  if (via.kind === "dataflow") {
    const subtitle = [via.flow.stage, via.flow.load_type].filter(Boolean).map((value) => humanize(value)).join(" · ") || "dataflow";
    return (
      <button className="assets-lineage-via-button" type="button" onClick={() => onSelectDataflow(via.flow)}>
        <span className="assets-lineage-via-direction" aria-hidden="true">{icon}</span>
        <span className="assets-lineage-via-copy">
          <strong>{flowDataflowName(via.flow)}</strong>
          <small>{subtitle}</small>
        </span>
        <ChevronRight size={13} />
      </button>
    );
  }
  const interactive = Boolean(via.referenceId);
  const content = (
    <>
      <span className="assets-lineage-via-direction is-dependency" aria-hidden="true">{icon}</span>
      <span className="assets-lineage-via-copy">
        <strong>{humanize(via.provenance)} · {humanize(via.dependencyKind)}</strong>
        <span className="assets-lineage-via-meta">
          <small>{humanize(via.resolutionMethod)}</small>
          <span className={`lineage-node-badge ${via.resolutionStatus}`}>{humanize(via.resolutionStatus)}</span>
        </span>
      </span>
      {interactive ? <ChevronRight size={13} /> : null}
    </>
  );
  return interactive ? (
    <button className="assets-lineage-via-button is-dependency" type="button" onClick={() => onSelectReference(via.referenceId!)}>{content}</button>
  ) : (
    <div className="assets-lineage-via-button is-dependency is-static">{content}</div>
  );
}

function flowDataflowName(flow: AssetFlow) {
  return flow.name || flow.dataflow_id || "-";
}

function DependsOnList({
  items,
  onSelectReference,
  onSelectRelatedAsset,
}: {
  items: AssetDependsOnItem[];
  onSelectReference: (referenceId: string) => void;
  onSelectRelatedAsset: (assetId: string) => void;
}) {
  if (!items.length) {
    return <div className="assets-empty-inline">No direct Depends On references.</div>;
  }
  return (
    <ul className="assets-list assets-neighbors-list assets-reference-list">
      {items.map((item, index) => {
        const rowTitle = dependsOnTitle(item);
        const rowSubtitle = dependsOnSubtitle(item);
        const rowMeta = <ReferenceMeta kind={item.kind} provenance={item.provenance} status={item.resolution_status} />;
        const rowKey = item.id || `depends-on-${index}`;
        const referenceId = item.reference_id || item.source_reference?.id || null;
        const resolvedAssetId = item.resolved_asset?.id;
        if (referenceId) {
          return (
            <li key={rowKey}>
              <button
                className="assets-neighbor-button"
                type="button"
                onClick={() => onSelectReference(referenceId)}
              >
                <span className="assets-neighbor-main">
                  <span className="assets-neighbor-copy">
                    <strong>{rowTitle}</strong>
                    <ReferenceRowSubtitle connectionName={item.resolved_asset?.connection_name} value={rowSubtitle} />
                  </span>
                </span>
                <span className="assets-neighbor-meta">{rowMeta}</span>
              </button>
            </li>
          );
        }
        if (resolvedAssetId) {
          return (
            <li key={rowKey}>
              <button
                className="assets-neighbor-button"
                type="button"
                onClick={() => onSelectRelatedAsset(resolvedAssetId)}
              >
                <span className="assets-neighbor-main">
                  <span className="assets-neighbor-copy">
                    <strong>{rowTitle}</strong>
                    <ReferenceRowSubtitle connectionName={item.resolved_asset?.connection_name} value={rowSubtitle} />
                  </span>
                </span>
                <span className="assets-neighbor-meta">{rowMeta}</span>
              </button>
            </li>
          );
        }
        return (
          <li key={rowKey}>
            <div className="assets-neighbor-button assets-neighbor-static">
              <span className="assets-neighbor-main">
                <span className="assets-neighbor-copy">
                  <strong>{rowTitle}</strong>
                  <ReferenceRowSubtitle connectionName={item.resolved_asset?.connection_name} value={rowSubtitle} />
                </span>
              </span>
              <span className="assets-neighbor-meta">{rowMeta}</span>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function UsedByList({
  items,
  onSelectReference,
  onSelectRelatedAsset,
}: {
  items: AssetUsedByItem[];
  onSelectReference: (referenceId: string) => void;
  onSelectRelatedAsset: (assetId: string) => void;
}) {
  if (!items.length) {
    return <div className="assets-empty-inline">No resolved Used By references.</div>;
  }
  return (
    <ul className="assets-list assets-neighbors-list assets-reference-list">
      {items.map((item, index) => {
        const target = item.target_asset;
        const rowKey = item.id || `used-by-${index}`;
        const referenceId = item.reference?.id || null;
        return (
          <li key={rowKey}>
            <button
              className="assets-neighbor-button"
              type="button"
              onClick={() => {
                if (referenceId) {
                  onSelectReference(referenceId);
                  return;
                }
                onSelectRelatedAsset(target.id);
              }}
            >
              <span className="assets-neighbor-main">
                <span className="assets-neighbor-copy">
                  <strong>{target.friendly_name || "-"}</strong>
                  <ReferenceRowSubtitle
                    connectionName={target.connection_name}
                    value={usedBySubtitle(target.connection_name || "-", item.reference?.display_name || item.reference?.raw_value || null)}
                  />
                </span>
              </span>
              <span className="assets-neighbor-meta">
                <ReferenceMeta kind={item.kind} provenance={item.provenance} status={item.resolution_status} />
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function AttentionSection({
  items,
  onSelectReference,
  mappingLabels,
  onOpenReferenceMapping,
}: {
  items: NonNullable<AssetInventoryItem["attention_items"]>;
  onSelectReference: (referenceId: string) => void;
  mappingLabels: Record<string, string>;
  onOpenReferenceMapping?: (referenceId: string) => void;
}) {
  return (
    <section className={`assets-drawer-section assets-attention-section${items.length ? " has-attention" : ""}`}>
      <h3>Attention</h3>
      {items.length ? (
        <ul className="assets-list assets-attention-list">
          {items.map((item, index) => {
            const referenceTarget = item.reference_id || null;
            const mappingLabel = referenceTarget ? mappingLabels[referenceTarget] : null;
            const content = (
              <>
                <strong className={`assets-severity assets-severity-${item.severity}`}>{item.severity}</strong>
                <span>
                  {item.message}
                  <small>{attentionContextLine(item)}</small>
                </span>
              </>
            );
            return (
              <li key={`${item.code}-${index}`}>
                {referenceTarget ? (
                  <div className="assets-attention-item">
                    <button className="assets-attention-row" type="button" onClick={() => onSelectReference(referenceTarget)}>
                      {content}
                    </button>
                    {mappingLabel && onOpenReferenceMapping ? (
                      <button className="text-action compact assets-attention-map" type="button" onClick={() => onOpenReferenceMapping(referenceTarget)}>
                        {mappingLabel}
                      </button>
                    ) : null}
                  </div>
                ) : (
                  <span className="assets-attention-row is-static">{content}</span>
                )}
              </li>
            );
          })}
        </ul>
      ) : (
        <div className="assets-empty-inline">No attention item is currently linked to this asset.</div>
      )}
    </section>
  );
}

function ProvenanceSection({
  sources,
  identifiers,
  observations,
}: {
  sources: NonNullable<AssetInventoryItem["metadata_sources"]>;
  identifiers: Array<Record<string, unknown>>;
  observations: Array<Record<string, unknown>>;
}) {
  return (
    <div className="assets-provenance">
      <small className="assets-section-note assets-provenance-note">
        {sources.length} source{sources.length === 1 ? "" : "s"} · {identifiers.length} ids · {observations.length} obs
      </small>
      <div className="assets-provenance-layout">
        <div className="assets-relationship-group assets-provenance-block assets-provenance-sources">
          <GroupTitle count={sources.length} title="Metadata Sources" />
          {sources.length ? (
            <div className="assets-provenance-source-table" role="table" aria-label="Metadata sources">
              <div className="assets-provenance-source-row assets-provenance-source-head" role="row">
                <span role="columnheader">ID</span>
                <span role="columnheader">Name</span>
                <span role="columnheader">URI</span>
              </div>
              {sources.map((source) => (
                <div className="assets-provenance-source-row" key={source.id} role="row" title={source.uri}>
                  <strong role="cell">#{source.id}</strong>
                  <b role="cell">{sourceTail(source.uri)}</b>
                  <span role="cell">{source.uri}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="assets-empty-inline">No metadata source is linked to this asset.</div>
          )}
        </div>
        <div className="assets-provenance-traces">
          <TraceGroup
            count={identifiers.length}
            title="Identifiers"
            tone="identifiers"
            records={identifiers}
            emptyText="No canonical identifier record."
          />
          <TraceGroup
            count={observations.length}
            title="Observations"
            tone="observations"
            records={observations}
            emptyText="No observation record."
          />
        </div>
      </div>
    </div>
  );
}

function GroupTitle({ title, count }: { title: string; count: number }) {
  return (
    <h4 className="assets-group-title">
      <span>{title}</span>
      <em>{count}</em>
    </h4>
  );
}

function TraceGroup({
  title,
  count,
  records,
  emptyText,
  tone,
}: {
  title: string;
  count: number;
  records: Array<Record<string, unknown>>;
  emptyText: string;
  tone: "identifiers" | "observations";
}) {
  return (
    <div className={`assets-relationship-group assets-provenance-block tone-${tone}`}>
      <GroupTitle count={count} title={title} />
      {records.length ? (
        <ul className="assets-list assets-trace-list">
          {records.map((record, index) => (
            <li key={`${title}-${index}`}>
              <details className="assets-trace-record">
                <summary>{traceRecordTitle(record, index)}</summary>
                <code>{formatRecord(record)}</code>
              </details>
            </li>
          ))}
        </ul>
      ) : (
        <div className="assets-empty-inline">{emptyText}</div>
      )}
    </div>
  );
}

function DrawerStat({
  label,
  value,
  detail,
  tone,
  warning = false,
  description,
}: {
  label: string;
  value: number | string;
  detail?: string;
  tone?: "connectivity" | "dataflow" | "reference" | "state";
  warning?: boolean;
  description?: string;
}) {
  return (
    <div className={`assets-drawer-stat${tone ? ` tone-${tone}` : ""}${warning ? " is-warning" : ""}`} title={description}>
      <span className="assets-drawer-stat-main">
        <span>{label}</span>
        <strong>{value}</strong>
      </span>
      {detail ? <small>{detail}</small> : <small>-</small>}
    </div>
  );
}

function assetDetailRows(asset: AssetInventoryItem, primaryRole: string) {
  const rows: Array<{ label: string; value: string; code?: boolean }> = [
    { label: "Canonical id", value: asset.id, code: true },
    { label: "Asset type", value: typeLabel(asset.asset_type) },
    { label: "Usage", value: primaryRole },
    { label: "Connection", value: asset.connection_name || "-" },
    { label: "Connection type", value: asset.connection_type || "-" },
  ];

  if (asset.asset_type === "sql_query") {
    appendDetail(rows, "Alias", assetAlias(asset), true);
    appendDetail(rows, "Format", asset.format);
    return rows;
  }

  if (asset.asset_type === "python_function") {
    appendDetail(rows, "Alias", assetAlias(asset), true);
    appendDetail(rows, "Python function", asset.python_function, true);
    appendDetail(rows, "Format", asset.format);
    return rows;
  }

  if (asset.asset_type === "api") {
    appendDetail(rows, "Format", asset.format);
    return rows;
  }

  appendDetail(rows, "Catalog", asset.catalog);
  appendDetail(rows, "Database", asset.database);
  appendDetail(rows, "Schema", asset.schema_name);
  appendDetail(rows, "Table", asset.table);
  appendDetail(rows, "Path", asset.path, true);
  appendDetail(rows, "Format", asset.format);
  return rows;
}

function splitRowsIntoColumns<T>(rows: T[], columnCount: number) {
  const safeColumnCount = Math.max(1, columnCount);
  const columnSize = Math.ceil(rows.length / safeColumnCount);
  return Array.from({ length: safeColumnCount }, (_, index) => rows.slice(index * columnSize, (index + 1) * columnSize))
    .filter((column) => column.length);
}

function appendDetail(rows: Array<{ label: string; value: string; code?: boolean }>, label: string, value: string | null | undefined, code = false) {
  if (!value) return;
  rows.push({ label, value, code });
}

function assetAlias(asset: AssetInventoryItem) {
  return [asset.schema_name, asset.table].filter(Boolean).join(".") || asset.table || null;
}

function assetDefinition(
  asset: AssetInventoryItem,
  detailDefinition: AssetDefinitionResponse | null,
  loading: boolean,
): AssetDefinitionResponse | null {
  if (detailDefinition) return detailDefinition;
  const query = asset.query?.trim();
  if (asset.asset_type === "sql_query" && query) {
    return {
      kind: "sql_query",
      language: "sql",
      status: "available",
      title: "SQL query",
      raw: query,
      formatted: query,
      line_count: lineCount(query),
      diagnostics: loading ? [{ severity: "info", code: "definition_loading", message: "Formatting SQL query..." }] : [],
    };
  }
  if (asset.asset_type === "python_function") {
    return {
      kind: "python_function",
      language: "python",
      status: loading ? "unavailable" : "empty",
      title: "Python function",
      function_path: asset.python_function,
      source: "",
      line_count: 0,
      diagnostics: loading
        ? [{ severity: "info", code: "definition_loading", message: "Resolving Python source..." }]
        : [],
    };
  }
  if (asset.asset_type === "api") {
    const endpoint = apiEndpoint(asset);
    return {
      kind: "api",
      status: endpoint ? "available" : "empty",
      title: "Endpoint",
      raw: endpoint || "",
      line_count: endpoint ? 1 : 0,
      diagnostics: [],
    };
  }
  if (asset.asset_type === "path" && asset.path && asset.path.length > 72) {
    return {
      kind: "path",
      status: "available",
      title: "Location",
      raw: asset.path,
      line_count: 1,
      diagnostics: [],
    };
  }
  if (asset.asset_type === "unresolved") {
    const attentionItems = asset.attention_items ?? [];
    return {
      kind: "unresolved",
      status: attentionItems.length ? "available" : "empty",
      title: "Resolution",
      raw: attentionItems.map((item) => `${item.severity}: ${item.message}`).join("\n"),
      line_count: attentionItems.length,
      diagnostics: attentionItems.map((item) => ({
        severity: item.severity,
        code: item.code,
        message: item.message,
        details: item.details,
      })),
    };
  }
  return null;
}

type DefinitionMode = "formatted" | "raw" | "source";

function definitionDefaultMode(definition: AssetDefinitionResponse): DefinitionMode {
  if (definition.source) return "source";
  if (definition.formatted) return "formatted";
  return "raw";
}

function definitionModes(definition: AssetDefinitionResponse): Array<{ key: DefinitionMode; label: string }> {
  const modes: Array<{ key: DefinitionMode; label: string }> = [];
  if (definition.formatted) modes.push({ key: "formatted", label: "Formatted" });
  if (definition.raw && definition.raw !== definition.formatted) modes.push({ key: "raw", label: "Raw" });
  if (definition.source) modes.push({ key: "source", label: "Source" });
  if (!modes.length && definition.raw) modes.push({ key: "raw", label: "Raw" });
  return modes;
}

function definitionContent(definition: AssetDefinitionResponse, mode: DefinitionMode) {
  if (mode === "source") return definition.source?.trim() || "";
  if (mode === "formatted") return definition.formatted?.trim() || definition.raw?.trim() || "";
  return definition.raw?.trim() || definition.formatted?.trim() || "";
}

function definitionTitle(definition: AssetDefinitionResponse) {
  return definition.title || humanize(definition.kind);
}

function definitionSummary(definition: AssetDefinitionResponse) {
  const status = definition.status && definition.status !== "available" ? humanize(definition.status) : null;
  if (definition.kind === "python_function") {
    return [
      definition.function_path,
      definition.relative_path ? `${definition.relative_path}${pythonLineRange(definition) ? `:${pythonLineRange(definition)}` : ""}` : null,
      lineLabel(definition.line_count),
      status,
    ].filter(Boolean).join(" · ") || "python";
  }
  if (definition.kind === "sql_query") {
    return ["sql", lineLabel(definition.line_count), status].filter(Boolean).join(" · ");
  }
  if (definition.kind === "api") {
    return [definition.raw, status].filter(Boolean).join(" · ") || "endpoint";
  }
  if (definition.kind === "path") {
    return [definition.raw, status].filter(Boolean).join(" · ") || "location";
  }
  return [lineLabel(definition.line_count), status].filter(Boolean).join(" · ") || "resolution context";
}

function definitionToolbarLabel(definition: AssetDefinitionResponse, mode: DefinitionMode) {
  if (definition.kind === "api") return "Endpoint";
  if (definition.kind === "path") return "Location";
  return humanize(mode);
}

function definitionIcon(kind: string) {
  if (kind === "sql_query") return <Code2 size={15} />;
  if (kind === "python_function") return <FileCode2 size={15} />;
  if (kind === "api") return <Braces size={15} />;
  if (kind === "path") return <MapPin size={15} />;
  return <Code2 size={15} />;
}

function definitionEmptyMessage(definition: AssetDefinitionResponse) {
  if (definition.kind === "python_function") return definition.function_path || "No Python source is available.";
  if (definition.kind === "sql_query") return "No SQL query is available.";
  if (definition.kind === "api") return "No endpoint is available.";
  if (definition.kind === "path") return "No location is available.";
  return "No resolution context is available.";
}

function definitionDetailRows(definition: AssetDefinitionResponse) {
  return [
    { label: "Type", value: humanize(definition.kind) },
    { label: "Status", value: humanize(definition.status) },
  ].filter((row) => row.value && row.value !== "-");
}

function pythonLineRange(definition: AssetDefinitionResponse) {
  const startLine = pickUnknownString(definition, "start_line");
  const endLine = pickUnknownString(definition, "end_line");
  if (startLine && endLine) return startLine === endLine ? startLine : `${startLine}-${endLine}`;
  return startLine || endLine;
}

function lineLabel(value: number | null | undefined) {
  if (!value) return null;
  return `${value} line${value === 1 ? "" : "s"}`;
}

function lineCount(value: string) {
  return value ? value.split(/\r?\n/).length : 0;
}

function apiEndpoint(asset: AssetInventoryItem) {
  if (asset.asset_type !== "api") return null;
  const identifiers = asset.identifiers ?? [];
  const endpointIdentifier = identifiers.find((identifier) => pickString(identifier, "kind") === "api_endpoint")
    ?? identifiers[0];
  return pickString(endpointIdentifier, "display_value")
    || pickString(endpointIdentifier, "normalized_value")
    || asset.display_name
    || null;
}

function pickString(record: Record<string, unknown> | undefined, key: string) {
  const value = record?.[key];
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function pickUnknownString(record: Record<string, unknown> | undefined, key: string) {
  const value = record?.[key];
  if (typeof value === "string" && value.trim()) return value.trim();
  if (typeof value === "number") return String(value);
  return null;
}

function sourceTail(uri: string) {
  const normalized = uri.replace(/\\/g, "/");
  return normalized.split("/").filter(Boolean).pop() || uri;
}

function traceRecordTitle(record: Record<string, unknown>, index: number) {
  const kind = pickString(record, "kind") || pickString(record, "type") || `record ${index + 1}`;
  const value = pickString(record, "display_value")
    || pickString(record, "normalized_value")
    || pickString(record, "value")
    || pickString(record, "name");
  if (value) return `${humanize(kind)} · ${value}`;
  const sourceType = pickString(record, "source_type");
  const role = pickString(record, "role");
  const sourceId = record.metadata_source_id;
  if (sourceType || role || sourceId != null) {
    return [
      sourceType ? humanize(sourceType) : humanize(kind),
      sourceId != null ? `#${String(sourceId)}` : "",
      role ? humanize(role) : "",
    ].filter(Boolean).join(" · ");
  }
  return value ? `${humanize(kind)} · ${value}` : humanize(kind);
}

function formatRecord(record: Record<string, unknown>) {
  return JSON.stringify(record);
}

function assetBriefTitle(asset: AssetBrief) {
  return asset.friendly_name || asset.display_name || "-";
}

function assetBriefSubtitle(asset: AssetBrief) {
  return asset.full_identity || asset.connection_name || asset.id;
}

function AssetRowSubtitle({ asset }: { asset: AssetBrief }) {
  const subtitle = assetBriefSubtitle(asset);
  return <ConnectionSubtitle connectionName={asset.connection_name} title={assetBriefTooltip(asset)} value={subtitle} />;
}

function ReferenceRowSubtitle({ connectionName, value }: { connectionName?: string | null; value: string }) {
  return <ConnectionSubtitle connectionName={connectionName} value={value} />;
}

function ConnectionSubtitle({
  connectionName,
  value,
  title,
}: {
  connectionName?: string | null;
  value: string;
  title?: string;
}) {
  const connection = connectionName?.trim() || "";
  const connectionIndex = connection ? value.indexOf(connection) : -1;
  if (connectionIndex < 0) return <small title={title}>{value}</small>;
  const prefix = value.slice(0, connectionIndex);
  const suffix = value.slice(connectionIndex + connection.length);
  return (
    <span className="assets-neighbor-subtitle" title={title}>
      {prefix ? <span className="assets-connection-prefix">{prefix}</span> : null}
      <span className="assets-connection-name">{connection}</span>
      {suffix ? <span className="assets-connection-suffix">{suffix}</span> : null}
    </span>
  );
}

function assetBriefTooltip(asset: AssetBrief) {
  return [asset.full_identity, asset.connection_name, `id: ${asset.id}`].filter(Boolean).join("\n");
}

function assetBriefIconKind(asset: AssetBrief): AssetIconKind {
  return assetIconKind(assetBriefFormat(asset));
}

function assetBriefBadge(asset: AssetBrief) {
  return compactHumanize(assetBriefFormat(asset)).toUpperCase();
}

function assetBriefFormat(asset: AssetBrief) {
  return asset.format || String(asset.asset_type || "asset");
}

function typeLabel(value: string | null | undefined) {
  return compactHumanize(value).toLocaleLowerCase();
}

function humanize(value: string | null | undefined) {
  if (!value) return "-";
  return value.replace(/_/g, " ");
}

function compactHumanize(value: string | null | undefined) {
  return humanize(value)
    .replace(/\bpython sql\b/giu, "py_sql")
    .replace(/\bpython function\b/giu, "py_function")
    .replace(/\bsql query\b/giu, "sql_query")
    .replace(/\bpython\b/giu, "py")
    .replace(/\s+/gu, "_");
}

function positionFromCounts(upstreamCount: number, downstreamCount: number) {
  if (upstreamCount === 0 && downstreamCount > 0) return "entry";
  if (upstreamCount > 0 && downstreamCount > 0) return "transit";
  if (upstreamCount > 0 && downstreamCount === 0) return "exit";
  return "isolated";
}

function dependsOnTitle(item: AssetDependsOnItem) {
  if (item.source_reference) {
    return item.source_reference.display_name || item.source_reference.raw_value || "reference";
  }
  if (item.resolved_asset) {
    return item.resolved_asset.friendly_name || item.resolved_asset.display_name || "-";
  }
  return "reference";
}

function dependsOnSubtitle(item: AssetDependsOnItem) {
  if (item.resolved_asset) {
    const connection = item.resolved_asset.connection_name || "unknown connection";
    const assetName = item.resolved_asset.friendly_name || item.resolved_asset.display_name || item.resolved_asset.id;
    return `mapped to ${connection} . ${assetName}`;
  }
  return "unmapped reference";
}

function ReferenceMeta({
  kind,
  provenance,
  status,
}: {
  kind: string | null | undefined;
  provenance: string | null | undefined;
  status: string | null | undefined;
}) {
  const parts = [
    { label: humanize(provenance), tone: "provenance" },
    { label: humanize(kind), tone: "kind" },
    { label: humanize(status), tone: `status-${referenceStatusTone(status)}` },
  ].filter((part) => part.label !== "-");
  return (
    <span className="assets-reference-meta">
      {parts.map((part, index) => (
        <span key={`${part.tone}-${part.label}`}>
          {index ? <span className="assets-reference-meta-separator"> · </span> : null}
          <span className={`assets-reference-meta-${part.tone}`}>{part.label}</span>
        </span>
      ))}
    </span>
  );
}

function referenceStatusTone(status: string | null | undefined) {
  const normalized = String(status || "").toLocaleLowerCase();
  if (normalized.includes("unresolved") || normalized.includes("missing")) return "attention";
  if (normalized.includes("resolved")) return "resolved";
  return "default";
}

function usedBySubtitle(connectionName: string, referenceLabel: string | null) {
  if (!referenceLabel) return connectionName || "-";
  const parts = [connectionName || "-", referenceLabel];
  return parts.join(" . ");
}
