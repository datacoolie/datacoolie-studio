import type { ReactNode } from "react";
import type { Environment, EnvironmentFreshness, Project } from "../shared/api/types";
import { useDrawerEscape } from "../shared/hooks/useDrawerEscape";
import { ContextBar } from "./ContextBar";
import { SidebarNav } from "./SidebarNav";
import type { CapabilityKey, ModuleKey, ModuleScope } from "./moduleRegistry";

interface AppShellProps {
  activeModule: ModuleKey;
  activeScope: ModuleScope;
  projects: Project[];
  environments: Environment[];
  selectedProjectId: number | null;
  selectedEnvironmentId: number | null;
  sidebarCollapsed: boolean;
  enabledCapabilities: ReadonlySet<CapabilityKey>;
  metadataSourceCount: number;
  logPathCount: number;
  freshness: EnvironmentFreshness | null;
  error: string | null;
  children: ReactNode;
  onNavigate: (module: ModuleKey) => void;
  onToggleSidebar: () => void;
  onProjectSelect: (projectId: number | null) => void;
  onOpenProject: (projectId: number) => void;
}

export function AppShell({
  activeModule,
  activeScope,
  projects,
  environments,
  selectedProjectId,
  selectedEnvironmentId,
  sidebarCollapsed,
  enabledCapabilities,
  metadataSourceCount,
  logPathCount,
  freshness,
  error,
  children,
  onNavigate,
  onToggleSidebar,
  onProjectSelect,
  onOpenProject
}: AppShellProps) {
  useDrawerEscape(
    onToggleSidebar,
    () => !sidebarCollapsed && window.matchMedia("(max-width: 620px)").matches
  );

  const shellClassName = [
    "app-shell",
    sidebarCollapsed ? "sidebar-collapsed" : "",
    !sidebarCollapsed ? "mobile-drawer-open" : ""
  ].filter(Boolean).join(" ");

  return (
    <div className={shellClassName}>
      <SidebarNav
        activeModule={activeModule}
        hasProject={Boolean(selectedProjectId)}
        hasEnvironment={Boolean(selectedEnvironmentId)}
        collapsed={sidebarCollapsed}
        enabledCapabilities={enabledCapabilities}
        onToggleCollapsed={onToggleSidebar}
        onNavigate={onNavigate}
      />
      {!sidebarCollapsed ? <button type="button" className="mobile-drawer-backdrop" aria-label="Close navigation" onClick={onToggleSidebar} /> : null}
      <main className="main-pane">
        <ContextBar
          activeModule={activeModule}
          scope={activeScope}
          projects={projects}
          environments={environments}
          selectedProjectId={selectedProjectId}
          selectedEnvironmentId={selectedEnvironmentId}
          metadataSourceCount={metadataSourceCount}
          logPathCount={logPathCount}
          freshness={freshness}
          onProjectSelect={onProjectSelect}
          onOpenProject={onOpenProject}
        />
        {error ? <div className="app-error">{error}</div> : null}
        <section className={`content-area content-${activeModule} content-scope-${activeScope}`}>{children}</section>
      </main>
    </div>
  );
}
