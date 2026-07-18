import { AlertTriangle, RefreshCw } from "lucide-react";
import { Icon } from "@iconify/react";
import { useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from "react";
import { api } from "../../shared/api/client";
import type { AssetInventoryItem, AssetReferenceGroupItem, Environment, ProjectReferenceMapping } from "../../shared/api/types";
import { EmptyState } from "../../shared/components/EmptyState";
import { assetTypeIconId, assetTypeTone, referenceTypeAssetType } from "../lineage/model/presentation";
import { buildReferenceMappingPayload, type ReferenceMappingPayload } from "../reference-mappings/referenceMappingModel";
import { ProjectReferenceMappingDrawer } from "./ProjectReferenceMappingDrawer";
import { ProjectReferenceMappingTargetPicker } from "./ProjectReferenceMappingTargetPicker";
import {
  buildProjectMappingRegistry,
  canCreateProjectMapping,
  canEditProjectMapping,
  projectMappingResolutionSummary,
  projectMappingInitialTargetId,
  projectMappingStateLabel,
  projectMappingTargetBusinessKey,
  projectMappingTargetLabel,
  type ProjectAssetsSnapshot,
  type ProjectReferenceRegistryRow,
  type ProjectMappingState,
} from "./projectReferenceMappingRegistryModel";

interface ProjectReferenceMappingsPageProps {
  projectId: number | null;
  projectName: string | null;
  environments: Environment[];
  mappings: ProjectReferenceMapping[];
  busy: boolean;
  routeSearch?: string;
  onReload: () => Promise<void>;
  onCreate: (payload: ReferenceMappingPayload) => Promise<unknown>;
  onUpdate: (mappingId: number, payload: ReferenceMappingPayload) => Promise<unknown>;
  onDelete: (mappingId: number) => Promise<unknown>;
}

type MappingFilter = "all" | "needs" | "manual" | "automatic" | "coverage" | "saved";

interface EnvironmentLoadFailure {
  environmentId: number;
  environmentName: string;
  message: string;
}

interface ProjectMappingDraft {
  rowId: string;
  targetId: string | null;
  note: string;
}

interface MappingActionError {
  rowId: string;
  message: string;
}

export function ProjectReferenceMappingsPage({
  projectId,
  projectName,
  environments,
  mappings,
  busy,
  routeSearch,
  onReload,
  onCreate,
  onUpdate,
  onDelete,
}: ProjectReferenceMappingsPageProps) {
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState<MappingFilter>("all");
  const [snapshots, setSnapshots] = useState<ProjectAssetsSnapshot[]>([]);
  const [loadFailures, setLoadFailures] = useState<EnvironmentLoadFailure[]>([]);
  const [loading, setLoading] = useState(false);
  const [detailsRowId, setDetailsRowId] = useState<string | null>(null);
  const [mappingDraft, setMappingDraft] = useState<ProjectMappingDraft | null>(null);
  const [openPickerRowId, setOpenPickerRowId] = useState<string | null>(null);
  const [clearRowId, setClearRowId] = useState<string | null>(null);
  const [savingRowId, setSavingRowId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<MappingActionError | null>(null);
  const requestRef = useRef(0);
  const activeProjectIdRef = useRef<number | null>(projectId);

  // A mutation can finish after a route switch. Keep its follow-up refresh and
  // local UI state tied to the project visible in this render.
  activeProjectIdRef.current = projectId;

  const projectEnvironments = useMemo(
    () => projectId ? environments.filter((environment) => environment.project_id === projectId) : [],
    [environments, projectId],
  );
  const projectEnvironmentIds = useMemo(
    () => new Set(projectEnvironments.map((environment) => environment.id)),
    [projectEnvironments],
  );
  const projectSnapshots = useMemo(
    () => snapshots.filter((snapshot) => projectEnvironmentIds.has(snapshot.environment.id)),
    [projectEnvironmentIds, snapshots],
  );
  const projectMappings = useMemo(
    () => projectId ? mappings.filter((mapping) => mapping.project_id === projectId) : [],
    [mappings, projectId],
  );
  const visibleLoadFailures = useMemo(
    () => loadFailures.filter((failure) => projectEnvironmentIds.has(failure.environmentId)),
    [loadFailures, projectEnvironmentIds],
  );

  const loadRegistryData = useCallback(async () => {
    const requestedProjectId = projectId;
    if (activeProjectIdRef.current !== requestedProjectId) return;
    const requestId = ++requestRef.current;
    setLoading(true);
    const results = await Promise.allSettled(
      projectEnvironments.map(async (environment) => ({
        environment,
        response: await loadEnvironmentAssetRegistry(environment.id),
      })),
    );
    if (requestId !== requestRef.current || activeProjectIdRef.current !== requestedProjectId) return;

    const nextSnapshots: ProjectAssetsSnapshot[] = [];
    const nextFailures: EnvironmentLoadFailure[] = [];
    results.forEach((result, index) => {
      const environment = projectEnvironments[index];
      if (!environment) return;
      if (result.status === "fulfilled") {
        nextSnapshots.push({
          environment: result.value.environment,
          assets: result.value.response.assets,
          referenceGroups: result.value.response.referenceGroups,
        });
        return;
      }
      nextFailures.push({
        environmentId: environment.id,
        environmentName: environment.name,
        message: toErrorMessage(result.reason),
      });
    });
    setSnapshots((currentSnapshots) => {
      const currentByEnvironment = new Map(currentSnapshots.map((snapshot) => [snapshot.environment.id, snapshot]));
      const refreshedByEnvironment = new Map(nextSnapshots.map((snapshot) => [snapshot.environment.id, snapshot]));
      return projectEnvironments.flatMap((environment) => {
        const refreshed = refreshedByEnvironment.get(environment.id);
        if (refreshed) return [refreshed];
        const previous = currentByEnvironment.get(environment.id);
        return previous ? [previous] : [];
      });
    });
    setLoadFailures(nextFailures);
    setLoading(false);
  }, [projectEnvironments, projectId]);

  useEffect(() => {
    void loadRegistryData();
    return () => {
      requestRef.current += 1;
    };
  }, [loadRegistryData]);

  useEffect(() => {
    const params = new URLSearchParams(routeSearch ?? "");
    const requestedQuery = params.get("q");
    if (requestedQuery !== null) setSearch(requestedQuery);
  }, [routeSearch]);

  useEffect(() => {
    setDetailsRowId(null);
    setMappingDraft(null);
    setOpenPickerRowId(null);
    setClearRowId(null);
    setSavingRowId(null);
    setActionError(null);
  }, [projectId]);

  const refreshRegistry = useCallback(async () => {
    if (activeProjectIdRef.current !== projectId) return;
    await Promise.all([onReload(), loadRegistryData()]);
  }, [loadRegistryData, onReload, projectId]);

  const registry = useMemo(
    () => buildProjectMappingRegistry(projectSnapshots, projectMappings, projectId ?? undefined),
    [projectId, projectMappings, projectSnapshots],
  );
  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return registry.rows.filter((row) => {
      if (!matchesFilter(row.state, filter)) return false;
      if (!needle) return true;
      return [
        row.normalizedValue,
        row.referenceType,
        projectMappingTargetLabel(row),
        row.mapping?.note,
        row.environments.map((environment) => environment.environmentName).join(" "),
      ].filter(Boolean).join(" ").toLowerCase().includes(needle);
    });
  }, [filter, registry.rows, search]);
  const counts = useMemo(() => buildCounts(registry.rows.map((row) => row.state)), [registry.rows]);
  const detailsRow = registry.rows.find((row) => row.id === detailsRowId) ?? null;

  function beginMapping(row: ProjectReferenceRegistryRow) {
    if (!canCreateProjectMapping(row) && !canEditProjectMapping(row)) return;
    setMappingDraft((current) => current?.rowId === row.id ? current : {
      rowId: row.id,
      targetId: projectMappingInitialTargetId(row),
      note: row.mapping?.note ?? "",
    });
    setOpenPickerRowId(row.id);
    setClearRowId(null);
    setActionError(null);
  }

  async function mapOrEdit(row: ProjectReferenceRegistryRow) {
    if (!canCreateProjectMapping(row) && !canEditProjectMapping(row)) return;
    const mutationProjectId = projectId;
    if (activeProjectIdRef.current !== mutationProjectId) return;
    if (mappingDraft?.rowId !== row.id) {
      beginMapping(row);
      return;
    }
    const target = registry.targets.find((candidate) => candidate.id === mappingDraft.targetId) ?? null;
    if (!target) {
      setActionError({ rowId: row.id, message: "Choose a canonical target before saving this project mapping." });
      return;
    }
    setSavingRowId(row.id);
    setActionError(null);
    try {
      const payload = buildReferenceMappingPayload(
        { reference_type: row.referenceType, normalized_value: row.normalizedValue },
        target,
        mappingDraft.note,
      );
      if (row.mapping) await onUpdate(row.mapping.id, payload);
      else await onCreate(payload);
      if (activeProjectIdRef.current !== mutationProjectId) return;
      await refreshRegistry();
      if (activeProjectIdRef.current !== mutationProjectId) return;
      setMappingDraft(null);
    } catch (cause) {
      if (activeProjectIdRef.current === mutationProjectId) {
        setActionError({ rowId: row.id, message: toErrorMessage(cause, "Mapping could not be saved.") });
      }
    } finally {
      if (activeProjectIdRef.current === mutationProjectId) setSavingRowId(null);
    }
  }

  async function clearMapping(row: ProjectReferenceRegistryRow) {
    if (!row.mapping || !canEditProjectMapping(row)) return;
    const mutationProjectId = projectId;
    if (activeProjectIdRef.current !== mutationProjectId) return;
    if (clearRowId !== row.id) {
      setClearRowId(row.id);
      setActionError(null);
      return;
    }
    setSavingRowId(row.id);
    setActionError(null);
    try {
      await onDelete(row.mapping.id);
      if (activeProjectIdRef.current !== mutationProjectId) return;
      await refreshRegistry();
      if (activeProjectIdRef.current !== mutationProjectId) return;
      setClearRowId(null);
      if (mappingDraft?.rowId === row.id) setMappingDraft(null);
    } catch (cause) {
      if (activeProjectIdRef.current === mutationProjectId) {
        setActionError({ rowId: row.id, message: toErrorMessage(cause, "Mapping could not be cleared.") });
      }
    } finally {
      if (activeProjectIdRef.current === mutationProjectId) setSavingRowId(null);
    }
  }

  function updateDraftTarget(rowId: string, targetId: string) {
    setMappingDraft((current) => current?.rowId === rowId ? { ...current, targetId } : current);
    setOpenPickerRowId(null);
    setActionError((current) => current?.rowId === rowId ? null : current);
  }

  function openDetails(row: ProjectReferenceRegistryRow) {
    setDetailsRowId(row.id);
  }

  function handleRowKeyDown(event: ReactKeyboardEvent<HTMLTableRowElement>, row: ProjectReferenceRegistryRow) {
    if (event.target !== event.currentTarget) return;
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    openDetails(row);
  }

  if (!projectId) {
    return <EmptyState title="Select a project" detail="Reference mappings are configured at project scope." />;
  }

  return (
    <div className="view-stack reference-mappings-view">
      <section className="table-panel projects-panel reference-mappings-panel">
        <div className="reference-mappings-toolbar">
          <div className="reference-mappings-title">
            <h2>Reference mappings</h2>
            <span>{projectName ? `${projectName} · shared across environments` : "Shared across environments"}</span>
          </div>
          <div className="reference-mappings-controls">
            <input
              className="reference-mappings-search"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Filter reference, target, note, environment"
              aria-label="Filter reference mappings"
            />
            <button className="text-action reference-mappings-icon-action" type="button" disabled={loading || busy} onClick={() => void refreshRegistry()} title="Reload mapping registry" aria-label="Reload mapping registry">
              <RefreshCw size={13} />
            </button>
          </div>
        </div>

        <div className="reference-mappings-statusline">
          <span>Each row is one canonical reference. A saved project mapping overrides automatic resolution for every occurrence of that reference.</span>
          <strong>{loading ? "Loading registry..." : `${counts.manual} manual active · ${counts.needs} need attention · ${counts.automatic} automatic`}</strong>
        </div>

        {visibleLoadFailures.length ? (
          <div className="reference-mappings-load-warning" role="alert">
            <AlertTriangle size={14} aria-hidden="true" />
            <span>Could not load {visibleLoadFailures.map((failure) => failure.environmentName).join(", ")}. Successful or previously loaded environments remain visible.</span>
            <button className="text-action" type="button" disabled={loading || busy} onClick={() => void loadRegistryData()}>Retry</button>
          </div>
        ) : null}

        <div className="reference-mappings-filter-row" aria-label="Reference mapping filters">
          <FilterChip label="All" value={counts.all} active={filter === "all"} onClick={() => setFilter("all")} />
          <FilterChip label="Manual active" value={counts.manual} active={filter === "manual"} onClick={() => setFilter("manual")} />
          <FilterChip label="Needs attention" value={counts.needs} active={filter === "needs"} onClick={() => setFilter("needs")} />
          <FilterChip label="Automatic" value={counts.automatic} active={filter === "automatic"} onClick={() => setFilter("automatic")} />
          <FilterChip label="Coverage risk" value={counts.coverage} active={filter === "coverage"} onClick={() => setFilter("coverage")} />
          <FilterChip label="Saved only" value={counts.saved} active={filter === "saved"} onClick={() => setFilter("saved")} />
        </div>

        <div className="table-scroll reference-mappings-table-wrap">
          <table className="quality-grid reference-mappings-table reference-mappings-registry-table">
            <thead>
              <tr>
                <th>Reference</th>
                <th>Resolution</th>
                <th>Canonical asset</th>
                <th>Target coverage</th>
                <th>Updated</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const draft = mappingDraft?.rowId === row.id ? mappingDraft : null;
                const canCreate = canCreateProjectMapping(row);
                const canEdit = canEditProjectMapping(row);
                const canMutate = canCreate || canEdit;
                const saving = savingRowId === row.id;
                const initialTargetId = projectMappingInitialTargetId(row);
                const canCommit = Boolean(draft?.targetId ?? initialTargetId);
                const showActionError = actionError?.rowId === row.id ? actionError.message : null;
                return (
                  <tr
                    className="project-mapping-registry-row"
                    key={row.id}
                    tabIndex={0}
                    aria-label={`View details for reference ${row.normalizedValue}`}
                    onClick={() => openDetails(row)}
                    onKeyDown={(event) => handleRowKeyDown(event, row)}
                  >
                      <td>
                        <span className="assets-asset-cell project-mapping-reference-cell" title={row.normalizedValue}>
                          <span className={`assets-asset-icon asset-tone-${assetTypeTone(referenceTypeAssetType(row.referenceType))}`}><Icon icon={assetTypeIconId(referenceTypeAssetType(row.referenceType))} aria-hidden="true" /></span>
                          <span className="assets-asset-copy">
                            <strong>{row.normalizedValue}</strong>
                            <small><span className={`reference-type-label reference-type-${row.referenceType.replace(/_reference$/, "")}`}>{row.referenceType.replace(/_reference$/, "").replace(/_/g, " ")}</span> · {row.mapping ? "project mapping" : row.environments.length ? `${row.environments.length} affected envs` : "not currently observed"}</small>
                          </span>
                        </span>
                      </td>
                      <td>
                        <span className={`assets-status-chip status-${row.state}`}>{projectMappingStateLabel(row.state)}</span>
                        <small>{projectMappingResolutionSummary(row) || "No current resolution observation"}</small>
                      </td>
                      <td onClick={(event) => event.stopPropagation()}>
                        {canMutate ? (
                          <ProjectReferenceMappingTargetPicker
                            row={row}
                            targets={registry.targets}
                            selectedTargetId={draft?.targetId ?? initialTargetId}
                            open={openPickerRowId === row.id}
                            disabled={busy || saving}
                            onOpen={() => beginMapping(row)}
                            onClose={() => setOpenPickerRowId(null)}
                            onTargetChange={(targetId) => updateDraftTarget(row.id, targetId)}
                          />
                        ) : row.mapping ? (
                          <>
                            <div className="reference-mapping-primary" title={projectMappingTargetLabel(row)}>{projectMappingTargetLabel(row)}</div>
                            <small>{projectMappingTargetBusinessKey(row)}</small>
                          </>
                        ) : <span className="reference-mapping-empty">No saved mapping</span>}
                      </td>
                      <td>
                        {row.mapping ? (
                          <>
                            <span className={row.targetCoverage.missingEnvironmentNames.length ? "reference-mapping-coverage is-partial" : "reference-mapping-coverage"}>
                              {row.targetCoverage.total ? `${row.targetCoverage.available}/${row.targetCoverage.total} affected envs` : "No observed references"}
                            </span>
                            <small>{row.targetCoverage.missingEnvironmentNames.length
                              ? `Missing: ${row.targetCoverage.missingEnvironmentNames.join(", ")}`
                              : row.targetCoverage.availableEnvironmentNames.length
                                ? `Available: ${row.targetCoverage.availableEnvironmentNames.join(", ")}`
                                : "Target is not in the loaded asset registry"}</small>
                          </>
                        ) : row.observedTargets.length === 1 ? (
                          <>
                            <span className="reference-mapping-coverage is-automatic">
                              {row.observedTargets[0].environmentNames.length}/{row.environments.length} observed envs
                            </span>
                            <small>Observed in: {row.observedTargets[0].environmentNames.join(", ")}</small>
                          </>
                        ) : row.observedTargets.length > 1 ? (
                          <>
                            <span className="reference-mapping-coverage is-partial">{row.observedTargets.length} automatic targets</span>
                            <small>Review environment details</small>
                          </>
                        ) : <span className="reference-mapping-empty">—</span>}
                      </td>
                      <td className="reference-mapping-updated">{row.mapping ? formatTimestamp(row.mapping.updated_at) : "—"}</td>
                      <td onClick={(event) => event.stopPropagation()}>
                        {canMutate ? (
                          <div className="project-mapping-table-actions">
                            <button
                              className={row.mapping ? "text-action project-mapping-action-edit" : "text-action project-mapping-action-map"}
                              type="button"
                              disabled={busy || saving || (Boolean(draft) && !canCommit)}
                              onClick={() => void mapOrEdit(row)}
                            >
                              {saving
                                ? "Saving..."
                                : draft
                                  ? "Save"
                                  : row.mapping || row.state === "automatic"
                                    ? "Edit"
                                    : "Map"}
                            </button>
                            {canEdit ? (
                              <ClearMappingAction
                                confirming={clearRowId === row.id}
                                disabled={busy || saving}
                                onClear={() => void clearMapping(row)}
                                onDismiss={() => setClearRowId(null)}
                              />
                            ) : null}
                          </div>
                        ) : <span className="reference-mapping-empty">—</span>}
                        {showActionError ? <small className="project-mapping-table-error" role="alert">{showActionError}</small> : null}
                      </td>
                  </tr>
                );
              })}
              {!rows.length ? (
                <tr>
                  <td colSpan={6}>{loading ? "Loading reference registry..." : "No references or saved mappings match the current filters."}</td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </section>

      {detailsRow ? (
        <ProjectReferenceMappingDrawer
          row={detailsRow}
          onClose={() => setDetailsRowId(null)}
          onMapInTable={canCreateProjectMapping(detailsRow) || canEditProjectMapping(detailsRow)
            ? () => {
                beginMapping(detailsRow);
                setDetailsRowId(null);
              }
            : undefined}
          mapInTableLabel={detailsRow.mapping || detailsRow.observedTargets.length ? "Edit in table" : "Map in table"}
        />
      ) : null}
    </div>
  );
}

function ClearMappingAction({
  confirming,
  disabled,
  onClear,
  onDismiss,
}: {
  confirming: boolean;
  disabled: boolean;
  onClear: () => void;
  onDismiss: () => void;
}) {
  const actionRef = useRef<HTMLSpanElement | null>(null);

  useEffect(() => {
    if (!confirming) return undefined;
    function dismissOnOutsidePointer(event: PointerEvent) {
      if (event.target instanceof Node && !actionRef.current?.contains(event.target)) onDismiss();
    }
    function dismissOnEscape(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.preventDefault();
      onDismiss();
    }
    document.addEventListener("pointerdown", dismissOnOutsidePointer, true);
    document.addEventListener("keydown", dismissOnEscape);
    return () => {
      document.removeEventListener("pointerdown", dismissOnOutsidePointer, true);
      document.removeEventListener("keydown", dismissOnEscape);
    };
  }, [confirming, onDismiss]);

  return (
    <span ref={actionRef}>
      <button
        className={confirming ? "text-action project-mapping-action-clear confirm" : "text-action project-mapping-action-clear"}
        type="button"
        disabled={disabled}
        onClick={onClear}
      >
        {confirming ? "Confirm clear" : "Clear"}
      </button>
    </span>
  );
}

function FilterChip({ label, value, active, onClick }: { label: string; value: number; active: boolean; onClick: () => void }) {
  return (
    <button type="button" aria-pressed={active} className={active ? "reference-mapping-filter-chip active" : "reference-mapping-filter-chip"} onClick={onClick}>
      <span>{label}</span>
      <strong>{value.toLocaleString()}</strong>
    </button>
  );
}

function matchesFilter(state: ProjectMappingState, filter: MappingFilter) {
  if (filter === "all") return true;
  if (filter === "manual") return state === "manual";
  if (filter === "automatic") return state === "automatic";
  if (filter === "coverage") return state === "partial" || state === "missing_target";
  if (filter === "saved") return state === "stored_only" || state === "inactive";
  return ["needs_mapping", "partial", "missing_target", "inactive", "stored_only", "review"].includes(state);
}

function buildCounts(states: ProjectMappingState[]) {
  return {
    all: states.length,
    needs: states.filter((state) => matchesFilter(state, "needs")).length,
    manual: states.filter((state) => state === "manual").length,
    automatic: states.filter((state) => state === "automatic").length,
    coverage: states.filter((state) => state === "partial" || state === "missing_target").length,
    saved: states.filter((state) => state === "stored_only" || state === "inactive").length,
  };
}

function formatTimestamp(value: string) {
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.getTime()) ? value : timestamp.toLocaleString();
}

async function loadEnvironmentAssetRegistry(environmentId: number) {
  const [assetResponse, referenceResponse] = await Promise.all([
    api.getAssets(environmentId),
    api.getAssetReferences(environmentId),
  ]);
  const assets: AssetInventoryItem[] = assetResponse.items;
  const referenceGroups: AssetReferenceGroupItem[] = referenceResponse.items;
  return { assets, referenceGroups };
}

function toErrorMessage(reason: unknown, fallback = "The environment asset registry could not be loaded.") {
  return reason instanceof Error ? reason.message : fallback;
}
