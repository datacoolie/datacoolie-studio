import type { ReactNode } from "react";
import type { Environment, EnvironmentFreshness, Project } from "../shared/api/types";
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
  onEnvironmentSelect: (environmentId: number | null) => void;
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
  onEnvironmentSelect
}: AppShellProps) {
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
          onEnvironmentSelect={onEnvironmentSelect}
        />
        {error ? <div className="app-error">{error}</div> : null}
        <section className={`content-area content-${activeModule} content-scope-${activeScope}`}>{children}</section>
      </main>
    </div>
  );
}
