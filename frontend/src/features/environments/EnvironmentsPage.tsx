import { ArrowRight, CheckCircle2, Code2, Database, FolderOpen, Pencil, Plus, Settings2, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import type { ProjectSummary } from "../../shared/api/domainTypes";
import { EmptyState } from "../../shared/components/EmptyState";
import { ENVIRONMENT_PRESETS, orderEnvironmentItems } from "../../shared/environmentOrder";
import { environmentReadiness, environmentReadinessLabel } from "../../shared/environmentReadiness";

interface EnvironmentsPageProps {
  project: ProjectSummary | null;
  busy: boolean;
  onCreateEnvironment: (name: string) => Promise<number>;
  onRenameEnvironment: (environmentId: number, name: string) => Promise<void>;
  onDeleteEnvironment: (environmentId: number) => Promise<void>;
  onOpenEnvironment: (projectId: number, environmentId: number) => void;
  onConfigureSources: (projectId: number, environmentId: number) => void;
}

export function EnvironmentsPage({
  project,
  busy,
  onCreateEnvironment,
  onRenameEnvironment,
  onDeleteEnvironment,
  onOpenEnvironment,
  onConfigureSources,
}: EnvironmentsPageProps) {
  const [customName, setCustomName] = useState("");
  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameDraft, setRenameDraft] = useState("");

  const existingNames = useMemo(
    () => new Set((project?.environments ?? []).map((environment) => environment.name.toLowerCase())),
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
    await createAndConfigure(name);
  }

  async function createAndConfigure(name: string) {
    const envId = await onCreateEnvironment(name);
    if (!envId) return;
    setCustomName("");
    onConfigureSources(project!.id, envId);
  }

  async function handleDelete(id: number) {
    try {
      await onDeleteEnvironment(id);
      setDeletingId(null);
    } catch {
      // The workspace renders the mutation error and the confirmation stays open.
    }
  }

  async function handleRename(environmentId: number, currentName: string) {
    const name = normalizeName(renameDraft);
    if (!isValidEnvironmentName(name) || name === currentName) return;
    try {
      await onRenameEnvironment(environmentId, name);
      setRenamingId(null);
      setRenameDraft("");
    } catch {
      // The workspace renders the mutation error and the rename form stays open.
    }
  }

  return (
    <div className="view-stack">
      <section className="table-panel environments-panel">
        <div className="panel-toolbar">
          <h2>Environments</h2>
          <span>{project.name}</span>
        </div>

        <div className="env-create-bar">
          <div className="env-preset-chips" aria-label="Suggested environments">
            {ENVIRONMENT_PRESETS.map((name) => {
              const exists = existingNames.has(name.toLowerCase());
              return (
                <button
                  key={name}
                  type="button"
                  className={`env-preset-chip${exists ? " exists" : ""}`}
                  disabled={busy || exists}
                  onClick={() => void createAndConfigure(name)}
                  aria-label={exists ? `${name} environment already exists` : `Create ${name} environment`}
                >
                  {exists ? <CheckCircle2 size={12} /> : <Plus size={12} />}
                  {name}
                </button>
              );
            })}
          </div>
          <form className="env-custom-form" onSubmit={handleCustomCreate}>
            <input
              id="environment-name"
              value={customName}
              onChange={(e) => setCustomName(e.target.value)}
              placeholder="e.g. staging"
              className="env-custom-input"
              aria-label="Add custom environment"
              aria-describedby="environment-name-hint"
            />
            <span id="environment-name-hint" className="env-custom-hint">Use letters, numbers, hyphens, and underscores.</span>
            <button type="submit" className="env-custom-btn" disabled={busy || !normalizeName(customName)}>
              <Plus size={13} aria-hidden="true" />
              Add
            </button>
          </form>
        </div>

        <div className="env-list">
          {orderedEnvironments.length === 0 && (
            <div className="env-empty-state">
              <div>
                <strong>Create your first environment</strong>
                <span>Start with a suggested environment, then add its metadata source.</span>
              </div>
              <button type="button" className="env-empty-create" disabled={busy} onClick={() => void createAndConfigure("dev")}>
                <Plus size={14} aria-hidden="true" />
                Create dev environment
              </button>
            </div>
          )}
          {orderedEnvironments.map((env) => {
            const status = environmentReadiness(env);
            const needsMetadata = status === "needs-metadata";
            const isConfirming = deletingId === env.id;
            const isRenaming = renamingId === env.id;
            return (
              <div
                key={env.id}
                className={`env-row${isConfirming ? " env-row-confirming" : ""}${isRenaming ? " env-row-renaming" : ""}`}
              >
                {isConfirming ? (
                  <div className="env-row-confirm">
                    <div className="env-row-confirm-copy">
                      <strong>Remove {env.name}?</strong>
                      <span>Studio source settings and cached data will be removed. Original source files are not deleted.</span>
                    </div>
                    <div className="env-row-confirm-actions">
                      <button className="env-confirm-no" disabled={busy} onClick={() => setDeletingId(null)}>
                        Cancel
                      </button>
                      <button className="env-confirm-yes" disabled={busy} onClick={() => handleDelete(env.id)}>
                        Delete environment
                      </button>
                    </div>
                  </div>
                ) : isRenaming ? (
                  <form
                    className="env-row-rename"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void handleRename(env.id, env.name);
                    }}
                    onKeyDown={(event) => {
                      if (event.key !== "Escape") return;
                      event.preventDefault();
                      setRenamingId(null);
                      setRenameDraft("");
                    }}
                  >
                    <label htmlFor={`environment-rename-${env.id}`}>Environment name</label>
                    <input
                      id={`environment-rename-${env.id}`}
                      autoFocus
                      value={renameDraft}
                      maxLength={50}
                      onChange={(event) => setRenameDraft(event.target.value)}
                    />
                    <button
                      type="submit"
                      className="env-rename-save"
                      disabled={
                        busy
                        || !isValidEnvironmentName(renameDraft)
                        || normalizeName(renameDraft) === env.name
                      }
                    >
                      Save
                    </button>
                    <button
                      type="button"
                      className="env-confirm-no"
                      onClick={() => {
                        setRenamingId(null);
                        setRenameDraft("");
                      }}
                    >
                      Cancel
                    </button>
                  </form>
                ) : (
                  <>
                    <button
                      className="env-row-main"
                      onClick={() => needsMetadata ? onConfigureSources(project.id, env.id) : onOpenEnvironment(project.id, env.id)}
                      aria-label={needsMetadata ? `Add a metadata source to ${env.name}` : `Open ${env.name} workspace`}
                    >
                      <span className="env-row-name">{env.name}</span>
                      <span className="env-row-stats">
                        <span title="Metadata sources"><Database size={11} />{env.metadata_source_count} src</span>
                        <span title="Log paths"><FolderOpen size={11} />{env.etl_log_path_count} log</span>
                        <span title="Code artifacts"><Code2 size={11} />{env.code_artifact_count ?? 0} code</span>
                      </span>
                      {status === "ready" ? (
                        <span className="env-badge env-badge-ready"><CheckCircle2 size={10} />{environmentReadinessLabel(status)}</span>
                      ) : (
                        <span className="env-badge env-badge-needs-metadata">{environmentReadinessLabel(status)}</span>
                      )}
                      <span className="env-row-open">
                        {needsMetadata ? "Add metadata source" : "Open workspace"}
                        <ArrowRight size={13} aria-hidden="true" />
                      </span>
                    </button>
                    <div className="env-row-actions">
                      <button
                        className="env-action-rename"
                        onClick={() => {
                          setDeletingId(null);
                          setRenamingId(env.id);
                          setRenameDraft(env.name);
                        }}
                        aria-label={`Rename ${env.name} environment`}
                      >
                        <Pencil size={13} aria-hidden="true" />
                        <span>Rename</span>
                      </button>
                      <button
                        className="env-action-configure"
                        onClick={() => onConfigureSources(project.id, env.id)}
                        aria-label={`Manage ${env.name} sources`}
                      >
                        <Settings2 size={13} aria-hidden="true" />
                        <span>Sources</span>
                      </button>
                      <button
                        className="env-action-delete"
                        onClick={() => {
                          setRenamingId(null);
                          setDeletingId(env.id);
                        }}
                        aria-label={`Delete ${env.name} environment`}
                      >
                        <Trash2 size={13} aria-hidden="true" />
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

function normalizeName(value: string) {
  return value.trim();
}

function isValidEnvironmentName(value: string) {
  return /^[A-Za-z0-9][A-Za-z0-9_-]{0,49}$/.test(normalizeName(value));
}
