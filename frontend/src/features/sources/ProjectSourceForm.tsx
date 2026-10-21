import { ChevronDown, Search, X } from "lucide-react";
import type { FormEvent } from "react";

import type { StorageBinding } from "../../shared/api/domainTypes";
import { StorageLocationFields } from "./StorageLocationFields";
import {
  addSourcePath,
  removeSourcePath,
  replaceSourcePath,
} from "./sourceSetupModel";

export type ProjectSourceFormProps = {
  busy: boolean;
  disabled: boolean;
  projectUri: string;
  metadataSubpath: string;
  sqlSubpaths: string[];
  codeSubpaths: string[];
  projectStorage: StorageBinding;
  onProjectUriChange: (value: string) => void;
  onMetadataSubpathChange: (value: string) => void;
  onSqlSubpathsChange: (value: string[]) => void;
  onCodeSubpathsChange: (value: string[]) => void;
  onProjectStorageChange: (value: StorageBinding) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
};

export function ProjectSourceForm({
  busy,
  disabled,
  projectUri,
  metadataSubpath,
  sqlSubpaths,
  codeSubpaths,
  projectStorage,
  onProjectUriChange,
  onMetadataSubpathChange,
  onSqlSubpathsChange,
  onCodeSubpathsChange,
  onProjectStorageChange,
  onSubmit,
}: ProjectSourceFormProps) {
  return (
    <form id="project-source-project-panel" role="tabpanel" aria-labelledby="project-source-project-tab" className="source-add-form source-project-form" onSubmit={onSubmit}>
      <div className="source-add-heading">
        <strong>Scan project root</strong>
        <span id="project-root-help">One root per scan. Marker files may define multiple SQL/code folders.</span>
      </div>
      <label className="source-field-wide" htmlFor="project-root-input">
        Project root
        <input id="project-root-input" value={projectUri} onChange={(event) => onProjectUriChange(event.target.value)} placeholder="Local directory or cloud storage URI" aria-describedby="project-root-help" required disabled={busy || disabled} />
      </label>
      <StorageLocationFields
        binding={projectStorage}
        uri={projectUri}
        sourceConfig={{
          metadata_root_uri: metadataSubpath.trim()
            ? `${projectUri.replace(/\/$/, "")}/${metadataSubpath.trim()}`
            : undefined,
        }}
        disabled={busy || disabled}
        onChange={onProjectStorageChange}
      />
      <details className="source-project-fallbacks">
        <summary>
          <span className="source-project-fallbacks-title"><ChevronDown size={14} aria-hidden="true" /><strong>Fallback folders</strong></span>
        </summary>
        <div className="source-project-options" aria-label="Project component fallback folders">
          <section className="source-project-option">
            <header className="source-project-option-header">
              <label htmlFor="project-metadata-fallback">
                <strong>Metadata</strong>
              </label>
            </header>
            <div className="source-project-input-shell">
              <input
                id="project-metadata-fallback"
                className="source-project-option-input"
                value={metadataSubpath}
                onChange={(event) => onMetadataSubpathChange(event.target.value)}
                disabled={busy || disabled}
                aria-label="Metadata fallback folder (default: metadata)"
                placeholder="metadata"
              />
            </div>
          </section>
          <section className="source-project-option">
            <header className="source-project-option-header">
              <div>
                <strong>SQL</strong>
              </div>
            </header>
            <div className="source-project-path-list">
              {sqlSubpaths.map((path, index) => (
                <div className="source-project-path-row" key={`sql-fallback-${index}`}>
                  <label className="source-project-path-label" htmlFor={`project-sql-fallback-${index}`}>SQL fallback folder {index + 1}</label>
                  <div className="source-project-input-shell">
                    <input
                      id={`project-sql-fallback-${index}`}
                      className="source-project-option-input"
                      value={path}
                      onChange={(event) => onSqlSubpathsChange(replaceSourcePath(sqlSubpaths, index, event.target.value))}
                      disabled={busy || disabled}
                      aria-label={`SQL fallback folder ${index + 1}`}
                      placeholder={index === 0 ? "sql" : "Additional SQL folder"}
                    />
                    <button
                      type="button"
                      className="source-project-remove-path"
                      aria-label={`Remove SQL fallback folder ${index + 1}`}
                      title={`Remove SQL fallback folder ${index + 1}`}
                      onClick={() => onSqlSubpathsChange(removeSourcePath(sqlSubpaths, index))}
                      disabled={busy || disabled || sqlSubpaths.length === 1}
                    >
                      <X size={14} aria-hidden="true" />
                    </button>
                  </div>
                </div>
              ))}
              <button type="button" className="source-project-add-path" onClick={() => onSqlSubpathsChange(addSourcePath(sqlSubpaths))} disabled={busy || disabled}>
                <span>+Add</span>
              </button>
            </div>
          </section>
          <section className="source-project-option">
            <header className="source-project-option-header">
              <div>
                <strong>Source code</strong>
              </div>
            </header>
            <div className="source-project-path-list">
              {codeSubpaths.map((path, index) => (
                <div className="source-project-path-row" key={`code-fallback-${index}`}>
                  <label className="source-project-path-label" htmlFor={`project-code-fallback-${index}`}>Source-code fallback folder {index + 1}</label>
                  <div className="source-project-input-shell">
                    <input
                      id={`project-code-fallback-${index}`}
                      className="source-project-option-input"
                      value={path}
                      onChange={(event) => onCodeSubpathsChange(replaceSourcePath(codeSubpaths, index, event.target.value))}
                      disabled={busy || disabled}
                      aria-label={`Source-code fallback folder ${index + 1}`}
                      placeholder={index === 0 ? "functions" : "Additional source-code folder"}
                    />
                    <button
                      type="button"
                      className="source-project-remove-path"
                      aria-label={`Remove source-code fallback folder ${index + 1}`}
                      title={`Remove source-code fallback folder ${index + 1}`}
                      onClick={() => onCodeSubpathsChange(removeSourcePath(codeSubpaths, index))}
                      disabled={busy || disabled || codeSubpaths.length === 1}
                    >
                      <X size={14} aria-hidden="true" />
                    </button>
                  </div>
                </div>
              ))}
              <button type="button" className="source-project-add-path" onClick={() => onCodeSubpathsChange(addSourcePath(codeSubpaths))} disabled={busy || disabled}>
                <span>+Add</span>
              </button>
            </div>
          </section>
        </div>
      </details>
      <button type="submit" disabled={busy || disabled || !projectUri.trim()}>
        <Search size={16} />
        <span>Scan project</span>
      </button>
    </form>
  );
}
