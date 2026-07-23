import { Database, FolderOpen, Plus, Server } from "lucide-react";
import { FormEvent, useState } from "react";
import { BrandLogo } from "../../app/BrandLogo";
import type { Environment, Project, SourcePath } from "../../shared/api/domainTypes";

interface WorkspacePanelProps {
  projects: Project[];
  environments: Environment[];
  metadataSources: SourcePath[];
  logPaths: SourcePath[];
  selectedProjectId: number | null;
  selectedEnvironmentId: number | null;
  busy: boolean;
  onProjectSelect: (id: number | null) => void;
  onEnvironmentSelect: (id: number | null) => void;
  onCreateProject: (name: string) => Promise<void>;
  onCreateEnvironment: (name: string) => Promise<void>;
  onAddMetadataSource: (uri: string, label?: string) => Promise<void>;
  onAddLogPath: (uri: string, label?: string) => Promise<void>;
}

export function WorkspacePanel({
  projects,
  environments,
  metadataSources,
  logPaths,
  selectedProjectId,
  selectedEnvironmentId,
  busy,
  onProjectSelect,
  onEnvironmentSelect,
  onCreateProject,
  onCreateEnvironment,
  onAddMetadataSource,
  onAddLogPath
}: WorkspacePanelProps) {
  const [projectName, setProjectName] = useState("");
  const [environmentName, setEnvironmentName] = useState("");
  const [metadataUri, setMetadataUri] = useState("");
  const [metadataLabel, setMetadataLabel] = useState("");
  const [logUri, setLogUri] = useState("");
  const [logLabel, setLogLabel] = useState("");

  async function submitProject(event: FormEvent) {
    event.preventDefault();
    if (!projectName.trim()) return;
    await onCreateProject(projectName.trim());
    setProjectName("");
  }

  async function submitEnvironment(event: FormEvent) {
    event.preventDefault();
    if (!environmentName.trim()) return;
    await onCreateEnvironment(environmentName.trim());
    setEnvironmentName("");
  }

  async function submitMetadata(event: FormEvent) {
    event.preventDefault();
    if (!metadataUri.trim()) return;
    await onAddMetadataSource(metadataUri.trim(), metadataLabel.trim() || undefined);
    setMetadataUri("");
    setMetadataLabel("");
  }

  async function submitLogPath(event: FormEvent) {
    event.preventDefault();
    if (!logUri.trim()) return;
    await onAddLogPath(logUri.trim(), logLabel.trim() || undefined);
    setLogUri("");
    setLogLabel("");
  }

  return (
    <aside className="workspace-panel">
      <div className="brand-row">
        <BrandLogo />
        <div>
          <h1>DataCoolie Studio</h1>
          <span>Local metadata and run view</span>
        </div>
      </div>

      <section className="sidebar-section">
        <div className="section-heading">
          <Server size={16} />
          <span>Workspace</span>
        </div>
        <label>
          Project
          <select
            value={selectedProjectId ?? ""}
            onChange={(event) => onProjectSelect(event.target.value ? Number(event.target.value) : null)}
          >
            <option value="">Select project</option>
            {projects.map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </select>
        </label>
        <form className="inline-form" onSubmit={submitProject}>
          <input value={projectName} onChange={(event) => setProjectName(event.target.value)} placeholder="New project" />
          <button type="submit" title="Create project" disabled={busy || !projectName.trim()}>
            <Plus size={16} />
          </button>
        </form>
        <label>
          Environment
          <select
            value={selectedEnvironmentId ?? ""}
            onChange={(event) => onEnvironmentSelect(event.target.value ? Number(event.target.value) : null)}
            disabled={!selectedProjectId}
          >
            <option value="">Select environment</option>
            {environments.map((environment) => (
              <option key={environment.id} value={environment.id}>
                {environment.name}
              </option>
            ))}
          </select>
        </label>
        <form className="inline-form" onSubmit={submitEnvironment}>
          <input
            value={environmentName}
            onChange={(event) => setEnvironmentName(event.target.value)}
            placeholder="New env"
            disabled={!selectedProjectId}
          />
          <button type="submit" title="Create environment" disabled={busy || !selectedProjectId || !environmentName.trim()}>
            <Plus size={16} />
          </button>
        </form>
      </section>

      <section className="sidebar-section">
        <div className="section-heading">
          <Database size={16} />
          <span>Metadata</span>
        </div>
        <form className="stack-form" onSubmit={submitMetadata}>
          <input
            value={metadataUri}
            onChange={(event) => setMetadataUri(event.target.value)}
            placeholder="JSON, YAML, XLSX path"
            disabled={!selectedEnvironmentId}
          />
          <input
            value={metadataLabel}
            onChange={(event) => setMetadataLabel(event.target.value)}
            placeholder="Label"
            disabled={!selectedEnvironmentId}
          />
          <button type="submit" disabled={busy || !selectedEnvironmentId || !metadataUri.trim()}>
            <Plus size={16} />
            <span>Add metadata</span>
          </button>
        </form>
        <SourceList items={metadataSources} empty="No metadata source" />
      </section>

      <section className="sidebar-section">
        <div className="section-heading">
          <FolderOpen size={16} />
          <span>ETL Logs</span>
        </div>
        <form className="stack-form" onSubmit={submitLogPath}>
          <input
            value={logUri}
            onChange={(event) => setLogUri(event.target.value)}
            placeholder="logs path"
            disabled={!selectedEnvironmentId}
          />
          <input
            value={logLabel}
            onChange={(event) => setLogLabel(event.target.value)}
            placeholder="Label"
            disabled={!selectedEnvironmentId}
          />
          <button type="submit" disabled={busy || !selectedEnvironmentId || !logUri.trim()}>
            <Plus size={16} />
            <span>Add log path</span>
          </button>
        </form>
        <SourceList items={logPaths} empty="No log source" />
      </section>
    </aside>
  );
}

function SourceList({ items, empty }: { items: SourcePath[]; empty: string }) {
  if (!items.length) return <div className="source-empty">{empty}</div>;
  return (
    <div className="source-list">
      {items.map((item) => (
        <div key={item.id} className="source-item" title={item.uri}>
          <span>{item.label || basename(item.uri)}</span>
          <small>{item.enabled ? "enabled" : "disabled"}</small>
        </div>
      ))}
    </div>
  );
}

function basename(uri: string) {
  const normalized = uri.replace(/\\/g, "/").replace(/\/$/, "");
  return normalized.split("/").pop() || uri;
}
