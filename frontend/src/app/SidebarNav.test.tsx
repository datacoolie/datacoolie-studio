import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { SidebarNav } from "./SidebarNav";

function renderSidebar(hasEnvironment: boolean) {
  return renderToStaticMarkup(
    <SidebarNav
      activeModule={hasEnvironment ? "overview" : "projects"}
      hasProject={true}
      hasEnvironment={hasEnvironment}
      collapsed={false}
      enabledCapabilities={new Set(["metadata", "master-data"])}
      onToggleCollapsed={vi.fn()}
      onNavigate={vi.fn()}
    />,
  );
}

describe("SidebarNav environment context", () => {
  it("hides environment navigation at project level", () => {
    const markup = renderSidebar(false);

    expect(markup).toContain('aria-label="Projects"');
    expect(markup).not.toContain(">Environment</span>");
    expect(markup).not.toContain('aria-label="Overview"');
    expect(markup).not.toContain('aria-label="Sources"');
  });

  it("shows environment navigation inside an environment", () => {
    const markup = renderSidebar(true);

    expect(markup).toContain(">Environment</span>");
    expect(markup).toContain('aria-label="Overview"');
    expect(markup).toContain('aria-label="Sources"');
  });
});
