import type { ReactNode } from "react";
import type { EnvironmentContext } from "../shared/api/domainTypes";
import { useDrawerEscape } from "../shared/hooks/useDrawerEscape";
import { ContextBar } from "./ContextBar";
import { SidebarNav } from "./SidebarNav";
import type { CapabilityKey, ModuleKey, ModuleScope } from "./moduleRegistry";

interface AppShellProps {
  activeModule: ModuleKey;
  activeScope: ModuleScope;
  project: { id: number; name: string } | null;
  environment: { id: number; name: string } | null;
  sidebarCollapsed: boolean;
  enabledCapabilities: ReadonlySet<CapabilityKey>;
  metadataSourceCount: number;
  logPathCount: number;
  freshness: EnvironmentContext["freshness"] | null;
  timezoneName: string | null;
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
  project,
  environment,
  sidebarCollapsed,
  enabledCapabilities,
  metadataSourceCount,
  logPathCount,
  freshness,
  timezoneName,
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
        hasProject={Boolean(project)}
        hasEnvironment={Boolean(environment)}
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
          project={project}
          environment={environment}
          metadataSourceCount={metadataSourceCount}
          logPathCount={logPathCount}
          freshness={freshness}
          timezoneName={timezoneName}
          onProjectSelect={onProjectSelect}
          onOpenProject={onOpenProject}
        />
        {error ? <div className="app-error">{error}</div> : null}
        <section className={`content-area content-${activeModule} content-scope-${activeScope}`}>{children}</section>
      </main>
    </div>
  );
}
