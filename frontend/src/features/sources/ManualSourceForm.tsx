import { Plus } from "lucide-react";
import type { FormEvent } from "react";

import type { StorageBinding } from "../../shared/api/domainTypes";
import { StorageLocationFields } from "./StorageLocationFields";
import { manualSourceCopy, type ManualSourceKind } from "./sourceSetupModel";

export type ManualSourceFormProps = {
  busy: boolean;
  disabled: boolean;
  manualKind: ManualSourceKind;
  manualUri: string;
  manualLabel: string;
  artifactType: string;
  moduleRoots: string;
  modulePrefix: string;
  manualStorage: StorageBinding;
  onManualKindChange: (value: ManualSourceKind) => void;
  onManualUriChange: (value: string) => void;
  onManualLabelChange: (value: string) => void;
  onArtifactTypeChange: (value: string) => void;
  onModuleRootsChange: (value: string) => void;
  onModulePrefixChange: (value: string) => void;
  onManualStorageChange: (value: StorageBinding) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
};

export function ManualSourceForm({
  busy,
  disabled,
  manualKind,
  manualUri,
  manualLabel,
  artifactType,
  moduleRoots,
  modulePrefix,
  manualStorage,
  onManualKindChange,
  onManualUriChange,
  onManualLabelChange,
  onArtifactTypeChange,
  onModuleRootsChange,
  onModulePrefixChange,
  onManualStorageChange,
  onSubmit,
}: ManualSourceFormProps) {
  const manualCopy = manualSourceCopy(manualKind);

  return (
    <form id="project-source-manual-panel" role="tabpanel" aria-labelledby="project-source-manual-tab" className="source-add-form source-project-form" onSubmit={onSubmit}>
      <div className="source-add-heading" aria-live="polite" aria-atomic="true">
        <strong>{manualCopy.heading}</strong>
        <span>{manualCopy.description}</span>
      </div>
      <label htmlFor="manual-source-type">
        Source type
        <select id="manual-source-type" value={manualKind} onChange={(event) => onManualKindChange(event.target.value as ManualSourceKind)} disabled={busy || disabled}>
          <option value="metadata">Metadata</option>
          <option value="sql">SQL</option>
          <option value="code">Source code</option>
        </select>
      </label>
      <label className="source-field-wide" htmlFor="manual-source-path">
        {manualCopy.pathLabel}
        <input
          id="manual-source-path"
          value={manualUri}
          onChange={(event) => onManualUriChange(event.target.value)}
          placeholder={manualCopy.pathPlaceholder}
          aria-describedby="manual-source-path-help"
          disabled={busy || disabled}
        />
        <small id="manual-source-path-help" className="source-field-help">{manualCopy.pathHint}</small>
      </label>
      <label htmlFor="manual-source-label">
        Label
        <input id="manual-source-label" value={manualLabel} onChange={(event) => onManualLabelChange(event.target.value)} placeholder="Optional label" disabled={busy || disabled} />
      </label>
      <StorageLocationFields
        binding={manualStorage}
        uri={manualUri}
        disabled={busy || disabled}
        onChange={onManualStorageChange}
      />
      {manualKind === "code" ? (
        <div className="source-form-row two">
          <label>
            Artifact type
            <select value={artifactType} onChange={(event) => onArtifactTypeChange(event.target.value)} disabled={busy || disabled}>
              <option value="directory">Directory</option>
              <option value="python_file">Python file</option>
              <option value="zip">ZIP archive</option>
              <option value="wheel">Python wheel</option>
              <option value="installed_distribution">Installed distribution (pip)</option>
            </select>
          </label>
          <label>
            Module prefix override
            <input value={modulePrefix} onChange={(event) => onModulePrefixChange(event.target.value)} placeholder="Usually automatic" disabled={busy || disabled} />
          </label>
          <label className="source-field-wide">
            Module roots override
            <input value={moduleRoots} onChange={(event) => onModuleRootsChange(event.target.value)} placeholder="Optional filters, e.g. src, python" disabled={busy || disabled} />
          </label>
        </div>
      ) : null}
      <button type="submit" disabled={busy || disabled || !manualUri.trim()}>
        <Plus size={16} />
        <span>{manualCopy.submitLabel}</span>
      </button>
    </form>
  );
}
