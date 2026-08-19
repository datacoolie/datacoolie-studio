import { ArrowLeft, Check, ChevronDown, ChevronRight, Copy, Database, Loader2, X } from "lucide-react";
import { Icon } from "@iconify/react";
import { createPortal } from "react-dom";
import { useEffect, useState } from "react";
import type { AssetBrief, AssetInventoryItem, AssetReferenceGroupItem, AssetReferenceOccurrenceItem, ReferenceOccurrenceSourceResponse } from "../../shared/api/domainTypes";
import { presentReferenceResolution, type ReferenceResolutionPresentation } from "../../shared/referenceResolutionPresentation";
import { useDrawerEscape } from "../../shared/hooks/useDrawerEscape";
import { metadataNavigationTarget, type MetadataNavigationTarget } from "../../shared/metadataNavigation";
import { LineageFormatIcon } from "../lineage/components/LineageFormatIcon";
import { assetIconKind, assetTypeIconId, assetTypeTone, referenceTypeAssetType } from "../lineage/model/presentation";
import { ReferenceMappingDrawer } from "../reference-mappings/ReferenceMappingDrawer";
import { referenceMappingAction, referenceMappingActionLabel, type ReferenceMappingPayload } from "../reference-mappings/referenceMappingModel";
import {
  groupReferenceUsage,
  occurrenceLocationLabel,
  occurrenceResolutionMethod,
  occurrenceScopeLabel,
  plural,
  referenceResolutionStory,
  shouldShowNormalizedValue,
} from "./referenceDrawerModel";
import { referenceResolutionPresentation } from "./assetsPresentation";
import { SourceCodeViewer } from "./SourceCodeViewer";

interface ReferenceDrawerProps {
  environmentId: number;
  reference: AssetReferenceGroupItem;
  occurrences: AssetReferenceOccurrenceItem[];
  mappingMode: boolean;
  assets: AssetInventoryItem[];
  mappingBusy?: boolean;
  canGoBack: boolean;
  onBack: () => void;
  onClose: () => void;
  onSelectAsset: (assetId: string) => void;
  onOpenMetadata: (target: MetadataNavigationTarget) => void;
  onOpenReferenceMapping: (reference: AssetReferenceGroupItem) => void;
  onCreateReferenceMapping: (payload: ReferenceMappingPayload) => Promise<unknown>;
  onUpdateReferenceMapping: (mappingId: number, payload: ReferenceMappingPayload) => Promise<unknown>;
  onDeleteReferenceMapping: (mappingId: number) => Promise<unknown>;
  onRefreshReferenceMappings: () => Promise<void>;
  onSearchMappingTargets?: (query: string, connectionName: string) => Promise<AssetInventoryItem[]>;
  onLoadOccurrenceSource: (environmentId: number, occurrenceId: string) => Promise<ReferenceOccurrenceSourceResponse>;
  onNavigateAway?: () => void;
}

export function ReferenceDrawer({
  environmentId,
  reference,
  occurrences,
  mappingMode,
  assets,
  mappingBusy,
  canGoBack,
  onBack,
  onClose,
  onSelectAsset,
  onOpenMetadata,
  onOpenReferenceMapping,
  onCreateReferenceMapping,
  onUpdateReferenceMapping,
  onDeleteReferenceMapping,
  onRefreshReferenceMappings,
  onSearchMappingTargets,
  onLoadOccurrenceSource,
  onNavigateAway,
}: ReferenceDrawerProps) {
  const story = referenceResolutionStory(reference, occurrences);
  const resolution = referenceResolutionPresentation(reference);
  const mappingAction = referenceMappingAction(reference);
  const mappingActionLabel = referenceMappingActionLabel(mappingAction);
  const usageGroups = groupReferenceUsage(reference, occurrences);
  const effectiveMapping = reference.manual_mapping ?? null;
  const resolvedTarget = reference.resolved_asset || null;
  const resolvedTargetState = resolution.state === "automatic" || resolution.state === "manual" ? resolution.state : null;
  const candidates = reference.candidate_assets.filter((candidate) => candidate.id !== resolvedTarget?.id);
  const missingMappedTarget = !resolvedTarget ? reference.manual_mapping?.target_normalized_value || null : null;
  const showTargetSection = Boolean(resolvedTarget || candidates.length || missingMappedTarget);
  const targetSectionTitle = resolvedTarget
    ? candidates.length ? "Target and candidates" : "Target"
    : missingMappedTarget
      ? candidates.length ? "Mapped target and candidates" : "Mapped target"
      : "Candidates";
  const targetSectionCount = (resolvedTarget || missingMappedTarget ? 1 : 0) + candidates.length;
  const [expandedOccurrenceIds, setExpandedOccurrenceIds] = useState<Set<string>>(() => new Set());
  const [sourceByOccurrenceId, setSourceByOccurrenceId] = useState<Record<string, ReferenceOccurrenceSourceResponse>>({});
  const [sourceLoadingIds, setSourceLoadingIds] = useState<Set<string>>(() => new Set());
  const [sourceErrors, setSourceErrors] = useState<Record<string, string>>({});
  const [copied, setCopied] = useState(false);

  useDrawerEscape(onClose);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1200);
    return () => window.clearTimeout(timer);
  }, [copied]);

  async function copyReferenceId() {
    if (!navigator.clipboard?.writeText) return;
    await navigator.clipboard.writeText(reference.id);
    setCopied(true);
  }

  function toggleOccurrenceSource(occurrenceId: string) {
    const opening = !expandedOccurrenceIds.has(occurrenceId);
    setExpandedOccurrenceIds((current) => {
      const next = new Set(current);
      if (opening) next.add(occurrenceId);
      else next.delete(occurrenceId);
      return next;
    });
    if (!opening || sourceByOccurrenceId[occurrenceId] || sourceLoadingIds.has(occurrenceId)) return;
    setSourceLoadingIds((current) => new Set(current).add(occurrenceId));
    setSourceErrors((current) => {
      const next = { ...current };
      delete next[occurrenceId];
      return next;
    });
    void onLoadOccurrenceSource(environmentId, occurrenceId)
      .then((source) => setSourceByOccurrenceId((current) => ({ ...current, [occurrenceId]: source })))
      .catch((error: unknown) => setSourceErrors((current) => ({
        ...current,
        [occurrenceId]: error instanceof Error ? error.message : "Source preview could not be loaded.",
      })))
      .finally(() => setSourceLoadingIds((current) => {
        const next = new Set(current);
        next.delete(occurrenceId);
        return next;
      }));
  }

  return createPortal(
    <div className="metadata-drawer-backdrop assets-detail-backdrop" onMouseDown={onClose}>
      <aside className="metadata-drawer assets-drawer" aria-labelledby="reference-drawer-title" onMouseDown={(event) => event.stopPropagation()}>
        <header className="metadata-drawer-header assets-drawer-header">
          {canGoBack ? (
            <button className="icon-action small assets-drawer-back-icon" type="button" title="Back" aria-label="Back" onClick={onBack}>
              <ArrowLeft size={14} />
            </button>
          ) : null}
          <div className="assets-drawer-title reference-drawer-title">
            <span className="eyebrow">{mappingMode ? "Reference mapping" : "Reference"}</span>
            <div className="assets-drawer-title-line">
              <h2 id="reference-drawer-title">
                <span className={`assets-asset-icon asset-tone-${assetTypeTone(referenceTypeAssetType(reference.reference_type))}`} aria-hidden="true">
                  <Icon icon={assetTypeIconId(referenceTypeAssetType(reference.reference_type))} width={18} height={18} />
                </span>
                <span>{reference.display_name}</span>
              </h2>
              <button className={`icon-action small assets-copy-icon${copied ? " copied" : ""}`} type="button" title={copied ? "Copied" : "Copy id"} aria-label={copied ? "Copied" : "Copy reference id"} onClick={() => void copyReferenceId()}>
                {copied ? <Check size={14} /> : <Copy size={14} />}
              </button>
            </div>
            {shouldShowNormalizedValue(reference) ? <small title={reference.normalized_value}>{reference.normalized_value}</small> : null}
            <div className="assets-drawer-header-chips">
              <span className="assets-header-chip">{compactHumanize(reference.reference_type)}</span>
              <span className="assets-header-chip">{reference.provenances.length ? reference.provenances.map(compactHumanize).join(", ") : "unknown source"}</span>
              <ResolutionBadge presentation={resolution} />
            </div>
          </div>
          <div className="assets-drawer-header-actions">
            <button className="icon-action small" type="button" title="Close" aria-label="Close reference drawer" onClick={onClose}>
              <X size={14} />
            </button>
          </div>
        </header>

        {mappingMode ? (
          <div className="metadata-drawer-body assets-drawer-body">
            <ReferenceMappingDrawer
              reference={reference}
              assets={assets}
              busy={mappingBusy}
              onCreate={onCreateReferenceMapping}
              onUpdate={onUpdateReferenceMapping}
              onDelete={onDeleteReferenceMapping}
              onRefresh={onRefreshReferenceMappings}
              onSearchTargets={onSearchMappingTargets}
              onBack={onBack}
            />
          </div>
        ) : (
          <div className="metadata-drawer-body assets-drawer-body reference-drawer-body">
            <section className={`reference-resolution-summary tone-${story.tone}`} aria-labelledby="reference-resolution-title">
              <div className="reference-resolution-copy">
                <span className="eyebrow">Resolution</span>
                <h3 id="reference-resolution-title">{story.title}</h3>
                <p>{story.detail}</p>
              </div>
              <div className="reference-resolution-actions">
                {mappingActionLabel ? (
                  <button className="text-action primary" type="button" onClick={() => onOpenReferenceMapping(reference)}>
                    {mappingActionLabel}
                  </button>
                ) : null}
                <button
                  className="text-action assets-metadata-action"
                  type="button"
                  onClick={() => {
                    onOpenMetadata(metadataNavigationTarget(occurrences.flatMap((occurrence) => occurrence.dataflow_ids), reference.normalized_value));
                    onNavigateAway?.();
                  }}
                >
                  <Database size={14} />
                  Open in Metadata
                </button>
              </div>
            </section>

            {showTargetSection ? (
              <section className="reference-drawer-section" aria-labelledby="reference-target-title">
                <SectionTitle id="reference-target-title" count={targetSectionCount} title={targetSectionTitle} />
                <div className="reference-object-list">
                  {resolvedTarget ? (
                    <ReferenceAssetRow
                      asset={resolvedTarget}
                      meta={effectiveMapping ? "manual · project mapping" : "automatic resolution"}
                      highlightState={resolvedTargetState}
                      onSelect={onSelectAsset}
                    />
                  ) : null}
                  {missingMappedTarget ? (
                    <div className="reference-missing-target">
                      <strong>{missingMappedTarget}</strong>
                      <span>mapped target · missing in this environment</span>
                    </div>
                  ) : null}
                  {candidates.map((candidate) => (
                    <ReferenceAssetRow key={candidate.id} asset={candidate} meta="candidate" onSelect={onSelectAsset} />
                  ))}
                </div>
                {reference.manual_mapping?.note ? <p className="reference-mapping-note-copy">{reference.manual_mapping.note}</p> : null}
              </section>
            ) : null}

            <section className="reference-drawer-section reference-usage-section" aria-labelledby="reference-usage-title">
              <SectionTitle id="reference-usage-title" count={occurrences.length} title="Usage evidence" />
              {usageGroups.length ? (
                <div className="reference-usage-groups">
                  {usageGroups.map((group) => (
                    <div className={`reference-usage-group tone-${assetTypeTone(group.consumer?.asset_type || "default")}`} key={group.id}>
                      {group.consumer ? (
                        <ReferenceAssetRow
                          asset={group.consumer}
                          meta={plural(group.occurrences.length, "detection")}
                          onSelect={onSelectAsset}
                        />
                      ) : <div className="reference-usage-unknown">Unknown consumer</div>}
                      {group.occurrences.length ? (
                        <div className="reference-occurrence-list">
                          {group.occurrences.map((occurrence) => (
                            <OccurrenceEvidenceRow
                              key={occurrence.id}
                              occurrence={occurrence}
                              canonicalValue={reference.normalized_value}
                              showStatus={reference.resolution.state === "unresolved"}
                              expanded={expandedOccurrenceIds.has(occurrence.id)}
                              loading={sourceLoadingIds.has(occurrence.id)}
                              source={sourceByOccurrenceId[occurrence.id] || null}
                              error={sourceErrors[occurrence.id] || null}
                              onToggle={() => toggleOccurrenceSource(occurrence.id)}
                            />
                          ))}
                        </div>
                      ) : <small className="reference-usage-empty">No detection detail is available.</small>}
                    </div>
                  ))}
                </div>
              ) : <div className="assets-empty-inline">No usage evidence is linked to this reference.</div>}
            </section>

            <TechnicalTrace reference={reference} occurrences={occurrences} />
          </div>
        )}
      </aside>
    </div>,
    document.body,
  );
}

function ReferenceAssetRow({ asset, meta, highlightState, onSelect }: {
  asset: AssetBrief;
  meta: string;
  highlightState?: "automatic" | "manual" | null;
  onSelect: (assetId: string) => void;
}) {
  return (
    <button className={`reference-object-row${highlightState ? ` is-resolved-target status-${highlightState}` : ""}`} type="button" onClick={() => onSelect(asset.id)}>
      <span className={`assets-asset-icon asset-tone-${assetTypeTone(asset.asset_type)}`} aria-hidden="true">
        <LineageFormatIcon kind={assetIconKind(asset.format || asset.asset_type)} label={asset.asset_type} size={16} />
      </span>
      <span className="reference-object-copy">
        <strong>{asset.friendly_name || asset.display_name}</strong>
        <small>{asset.full_identity || asset.connection_name || asset.id}</small>
      </span>
      <span className="reference-object-meta">{meta}</span>
      <ChevronRight size={14} aria-hidden="true" />
    </button>
  );
}

function OccurrenceEvidenceRow({ occurrence, canonicalValue, showStatus, expanded, loading, source, error, onToggle }: {
  occurrence: AssetReferenceOccurrenceItem;
  canonicalValue: string;
  showStatus: boolean;
  expanded: boolean;
  loading: boolean;
  source: ReferenceOccurrenceSourceResponse | null;
  error: string | null;
  onToggle: () => void;
}) {
  const location = occurrenceLocationLabel(occurrence);
  const context = [compactHumanize(occurrence.provenance), occurrenceScopeLabel(occurrence), occurrenceResolutionMethod(occurrence)].filter(Boolean).join(" · ");
  const rawDiffers = normalizeText(occurrence.raw_value) !== normalizeText(canonicalValue);
  return <div className={`reference-occurrence-evidence${expanded ? " is-expanded" : ""}`}>
    <div className="reference-occurrence-row is-static">
      <span className="reference-occurrence-copy">
        <strong>{location || "Detected reference"}</strong>
        <small>{context}</small>
        {rawDiffers ? <code title={occurrence.raw_value}>{occurrence.raw_value}</code> : null}
      </span>
      <button className="icon-action small reference-occurrence-expand" type="button" title={expanded ? "Hide detail" : "Show detail"} aria-label={expanded ? "Hide detail" : "Show detail"} aria-expanded={expanded} onClick={onToggle}>
        {loading ? <Loader2 className="is-spinning" size={13} /> : <ChevronDown size={13} />}
      </button>
      {showStatus ? <ResolutionBadge presentation={occurrenceResolutionPresentation(occurrence.resolution)} compact /> : null}
    </div>
    {expanded ? <>
      <OccurrenceSourcePreview source={source} error={error} loading={loading} />
    </> : null}
  </div>;
}

function OccurrenceSourcePreview({ source, error, loading }: { source: ReferenceOccurrenceSourceResponse | null; error: string | null; loading: boolean }) {
  const [viewId, setViewId] = useState<string | null>(null);
  useEffect(() => {
    setViewId(source?.views[0]?.id || null);
  }, [source]);
  if (loading) return <div className="reference-source-preview is-loading"><Loader2 className="is-spinning" size={15} />Loading source preview</div>;
  if (error) return <div className="reference-source-preview is-error">{error}</div>;
  if (!source) return null;
  const selectedView = source.views.find((item) => item.id === viewId) || source.views[0] || null;
  return (
    <div className="reference-source-preview">
      {source.views.length ? (
        <>
          <div className="reference-source-preview-header">
            {source.views.length > 1 ? (
              <div className="assets-definition-tabs" role="tablist" aria-label="Source view">
                {source.views.map((view) => <button key={view.id} className={view.id === selectedView?.id ? "is-active" : ""} type="button" role="tab" aria-selected={view.id === selectedView?.id} onClick={() => setViewId(view.id)}>{view.label}</button>)}
              </div>
            ) : <strong>{selectedView?.label}</strong>}
            {selectedView ? <small>{sourceContext(selectedView)}</small> : null}
          </div>
          {selectedView ? <SourceCodeViewer content={selectedView.content} language={selectedView.language} matches={selectedView.matches} defaultWrapped ariaLabel={`${selectedView.label} source`} /> : null}
        </>
      ) : <div className="reference-source-empty">No source preview is available for this detection.</div>}
      {source.diagnostics.length ? <div className="reference-source-diagnostics">{source.diagnostics.map((diagnostic) => <small key={`${diagnostic.code}-${diagnostic.message}`}>{diagnostic.message}</small>)}</div> : null}
    </div>
  );
}

function sourceContext(view: ReferenceOccurrenceSourceResponse["views"][number]) {
  const match = view.matches[0];
  const precision = match?.precision === "exact_reference" ? "reference match" : match?.precision === "detection_expression" ? "detection expression" : "location";
  return [view.function_path || view.path, match ? `${precision} · ${match.line}:${match.column + 1}` : null].filter(Boolean).join(" · ");
}

function TechnicalTrace({ reference, occurrences }: { reference: AssetReferenceGroupItem; occurrences: AssetReferenceOccurrenceItem[] }) {
  const occurrenceObservations = occurrences
    .filter((occurrence) => occurrence.observations.length)
    .map((occurrence) => ({ occurrence_id: occurrence.id, observations: occurrence.observations }));
  return (
    <details className="reference-technical-trace">
      <summary>
        <span className="reference-technical-summary-title"><ChevronRight className="reference-technical-chevron" size={14} aria-hidden="true" />Technical trace</span>
        <small>Reference and occurrence IDs</small>
      </summary>
      <div className="reference-technical-content">
        <dl>
          <div><dt>Reference ID</dt><dd><code>{reference.id}</code></dd></div>
          <div><dt>Canonical key</dt><dd><code>{reference.reference_type} · {reference.normalized_value}</code></dd></div>
          <div><dt>Mapping</dt><dd>{reference.manual_mapping?.mapping_id ? `#${reference.manual_mapping.mapping_id} · project scope` : "-"}</dd></div>
          {reference.manual_mapping?.target_normalized_value ? <div><dt>Mapping target</dt><dd><code>{[reference.manual_mapping.target_identifier_kind, reference.manual_mapping.target_normalized_value].filter(Boolean).join(" · ")}</code></dd></div> : null}
        </dl>
        {occurrences.length ? (
          <div className="reference-trace-ids">
            <strong>Occurrence IDs</strong>
            {occurrences.map((occurrence) => <code key={occurrence.id}>{occurrence.id}</code>)}
          </div>
        ) : null}
        {occurrenceObservations.length ? (
          <div className="reference-trace-observations">
            <strong>Observations</strong>
            <SourceCodeViewer className="is-light" content={JSON.stringify(occurrenceObservations, null, 2)} language="json" ariaLabel="Occurrence observations" />
          </div>
        ) : null}
      </div>
    </details>
  );
}


function SectionTitle({ id, title, count }: { id: string; title: string; count: number }) {
  return <h3 id={id}>{title} <span>{count}</span></h3>;
}

function ResolutionBadge({ presentation, compact = false }: { presentation: ReferenceResolutionPresentation; compact?: boolean }) {
  return <span className={`assets-status-chip status-${presentation.state}${compact ? " is-compact" : ""}`}>{presentation.label}</span>;
}

function occurrenceResolutionPresentation(
  resolution: AssetReferenceOccurrenceItem["resolution"],
): ReferenceResolutionPresentation {
  return presentReferenceResolution(resolution);
}

function humanize(value: string | null | undefined) {
  return value?.replace(/_/gu, " ") || "-";
}

function compactHumanize(value: string | null | undefined) {
  return humanize(value)
    .replace(/\bpython sql\b/giu, "py_sql")
    .replace(/\bpython function\b/giu, "py_function")
    .replace(/\bsql query\b/giu, "sql_query")
    .replace(/\bpython\b/giu, "py")
    .replace(/\s+/gu, "_");
}

function normalizeText(value: string | null | undefined) {
  return String(value || "").trim().toLocaleLowerCase().replace(/\\/gu, "/").replace(/\/+$/gu, "");
}
