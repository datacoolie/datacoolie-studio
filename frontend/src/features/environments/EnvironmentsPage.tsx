import { CheckCircle2, Code2, Database, FolderOpen, Plus, Settings2, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import type { ProjectEnvironmentSummary, ProjectSummary } from "../../shared/api/types";
import { EmptyState } from "../../shared/components/EmptyState";
import { ENVIRONMENT_PRESETS, orderEnvironmentItems } from "../../shared/environmentOrder";

interface EnvironmentsPageProps {
  project: ProjectSummary | null;
  busy: boolean;
  onCreateEnvironment: (name: string) => Promise<number>;
  onDeleteEnvironment: (environmentId: number) => Promise<void>;
  onOpenEnvironment: (projectId: number, environmentId: number) => void;
  onConfigureSources: (projectId: number, environmentId: number) => void;
}

export function EnvironmentsPage({
  project,
  busy,
  onCreateEnvironment,
  onDeleteEnvironment,
  onOpenEnvironment,
  onConfigureSources,
}: EnvironmentsPageProps) {
  const [customName, setCustomName] = useState("");
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const existingNames = useMemo(
    () => new Set(project?.environments.map((e) => e.name) ?? []),
    [project]
  );
  const orderedEnvironments = useMemo(
    () => orderEnvironmentItems(project?.environments ?? []),
    [project]
  );

  if (!project) {
    return <EmptyState title={busy ? "Loading project" : "Project not found"} />;
  }

  async function handleCustomCreate(e: React.FormEvent) {
    e.preventDefault();
    const name = normalizeName(customName);
    if (!name) return;
    const envId = await onCreateEnvironment(name);
    setCustomName("");
    if (envId) onConfigureSources(project!.id, envId);
  }

  async function handleDelete(id: number) {
    await onDeleteEnvironment(id);
    setDeletingId(null);
  }

  return (
    <div className="view-stack">
      <section className="table-panel environments-panel">
        <div className="panel-toolbar">
          <h2>Environments</h2>
          <span>{project.name}</span>
        </div>

        {/* Create bar: presets + custom inline — all in one row */}
        <div className="env-create-bar">
          {ENVIRONMENT_PRESETS.map((name) => {
            const exists = existingNames.has(name);
            return (
              <button
                key={name}
                type="button"
                className={`env-preset-chip${exists ? " exists" : ""}`}
                disabled={busy || exists}
                onClick={async () => {
                  const envId = await onCreateEnvironment(name);
                  if (envId) onConfigureSources(project.id, envId);
                }}
                title={exists ? `${name} already exists` : `Create ${name} environment`}
              >
                {exists ? <CheckCircle2 size={12} /> : <Plus size={12} />}
                {name}
              </button>
            );
          })}
          <form className="env-custom-form" onSubmit={handleCustomCreate}>
            <input
              value={customName}
              onChange={(e) => setCustomName(e.target.value)}
              placeholder="Custom name…"
              className="env-custom-input"
            />
            <button type="submit" className="env-custom-btn" disabled={busy || !normalizeName(customName)}>
              <Plus size={13} />
            </button>
          </form>
        </div>

        {/* Environment list */}
        <div className="env-list">
          {orderedEnvironments.length === 0 && (
            <div className="table-empty">No environments yet — create one above</div>
          )}
          {orderedEnvironments.map((env) => {
            const status = envStatus(env);
            const isConfirming = deletingId === env.id;
            return (
              <div key={env.id} className={`env-row${isConfirming ? " env-row-confirming" : ""}`}>
                {isConfirming ? (
                  <div className="env-row-confirm">
                    <span>Delete <strong>{env.name}</strong>? This removes all sources and data.</span>
                    <button className="env-confirm-yes" disabled={busy} onClick={() => handleDelete(env.id)}>
                      Delete
                    </button>
                    <button className="env-confirm-no" onClick={() => setDeletingId(null)}>
                      Cancel
                    </button>
                  </div>
                ) : (
                  <>
                    <button className="env-row-main" onClick={() => onOpenEnvironment(project.id, env.id)}>
                      <span className="env-row-name">{env.name}</span>
                      <span className="env-row-stats">
                        <span title="Metadata sources"><Database size={11} />{env.metadata_source_count} src</span>
                        <span title="Log paths"><FolderOpen size={11} />{env.etl_log_path_count} log</span>
                        <span title="Code artifacts"><Code2 size={11} />{env.code_artifact_count ?? 0} code</span>
                      </span>
                      {status === "ready" && <span className="env-badge env-badge-ready"><CheckCircle2 size={10} />ready</span>}
                      {status === "partial" && <span className="env-badge env-badge-partial">partial</span>}
                      {status === "empty" && <span className="env-badge env-badge-empty">empty</span>}
                      <span className="env-row-open">Open workspace →</span>
                    </button>
                    <div className="env-row-actions">
                      <button
                        className="env-action-configure"
                        onClick={() => onConfigureSources(project.id, env.id)}
                        title="Configure sources"
                      >
                        <Settings2 size={13} />
                        <span>Sources</span>
                      </button>
                      <button
                        className="env-action-delete"
                        onClick={() => setDeletingId(env.id)}
                        title="Delete environment"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  </>
                )}
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}

function envStatus(env: ProjectEnvironmentSummary): "ready" | "partial" | "empty" {
  const hasMetadata = env.metadata_source_count > 0;
  const hasLogs = env.etl_log_path_count > 0;
  return hasMetadata && hasLogs
    ? "ready"
    : hasMetadata || hasLogs || (env.code_artifact_count ?? 0) > 0
      ? "partial"
      : "empty";
}

function normalizeName(value: string) {
  return value.trim().toLowerCase();
}
