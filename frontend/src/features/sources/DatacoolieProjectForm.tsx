import { FolderOpen, Settings2 } from "lucide-react";
import { useState, type FormEvent } from "react";

import type { DatacoolieProjectSourceImportInput } from "../../shared/api/contractTypes";
import type { StorageBinding } from "../../shared/api/domainTypes";
import { LOCAL_STORAGE_BINDING } from "./StorageLocationFields";
import { ManualSourceForm } from "./ManualSourceForm";
import { ProjectSourceForm } from "./ProjectSourceForm";
import { sourcePathValues, type ManualSourceKind } from "./sourceSetupModel";

export type DatacoolieProjectFormProps = {
  busy: boolean;
  disabled: boolean;
  onImportMetadataSources: (uri: string, label?: string, storage?: StorageBinding) => Promise<boolean>;
  onImportDatacoolieProjectSources: (payload: DatacoolieProjectSourceImportInput) => Promise<boolean>;
  onAddSqlSource: (uri: string, label?: string, storage?: StorageBinding) => Promise<boolean>;
  onAddCodeArtifact: (uri: string, label?: string, sourceConfig?: Record<string, unknown>, storage?: StorageBinding) => Promise<boolean>;
};

export function DatacoolieProjectForm({
  busy,
  disabled,
  onImportMetadataSources,
  onImportDatacoolieProjectSources,
  onAddSqlSource,
  onAddCodeArtifact,
}: DatacoolieProjectFormProps) {
  const [mode, setMode] = useState<"project" | "manual">("project");
  const [projectUri, setProjectUri] = useState("");
  const [metadataSubpath, setMetadataSubpath] = useState("");
  const [sqlSubpaths, setSqlSubpaths] = useState([""]);
  const [codeSubpaths, setCodeSubpaths] = useState([""]);
  const [manualKind, setManualKind] = useState<ManualSourceKind>("metadata");
  const [manualUri, setManualUri] = useState("");
  const [manualLabel, setManualLabel] = useState("");
  const [artifactType, setArtifactType] = useState("directory");
  const [moduleRoots, setModuleRoots] = useState("");
  const [modulePrefix, setModulePrefix] = useState("");
  const [manualStorage, setManualStorage] = useState<StorageBinding>(LOCAL_STORAGE_BINDING);
  const [projectStorage, setProjectStorage] = useState<StorageBinding>(LOCAL_STORAGE_BINDING);

  async function submitProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!projectUri.trim()) return;
    await onImportDatacoolieProjectSources({
      project_uri: projectUri.trim(),
      metadata_subpath: metadataSubpath.trim() || undefined,
      sql_subpaths: sourcePathValues(sqlSubpaths),
      code_subpaths: sourcePathValues(codeSubpaths),
      code_discovery_mode: "auto",
      storage: projectStorage,
    });
  }

  async function submitManual(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!manualUri.trim()) return;
    const added = manualKind === "metadata"
      ? await onImportMetadataSources(manualUri.trim(), manualLabel.trim() || undefined, manualStorage)
      : manualKind === "sql"
        ? await onAddSqlSource(manualUri.trim(), manualLabel.trim() || undefined, manualStorage)
        : await onAddCodeArtifact(manualUri.trim(), manualLabel.trim() || undefined, {
            artifact_type: artifactType,
            module_roots: moduleRoots
              .split(",")
              .map((value) => value.trim())
              .filter(Boolean),
            ...(modulePrefix.trim() ? { module_prefix: modulePrefix.trim() } : {}),
          }, manualStorage);
    if (!added) return;
    setManualUri("");
    setManualLabel("");
  }

  return (
    <div className="source-config-card">
      <div className="source-config-tabs" role="tablist" aria-label="Project source mode">
        <button id="project-source-project-tab" type="button" role="tab" aria-selected={mode === "project"} aria-controls="project-source-project-panel" tabIndex={mode === "project" ? 0 : -1} className={mode === "project" ? "active" : ""} onClick={() => setMode("project")}>
          <FolderOpen size={14} />
          Project root
        </button>
        <button id="project-source-manual-tab" type="button" role="tab" aria-selected={mode === "manual"} aria-controls="project-source-manual-panel" tabIndex={mode === "manual" ? 0 : -1} className={mode === "manual" ? "active" : ""} onClick={() => setMode("manual")}>
          <Settings2 size={14} />
          Manual path
        </button>
      </div>

      {mode === "project" ? (
        <ProjectSourceForm
          busy={busy}
          disabled={disabled}
          projectUri={projectUri}
          metadataSubpath={metadataSubpath}
          sqlSubpaths={sqlSubpaths}
          codeSubpaths={codeSubpaths}
          projectStorage={projectStorage}
          onProjectUriChange={setProjectUri}
          onMetadataSubpathChange={setMetadataSubpath}
          onSqlSubpathsChange={setSqlSubpaths}
          onCodeSubpathsChange={setCodeSubpaths}
          onProjectStorageChange={setProjectStorage}
          onSubmit={submitProject}
        />
      ) : (
        <ManualSourceForm
          busy={busy}
          disabled={disabled}
          manualKind={manualKind}
          manualUri={manualUri}
          manualLabel={manualLabel}
          artifactType={artifactType}
          moduleRoots={moduleRoots}
          modulePrefix={modulePrefix}
          manualStorage={manualStorage}
          onManualKindChange={setManualKind}
          onManualUriChange={setManualUri}
          onManualLabelChange={setManualLabel}
          onArtifactTypeChange={setArtifactType}
          onModuleRootsChange={setModuleRoots}
          onModulePrefixChange={setModulePrefix}
          onManualStorageChange={setManualStorage}
          onSubmit={submitManual}
        />
      )}
    </div>
  );
}
