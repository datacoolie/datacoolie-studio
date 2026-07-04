import type { MouseEvent } from "react";
import { ChevronsLeft, ChevronsRight, Menu } from "lucide-react";
import { BrandLogo } from "./BrandLogo";
import { isModuleEnabled, moduleGroups, modules, type CapabilityKey, type ModuleKey } from "./moduleRegistry";

interface SidebarNavProps {
  activeModule: ModuleKey;
  hasProject: boolean;
  hasEnvironment: boolean;
  collapsed: boolean;
  enabledCapabilities: ReadonlySet<CapabilityKey>;
  onToggleCollapsed: () => void;
  onNavigate: (module: ModuleKey) => void;
}

export function SidebarNav({ activeModule, hasProject, hasEnvironment, collapsed, enabledCapabilities, onToggleCollapsed, onNavigate }: SidebarNavProps) {
  const primaryGroups = moduleGroups.filter((group) => group !== "Studio");
  const studioGroups = moduleGroups.filter((group) => group === "Studio");
  const navigateFromMenu = (module: ModuleKey) => {
    onNavigate(module);
    if (!collapsed && window.matchMedia("(max-width: 620px)").matches) {
      onToggleCollapsed();
    }
  };
  const closeMobileDrawerFromBlank = (event: MouseEvent<HTMLElement>) => {
    if (collapsed || !window.matchMedia("(max-width: 620px)").matches) return;
    if (event.target instanceof Element && event.target.closest("button")) return;
    onToggleCollapsed();
  };

  return (
    <aside className={collapsed ? "sidebar-nav collapsed" : "sidebar-nav"} onClick={closeMobileDrawerFromBlank}>
      <div className="brand-row">
        <BrandLogo />
        <div className="brand-copy">
          <h1>DataCoolie Studio</h1>
          <span>Local data workbench</span>
        </div>
      </div>

      <nav className="module-nav" aria-label="Studio modules">
        {primaryGroups.map((group) => (
          <ModuleGroupNav
            key={group}
            group={group}
            activeModule={activeModule}
            collapsed={collapsed}
            hasProject={hasProject}
            hasEnvironment={hasEnvironment}
            enabledCapabilities={enabledCapabilities}
            onNavigate={navigateFromMenu}
          />
        ))}
      </nav>

      <nav className="module-nav module-nav-bottom" aria-label="Studio settings">
        {studioGroups.map((group) => (
          <ModuleGroupNav
            key={group}
            group={group}
            activeModule={activeModule}
            collapsed={collapsed}
            hasProject={hasProject}
            hasEnvironment={hasEnvironment}
            enabledCapabilities={enabledCapabilities}
            onNavigate={navigateFromMenu}
          />
        ))}
      </nav>

      <button
        className="sidebar-toggle"
        type="button"
        onClick={onToggleCollapsed}
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        aria-expanded={!collapsed}
        title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
      >
        <span className="toggle-icon-desktop">{collapsed ? <ChevronsRight size={16} /> : <ChevronsLeft size={16} />}</span>
        <span className="toggle-icon-mobile"><Menu size={16} /></span>
        {!collapsed ? <span className="toggle-label">Collapse</span> : null}
      </button>
    </aside>
  );
}

function ModuleGroupNav({
  group,
  activeModule,
  collapsed,
  hasProject,
  hasEnvironment,
  enabledCapabilities,
  onNavigate
}: {
  group: string;
  activeModule: ModuleKey;
  collapsed: boolean;
  hasProject: boolean;
  hasEnvironment: boolean;
  enabledCapabilities: ReadonlySet<CapabilityKey>;
  onNavigate: (module: ModuleKey) => void;
}) {
  return (
    <div className="module-group">
      {!collapsed ? <span className="module-group-label">{group}</span> : null}
      {modules
        .filter((module) => module.group === group)
        .filter((module) => isModuleEnabled(module, enabledCapabilities))
        .map((module) => {
          const Icon = module.icon;
          const disabled = (module.scope === "project" && !hasProject) || (module.requiresEnvironment && !hasEnvironment);
          return (
            <button
              key={module.key}
              className={activeModule === module.key ? "active" : ""}
              disabled={disabled}
              aria-label={module.label}
              onClick={() => onNavigate(module.key)}
              title={module.label}
            >
              <Icon size={17} />
              {!collapsed ? <span>{module.label}</span> : null}
            </button>
          );
        })}
    </div>
  );
}
