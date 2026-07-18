import { CheckCircle2, CircleAlert, Database, FolderOpen, GitBranch, Layers3, LayoutDashboard, MoreHorizontal, Settings2, Trash2 } from "lucide-react";
import type { KeyboardEvent as ReactKeyboardEvent, ReactNode } from "react";
import { useEffect, useRef, useState } from "react";
import type { Environment, ProjectReferenceMapping, ProjectSummary } from "../../shared/api/types";
import { EmptyState } from "../../shared/components/EmptyState";
import type { ProjectSectionKey } from "../../app/moduleRegistry";
import { projectReadinessSummary } from "../../shared/environmentReadiness";
import { EnvironmentsPage } from "../environments/EnvironmentsPage";
import { ProjectOverviewPage } from "../project-overview/ProjectOverviewPage";
import { ProjectReferenceMappingsPage } from "./ProjectReferenceMappingsPage";

interface ProjectDetailPageProps {
  project: ProjectSummary | null;
  projectId: number | null;
  projectName: string | null;
  section: ProjectSectionKey;
  environments: Environment[];
  mappings: ProjectReferenceMapping[];
  busy: boolean;
  routeSearch?: string;
  onDeleteProject: (projectId: number) => Promise<void>;
  onSectionChange: (section: ProjectSectionKey) => void;
  onOpenEnvironment: (projectId: number, environmentId: number) => void;
  onConfigureSources: (projectId: number, environmentId: number) => void;
  onCreateEnvironment: (name: string) => Promise<number>;
  onDeleteEnvironment: (environmentId: number) => Promise<void>;
  onQuickCreateEnvironment: (projectId: number, name: string) => Promise<void>;
  onReloadMappings: () => Promise<void>;
  onCreateMapping: ProjectReferenceMappingsPageProps["onCreate"];
  onUpdateMapping: ProjectReferenceMappingsPageProps["onUpdate"];
  onDeleteMapping: ProjectReferenceMappingsPageProps["onDelete"];
}

type ProjectReferenceMappingsPageProps = Parameters<typeof ProjectReferenceMappingsPage>[0];

const projectSections: ProjectSectionKey[] = ["overview", "environments", "reference-mappings"];

function projectTabId(section: ProjectSectionKey) {
  return `project-tab-${section}`;
}

function projectPanelId(section: ProjectSectionKey) {
  return `project-panel-${section}`;
}

export function ProjectDetailPage({
  project,
  projectId,
  projectName,
  section,
  environments,
  mappings,
  busy,
  routeSearch,
  onDeleteProject,
  onSectionChange,
  onOpenEnvironment,
  onConfigureSources,
  onCreateEnvironment,
  onDeleteEnvironment,
  onQuickCreateEnvironment,
  onReloadMappings,
  onCreateMapping,
  onUpdateMapping,
  onDeleteMapping,
}: ProjectDetailPageProps) {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [actionsOpen, setActionsOpen] = useState(false);
  const actionsMenuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!actionsOpen) return;

    function handlePointerDown(event: PointerEvent) {
      const target = event.target;
      if (target instanceof Node && actionsMenuRef.current?.contains(target)) return;
      setActionsOpen(false);
      setConfirmDelete(false);
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      setActionsOpen(false);
      setConfirmDelete(false);
    }

    document.addEventListener("pointerdown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("pointerdown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [actionsOpen]);

  if (!project) {
    return <EmptyState title={busy ? "Loading project" : "Project not found"} />;
  }
  const currentProjectId = project.id;

  async function handleDeleteProject() {
    await onDeleteProject(currentProjectId);
    setConfirmDelete(false);
    setActionsOpen(false);
  }

  const readiness = projectReadinessSummary(project.environments);
  const readinessLabel = !readiness.total
    ? "Add an environment to begin"
    : readiness.needsMetadata
      ? `${readiness.needsMetadata} ${readiness.needsMetadata === 1 ? "environment needs" : "environments need"} metadata`
      : "All environments ready";

  return (
    <div className={`view-stack project-detail-page${section === "reference-mappings" ? " project-detail-page-reference-mappings" : ""}`}>
      <section className="table-panel project-detail-header-panel">
        <div className="project-detail-header">
          <div className="project-detail-title">
            <h2><span>Project</span>{project.name}</h2>
            {project.description ? <p>{project.description}</p> : null}
          </div>
          <div className="project-detail-actions">
            <div className="project-actions-menu" ref={actionsMenuRef}>
              <button
                className="icon-action project-actions-trigger"
                type="button"
                onClick={() => {
                  setActionsOpen((value) => !value);
                  setConfirmDelete(false);
                }}
                title="Project actions"
                aria-label="Project actions"
                aria-expanded={actionsOpen}
              >
                <MoreHorizontal size={16} />
              </button>
              {actionsOpen ? (
                <div className="project-actions-popover" role="menu" aria-label="Project actions">
                  <div className="project-actions-popover-header">
                    <span>Project actions</span>
                  </div>
                  {confirmDelete ? (
                    <div className="project-delete-confirm">
                      <span>Delete this project?</span>
                      <button type="button" className="text-action danger" disabled={busy} onClick={handleDeleteProject}>Delete</button>
                      <button type="button" className="text-action" onClick={() => setConfirmDelete(false)}>Cancel</button>
                    </div>
                  ) : (
                    <button className="project-action-danger" type="button" onClick={() => setConfirmDelete(true)} role="menuitem">
                      <Trash2 size={13} />
                      <span>Delete project</span>
                    </button>
                  )}
                </div>
              ) : null}
            </div>
          </div>
        </div>

        <div className="project-detail-summary" aria-label="Project setup summary">
          <span className={`project-detail-readiness ${readiness.needsMetadata ? "needs-metadata" : "ready"}`}>
            {readiness.needsMetadata ? <CircleAlert size={14} /> : <CheckCircle2 size={14} />}
            {readinessLabel}
          </span>
          <span><Layers3 size={14} />{project.environment_count} environments</span>
          <span><Database size={14} />{project.metadata_source_count} metadata sources</span>
          <span><FolderOpen size={14} />{project.etl_log_path_count} log paths <em>optional</em></span>
        </div>

        <div className="project-local-tabs" role="tablist" aria-label="Project sections">
          <ProjectTab section="overview" label="Overview" active={section === "overview"} onClick={onSectionChange} icon={<LayoutDashboard size={14} aria-hidden="true" />} />
          <ProjectTab section="environments" label="Environments" active={section === "environments"} onClick={onSectionChange} icon={<Settings2 size={14} aria-hidden="true" />} />
          <ProjectTab section="reference-mappings" label="Reference mappings" compactLabel="Mappings" active={section === "reference-mappings"} onClick={onSectionChange} icon={<GitBranch size={14} aria-hidden="true" />} />
        </div>
      </section>

      <ProjectSectionPanel section="overview" active={section === "overview"}>
        {section === "overview" ? (
          <ProjectOverviewPage
            project={project}
            busy={busy}
            mappingCount={mappings.length}
            onOpenEnvironment={onOpenEnvironment}
            onOpenEnvironments={(id) => {
              if (id === project.id) onSectionChange("environments");
            }}
            onConfigureSources={onConfigureSources}
            onOpenReferenceMappings={() => onSectionChange("reference-mappings")}
            onQuickCreateEnvironment={onQuickCreateEnvironment}
          />
        ) : null}
      </ProjectSectionPanel>

      <ProjectSectionPanel section="environments" active={section === "environments"}>
        {section === "environments" ? (
          <EnvironmentsPage
            project={project}
            busy={busy}
            onCreateEnvironment={onCreateEnvironment}
            onDeleteEnvironment={onDeleteEnvironment}
            onOpenEnvironment={onOpenEnvironment}
            onConfigureSources={onConfigureSources}
          />
        ) : null}
      </ProjectSectionPanel>

      <ProjectSectionPanel section="reference-mappings" active={section === "reference-mappings"}>
        {section === "reference-mappings" ? (
          <ProjectReferenceMappingsPage
            projectId={projectId}
            projectName={projectName}
            environments={environments}
            mappings={mappings}
            busy={busy}
            routeSearch={routeSearch}
            onReload={onReloadMappings}
            onCreate={onCreateMapping}
            onUpdate={onUpdateMapping}
            onDelete={onDeleteMapping}
          />
        ) : null}
      </ProjectSectionPanel>
    </div>
  );
}

function ProjectTab({
  section,
  label,
  compactLabel,
  active,
  onClick,
  icon,
}: {
  section: ProjectSectionKey;
  label: string;
  compactLabel?: string;
  active: boolean;
  onClick: (section: ProjectSectionKey) => void;
  icon: ReactNode;
}) {
  function handleKeyDown(event: ReactKeyboardEvent<HTMLButtonElement>) {
    const currentIndex = projectSections.indexOf(section);
    let nextSection: ProjectSectionKey | null = null;
    if (event.key === "ArrowRight") nextSection = projectSections[(currentIndex + 1) % projectSections.length];
    if (event.key === "ArrowLeft") nextSection = projectSections[(currentIndex - 1 + projectSections.length) % projectSections.length];
    if (event.key === "Home") nextSection = projectSections[0];
    if (event.key === "End") nextSection = projectSections[projectSections.length - 1];
    if (!nextSection) return;

    event.preventDefault();
    onClick(nextSection);
    window.requestAnimationFrame(() => document.getElementById(projectTabId(nextSection!))?.focus());
  }

  return (
    <button
      type="button"
      role="tab"
      id={projectTabId(section)}
      aria-controls={projectPanelId(section)}
      aria-label={label}
      aria-selected={active}
      tabIndex={active ? 0 : -1}
      className={`project-local-tab project-local-tab-${section}${active ? " active" : ""}`}
      onClick={() => onClick(section)}
      onKeyDown={handleKeyDown}
    >
      {icon}
      <span className="project-tab-label-full">{label}</span>
      {compactLabel ? <span className="project-tab-label-compact" aria-hidden="true">{compactLabel}</span> : null}
    </button>
  );
}

function ProjectSectionPanel({ section, active, children }: { section: ProjectSectionKey; active: boolean; children: ReactNode }) {
  return (
    <section
      id={projectPanelId(section)}
      className={`project-section-panel project-section-panel-${section}`}
      role="tabpanel"
      aria-labelledby={projectTabId(section)}
      hidden={!active}
    >
      {children}
    </section>
  );
}
