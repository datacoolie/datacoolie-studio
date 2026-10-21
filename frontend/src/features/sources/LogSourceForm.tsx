import { Plus } from "lucide-react";
import { useState, type FormEvent } from "react";

import type { StorageBinding } from "../../shared/api/domainTypes";
import { LOCAL_STORAGE_BINDING, StorageLocationFields } from "./StorageLocationFields";

export type LogSourceFormProps = {
  busy: boolean;
  disabled: boolean;
  onAddLogBasePath: (uri: string, label?: string, sourceConfig?: Record<string, unknown>, storage?: StorageBinding) => Promise<boolean>;
};

export function LogSourceForm({ busy, disabled, onAddLogBasePath }: LogSourceFormProps) {
  const [mode, setMode] = useState<"log_base_path" | "separate_paths">("log_base_path");
  const [baseUri, setBaseUri] = useState("");
  const [executionUri, setExecutionUri] = useState("");
  const [systemUri, setSystemUri] = useState("");
  const [label, setLabel] = useState("");
  const [storage, setStorage] = useState<StorageBinding>(LOCAL_STORAGE_BINDING);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (mode === "log_base_path") {
      if (!baseUri.trim()) return;
      const added = await onAddLogBasePath(baseUri.trim(), label.trim() || undefined, {
        mode: "log_base_path",
        log_base_path: baseUri.trim(),
      }, storage);
      if (!added) return;
      setBaseUri("");
    } else {
      if (!executionUri.trim() && !systemUri.trim()) return;
      const primaryUri = executionUri.trim() || systemUri.trim();
      const added = await onAddLogBasePath(primaryUri, label.trim() || undefined, {
        mode: "separate_paths",
        execution_logs_uri: executionUri.trim() || undefined,
        system_logs_uri: systemUri.trim() || undefined,
      }, storage);
      if (!added) return;
      setExecutionUri("");
      setSystemUri("");
    }
    setLabel("");
  }

  return (
    <div className="source-config-card">
      <form className="source-add-form source-log-form" onSubmit={submit}>
        <div className="source-add-heading">
          <strong>Add log source</strong>
          <span>Log base path or separate execution/system log base folders.</span>
        </div>
        <label>
          Mode
          <select value={mode} onChange={(event) => setMode(event.target.value as "log_base_path" | "separate_paths")} disabled={busy || disabled}>
            <option value="log_base_path">Log base path</option>
            <option value="separate_paths">Separate execution/system log base paths</option>
          </select>
        </label>
        {mode === "log_base_path" ? (
          <label className="source-field-wide">
            Log base path
            <input value={baseUri} onChange={(event) => setBaseUri(event.target.value)} placeholder="logs folder" disabled={busy || disabled} />
          </label>
        ) : (
          <div className="source-form-row two">
            <label>
              Execution log base path
              <input value={executionUri} onChange={(event) => setExecutionUri(event.target.value)} placeholder="execution_logs folder" disabled={busy || disabled} />
            </label>
            <label>
              System log base path
              <input value={systemUri} onChange={(event) => setSystemUri(event.target.value)} placeholder="system_logs folder" disabled={busy || disabled} />
            </label>
          </div>
        )}
        <label>
          Label
          <input value={label} onChange={(event) => setLabel(event.target.value)} placeholder="Optional label" disabled={busy || disabled} />
        </label>
        <StorageLocationFields
          binding={storage}
          uri={mode === "log_base_path" ? baseUri : executionUri || systemUri}
          sourceConfig={mode === "log_base_path"
            ? { mode, log_base_path: baseUri }
            : { mode, execution_logs_uri: executionUri || undefined, system_logs_uri: systemUri || undefined }}
          disabled={busy || disabled}
          onChange={setStorage}
        />
        <button type="submit" disabled={busy || disabled || (mode === "log_base_path" ? !baseUri.trim() : !executionUri.trim() && !systemUri.trim())}>
          <Plus size={16} />
          <span>Add log source</span>
        </button>
      </form>
    </div>
  );
}
