import { AlertTriangle, X } from "lucide-react";
import { Icon } from "@iconify/react";
import { useEffect, useId, useRef, type SyntheticEvent } from "react";
import { createPortal } from "react-dom";
import { assetTypeIconId, assetTypeTone, referenceTypeAssetType } from "../lineage/model/presentation";
import {
  projectEnvironmentResolutionLabel,
  projectMappingResolutionSummary,
  projectMappingStateLabel,
  type ProjectReferenceRegistryRow,
} from "./projectReferenceMappingRegistryModel";

interface ProjectReferenceMappingDrawerProps {
  row: ProjectReferenceRegistryRow;
  onClose: () => void;
  onMapInTable?: () => void;
  mapInTableLabel?: string;
}

/**
 * Project mappings are written from the table. The drawer deliberately stays
 * read-only so its detail view cannot imply an environment-scoped asset-id map.
 */
export function ProjectReferenceMappingDrawer({ row, onClose, onMapInTable, mapInTableLabel }: ProjectReferenceMappingDrawerProps) {
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const titleId = useId();
  const impact = row.environments.reduce(
    (totals, environment) => ({
      occurrences: totals.occurrences + environment.occurrenceCount,
      consumers: totals.consumers + environment.consumerCount,
    }),
    { occurrences: 0, consumers: 0 },
  );
  const observedTarget = row.observedTargets.length === 1 ? row.observedTargets[0] : null;

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return undefined;
    if (!dialog.open) dialog.showModal();
    const frame = window.requestAnimationFrame(() => dialog.querySelector<HTMLElement>("button")?.focus());
    return () => {
      window.cancelAnimationFrame(frame);
      if (dialog.open) dialog.close();
    };
  }, []);

  function handleCancel(event: SyntheticEvent<HTMLDialogElement>) {
    event.preventDefault();
    onClose();
  }

  return createPortal(
    <dialog
      className="metadata-drawer project-mapping-drawer"
      ref={dialogRef}
      aria-modal="true"
      aria-labelledby={titleId}
      onCancel={handleCancel}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <header className="metadata-drawer-header project-mapping-drawer-header">
        <div>
          <span className="eyebrow">Reference mapping details</span>
          <div className="project-mapping-reference-header">
            <span className={`assets-asset-icon asset-tone-${assetTypeTone(referenceTypeAssetType(row.referenceType))}`}><Icon icon={assetTypeIconId(referenceTypeAssetType(row.referenceType))} aria-hidden="true" /></span>
            <div>
              <h2 id={titleId} title={row.normalizedValue}>{row.normalizedValue}</h2>
              <small>
                <span className={`reference-type-label reference-type-${row.referenceType.replace(/_reference$/, "")}`}>{row.referenceType.replace(/_reference$/, "").replace(/_/g, " ")}</span>
                <span aria-hidden="true"> · </span>
                <span className={`assets-status-chip status-${row.state}`}>{projectMappingStateLabel(row.state)}</span>
              </small>
            </div>
          </div>
        </div>
        <button className="icon-action small" type="button" title="Close" aria-label="Close mapping details" onClick={onClose}>
          <X size={15} />
        </button>
      </header>

      <div className="metadata-drawer-body project-mapping-drawer-body">
        <section className="project-mapping-drawer-summary" aria-label="Mapping impact">
          <div><span>Affected</span><strong>{row.environments.length} environments</strong></div>
          <div><span>Occurrences</span><strong>{impact.occurrences}</strong></div>
          <div><span>Consumers</span><strong>{impact.consumers}</strong></div>
          <div><span>Saved rule</span><strong>{row.mapping ? `#${row.mapping.id}` : "None"}</strong></div>
        </section>

        <section className="project-mapping-key-pair" aria-label="Mapping business keys">
          <div className="project-mapping-key-card is-reference">
            <span className="project-mapping-key-card-label">Reference business key</span>
            <p>Both fields must match a reference before this project rule can apply.</p>
            <dl className="project-mapping-key-fields">
              <div><dt>reference_type</dt><dd><code>{row.referenceType}</code></dd></div>
              <div><dt>reference_normalized_value</dt><dd><code>{row.normalizedValue}</code></dd></div>
            </dl>
            <small>One saved mapping applies to every occurrence of this canonical reference in the project.</small>
          </div>
          {row.mapping ? (
            <div className="project-mapping-key-card is-target">
              <div className="project-mapping-key-card-heading">
                <span className="project-mapping-key-card-label">Asset business key</span>
                {onMapInTable ? <button className="text-action primary project-mapping-drawer-bridge" type="button" onClick={onMapInTable}>{mapInTableLabel ?? "Edit in table"}</button> : null}
              </div>
              <p>These fields identify the asset selected when the reference condition matches.</p>
              <dl className="project-mapping-key-fields">
                <div><dt>target_identifier_kind</dt><dd><code>{row.mapping.target_identifier_kind}</code></dd></div>
                <div><dt>target_normalized_value</dt><dd><code>{row.mapping.target_normalized_value}</code></dd></div>
              </dl>
            </div>
          ) : observedTarget ? (
            <div className="project-mapping-key-card is-target is-observed">
              <div className="project-mapping-key-card-heading">
                <span className="project-mapping-key-card-label">Observed asset business key</span>
                {onMapInTable ? <button className="text-action primary project-mapping-drawer-bridge" type="button" onClick={onMapInTable}>{mapInTableLabel ?? "Edit in table"}</button> : null}
              </div>
              <p><span className="reference-mapping-target-mode is-automatic">Automatic</span> This target is observed from current environment resolution and is not a saved rule.</p>
              <dl className="project-mapping-key-fields">
                <div><dt>target_identifier_kind</dt><dd><code>{observedTarget.target.kind}</code></dd></div>
                <div><dt>target_normalized_value</dt><dd><code>{observedTarget.target.value}</code></dd></div>
              </dl>
            </div>
          ) : row.observedTargets.length > 1 ? (
            <div className="project-mapping-key-card is-target is-observed">
              <div className="project-mapping-key-card-heading">
                <span className="project-mapping-key-card-label">Observed asset business keys</span>
                {onMapInTable ? <button className="text-action primary project-mapping-drawer-bridge" type="button" onClick={onMapInTable}>{mapInTableLabel ?? "Edit in table"}</button> : null}
              </div>
              <p>Automatic resolution currently produces {row.observedTargets.length} canonical targets. Select one explicitly only if a project override is required.</p>
              <ul className="project-mapping-observed-target-list">
                {row.observedTargets.map((item) => (
                  <li key={item.target.id}>
                    <strong>{item.target.displayName}</strong>
                    <small>{item.target.connectionName ? `${item.target.connectionName} · ` : ""}{item.target.kind} · {item.target.value}</small>
                    <span>{item.environmentNames.join(", ")}</span>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <div className="project-mapping-key-card is-target is-empty">
              <div className="project-mapping-key-card-heading">
                <span className="project-mapping-key-card-label">Asset business key</span>
                {onMapInTable ? <button className="text-action primary project-mapping-drawer-bridge" type="button" onClick={onMapInTable}>{mapInTableLabel ?? "Map in table"}</button> : null}
              </div>
              <p>No project mapping is saved. Select a canonical target in the table to override automatic resolution.</p>
            </div>
          )}
        </section>

        {row.mapping ? (
          <section className="project-mapping-drawer-section">
            <div className="project-mapping-drawer-section-heading">
              <h3>Mapping context</h3>
              <span>rule details</span>
            </div>
            <div className="project-mapping-context-card">
              <dl className="project-mapping-metadata">
                <div><dt>Mapping rule</dt><dd>#{row.mapping.id} · project override</dd></div>
                <div><dt>Last updated</dt><dd>{new Date(row.mapping.updated_at).toLocaleString()}</dd></div>
                <div><dt>Rationale</dt><dd>{row.mapping.note || "No rationale note."}</dd></div>
              </dl>
            </div>
          </section>
        ) : null}

        {row.mapping ? (
          <section className="project-mapping-drawer-section">
            <div className="project-mapping-drawer-section-heading">
              <h3>Target coverage</h3>
              <span>{row.targetCoverage.total ? `${row.targetCoverage.available}/${row.targetCoverage.total} affected envs` : "not observed"}</span>
            </div>
            <p className="project-mapping-muted">{row.targetCoverage.missingEnvironmentNames.length
              ? `Target business key is missing in: ${row.targetCoverage.missingEnvironmentNames.join(", ")}.`
              : row.targetCoverage.availableEnvironmentNames.length
                ? `Target business key is available in: ${row.targetCoverage.availableEnvironmentNames.join(", ")}.`
                : "The target business key is not in the loaded asset registry."}</p>
          </section>
        ) : null}

        {row.state === "unresolved" || row.state === "automatic" ? (
          <section className="project-mapping-readonly" aria-label="Resolution note">
            <AlertTriangle size={16} aria-hidden="true" />
            <div>
              <strong>{row.state === "unresolved" ? "This reference needs mapping." : "Automatic resolution is active."}</strong>
              <p>{row.state === "unresolved" ? "Select one canonical asset to resolve this reference." : "A project mapping replaces the automatic target for every occurrence of this canonical reference."}</p>
            </div>
          </section>
        ) : null}

        <section className="project-mapping-drawer-section">
          <div className="project-mapping-drawer-section-heading">
            <h3>Effective resolution</h3>
            <span>{projectMappingResolutionSummary(row) || "no observations"}</span>
          </div>
          {row.environments.length ? (
            <ul className="project-mapping-environment-list">
              {row.environments.map((environment) => (
                <li key={environment.environmentId}>
                  <div>
                    <strong>{environment.environmentName}</strong>
                    {environment.observedTargetIds.length ? (
                      <small>{environment.observedTargetIds.map((targetId) => row.observedTargets.find((item) => item.target.id === targetId)?.target.displayName).filter(Boolean).join(", ")}</small>
                    ) : null}
                  </div>
                  <span>{projectEnvironmentResolutionLabel(environment)}</span>
                </li>
              ))}
            </ul>
          ) : <p className="project-mapping-muted">No current reference observation. This saved rule remains available for future matching references.</p>}
        </section>
      </div>

    </dialog>,
    document.body,
  );
}
