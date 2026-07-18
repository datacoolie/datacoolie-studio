import { Check, Search, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { AssetInventoryItem, AssetReferenceGroupItem, ProjectReferenceMapping } from "../../shared/api/types";
import { LineageFormatIcon } from "../lineage/components/LineageFormatIcon";
import { assetIconKind, assetTypeTone } from "../lineage/model/presentation";
import {
  buildReferenceMappingPayload,
  buildReferenceMappingTargets,
  filterReferenceMappingTargets,
  findReferenceMapping,
  mappingTargetForAssetId,
  mappingTargetForMapping,
  referenceMappingAction,
  referenceMappingActionLabel,
  type ReferenceMappingPayload,
  type ReferenceMappingTarget,
} from "./referenceMappingModel";

interface ReferenceMappingEditorProps {
  reference: AssetReferenceGroupItem;
  assets: AssetInventoryItem[];
  mappings: ProjectReferenceMapping[];
  busy?: boolean;
  onCreate: (payload: ReferenceMappingPayload) => Promise<unknown>;
  onUpdate: (mappingId: number, payload: ReferenceMappingPayload) => Promise<unknown>;
  onDelete: (mappingId: number) => Promise<unknown>;
  onRefresh: () => Promise<void>;
  onBack: () => void;
  onSearchTargets?: (query: string, connectionName: string) => Promise<AssetInventoryItem[]>;
}

export function ReferenceMappingEditor({
  reference,
  assets,
  mappings,
  busy = false,
  onCreate,
  onUpdate,
  onDelete,
  onRefresh,
  onBack,
  onSearchTargets,
}: ReferenceMappingEditorProps) {
  const action = referenceMappingAction(reference, mappings);
  const mapping = mappings.find((item) => item.id === reference.manual_mapping?.mapping_id)
    || findReferenceMapping(reference, mappings);
  const [remoteAssets, setRemoteAssets] = useState<AssetInventoryItem[]>([]);
  const [targetsLoading, setTargetsLoading] = useState(false);
  const targetAssets = useMemo(() => {
    if (!onSearchTargets) return assets;
    return [...new Map([...assets, ...remoteAssets].map((asset) => [asset.id, asset])).values()];
  }, [assets, onSearchTargets, remoteAssets]);
  const targets = useMemo(() => buildReferenceMappingTargets(targetAssets), [targetAssets]);
  const currentTarget = mappingTargetForMapping(mapping, targets);
  const resolvedTarget = mappingTargetForAssetId(reference.resolved_asset_id, targets);
  const [query, setQuery] = useState("");
  const [connectionName, setConnectionName] = useState("");
  const [selectedTarget, setSelectedTarget] = useState<ReferenceMappingTarget | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    setSelectedTarget(currentTarget || (action === "complete" ? resolvedTarget : null));
    setNote(mapping?.note || "");
    setQuery("");
    setConnectionName("");
    setError(null);
    setConfirmRemove(false);
  }, [action, currentTarget, mapping?.id, mapping?.note, reference.id, resolvedTarget]);

  useEffect(() => {
    if (!onSearchTargets) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setTargetsLoading(true);
      void onSearchTargets(query, connectionName)
        .then((items) => {
          if (!cancelled) setRemoteAssets(items);
        })
        .catch(() => {
          if (!cancelled) setRemoteAssets([]);
        })
        .finally(() => {
          if (!cancelled) setTargetsLoading(false);
        });
    }, 200);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [connectionName, onSearchTargets, query]);

  const connectionOptions = useMemo(
    () => [...new Set(targets.map((target) => target.connectionName))].sort((left, right) => left.localeCompare(right)),
    [targets],
  );
  const visibleTargets = useMemo(
    () => filterReferenceMappingTargets(reference, targets, { query, connectionName }),
    [connectionName, query, reference, targets],
  );

  const canSave = Boolean(selectedTarget) && !saving && !busy;
  const actionLabel = referenceMappingActionLabel(action);
  const impact = `${reference.occurrence_ids.length || reference.dependency_count} occurrences · ${reference.consumer_asset_ids.length} consumers`;

  async function save() {
    if (!selectedTarget) {
      setError("Choose a target asset before saving.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const payload = buildReferenceMappingPayload(reference, selectedTarget, note);
      if (mapping) await onUpdate(mapping.id, payload);
      else await onCreate(payload);
      await onRefresh();
      onBack();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Mapping could not be saved.");
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    if (!mapping) return;
    if (!confirmRemove) {
      setConfirmRemove(true);
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await onDelete(mapping.id);
      await onRefresh();
      onBack();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Mapping could not be removed.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="reference-mapping-editor">
      <section className="reference-mapping-summary" aria-label="Mapping scope">
        <span>Mapping</span>
        <strong>{compactReferenceType(reference.reference_type)} · {reference.display_name}</strong>
        <small>Project mapping · {impact}</small>
      </section>

      <>
          {action === "repair" && mapping ? (
            <section className="reference-mapping-warning">
              <strong>Mapped target is missing in this environment.</strong>
              <span>{mapping.target_display_value || mapping.target_normalized_value}</span>
            </section>
          ) : null}
          {action === "complete" && resolvedTarget ? (
            <section className="reference-mapping-hint">
              <strong>{resolvedTarget.displayName}</strong>
              <span>Preselected to complete the unresolved occurrences without creating a mixed result.</span>
            </section>
          ) : null}

          <div className="reference-mapping-target-controls">
            <label className="reference-mapping-search">
              <span>Target asset</span>
              <span className="reference-mapping-search-input">
                <Search size={14} aria-hidden="true" />
                <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search canonical assets" />
              </span>
            </label>
            <label>
              <span>Connection</span>
              <select value={connectionName} onChange={(event) => setConnectionName(event.target.value)}>
                <option value="">All connections</option>
                {connectionOptions.map((connection) => <option key={connection} value={connection}>{connection}</option>)}
              </select>
            </label>
          </div>

          <div className="reference-mapping-target-list" role="listbox" aria-label="Target assets">
            {visibleTargets.map((target) => {
              const selected = target.id === selectedTarget?.id;
              const candidate = reference.candidate_asset_ids.includes(target.assetId);
              return (
                <button
                  key={target.id}
                  className={`reference-mapping-target${selected ? " is-selected" : ""}`}
                  type="button"
                  role="option"
                  aria-selected={selected}
                  onClick={() => setSelectedTarget(target)}
                >
                  <span className={`assets-asset-icon asset-tone-${assetTypeTone(target.assetType)}`} aria-hidden="true">
                    <LineageFormatIcon kind={assetIconKind(target.format || target.assetType)} label={target.assetType} size={14} />
                  </span>
                  <span className="reference-mapping-target-copy">
                    <strong>{target.displayName}</strong>
                    <small>
                      <span className={`reference-mapping-target-connection asset-tone-${assetTypeTone(target.assetType)}`}>{target.connectionName}</span>
                      {target.context ? <><span className="reference-mapping-target-separator"> · </span><span>{target.context}</span></> : null}
                    </small>
                  </span>
                  {candidate ? <em>candidate</em> : null}
                  {selected ? <Check size={15} aria-label="Selected" /> : <span className="reference-mapping-target-spacer" aria-hidden="true" />}
                </button>
              );
            })}
            {targetsLoading ? <div className="assets-empty-inline">Loading targets...</div> : null}
            {!targetsLoading && !visibleTargets.length ? <div className="assets-empty-inline">No canonical asset matches this search.</div> : null}
          </div>

          <label className="reference-mapping-note">
            <span>Note</span>
            <input value={note} onChange={(event) => setNote(event.target.value)} placeholder="Optional mapping rationale" />
          </label>
        </>

      {error ? <div className="reference-mapping-error" role="alert">{error}</div> : null}

      <footer className="reference-mapping-actions">
          {mapping ? (
            <button className={confirmRemove ? "text-action danger confirm" : "text-action danger"} type="button" disabled={saving || busy} onClick={() => void remove()}>
              <Trash2 size={14} />
              {confirmRemove ? "Confirm reset" : "Reset to automatic"}
            </button>
          ) : <span />}
          <div>
            <button className="text-action" type="button" disabled={saving || busy} onClick={onBack}>Cancel</button>
            <button className="text-action primary" type="button" disabled={!canSave} onClick={() => void save()}>
              {saving ? "Saving..." : mapping ? "Update mapping" : `${actionLabel || "Save"} mapping`}
            </button>
          </div>
      </footer>
    </div>
  );
}

function compactReferenceType(value: string) {
  return value.replace(/_reference$/, "").replace(/_/g, " ");
}
