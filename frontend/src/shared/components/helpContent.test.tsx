import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { escapeHelpHtml, helpContentHtml, type HelpContent } from "./helpContent";
import { Help, HelpContentView, HelpProvider } from "./Help";
import { CompactNumberValue } from "./CompactNumberValue";
import { DataTable } from "./DataTable";
import { claimHelp, dismissActiveHelp, releaseHelp } from "./helpCoordinator";

describe("shared help content and arbitration", () => {
  it("keeps info icons on visuals and record help on content, including long explanations", () => {
    const longTitle = "Exact record context and measurement coverage. ".repeat(12);
    const record = renderToStaticMarkup(<Help title={longTitle}>Record</Help>);
    expect(record).toContain(longTitle);
    expect(record).not.toContain("help-info-button");
    const table = renderToStaticMarkup(<HelpProvider><DataTable rows={[{id:"run-a",value:12}]} columns={[{key:"id",label:"Run",headerTitle:longTitle},{key:"value",label:"Value"}]} /></HelpProvider>);
    expect(table).not.toContain("help-info-button");
    expect(table).toContain("run-a");
  });
  it("escapes HTML without losing exact whitespace and transported numeric strings", () => {
    const content: HelpContent = {title: '<img src=x onerror="alert(1)">', context: " \tRun  A\n ", metrics: [{label:"Rows",value:"9,007,199,254,740,993"},{label:"Duration",value:"12.50 seconds"},{label:"Missing",value:"Unavailable"},{label:"Recorded",value:"0"}]};
    const html = helpContentHtml(content);
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
    expect(html).toContain(" \tRun  A\n ");
    for (const metric of content.metrics!) expect(html).toContain(metric.value);
    const react = renderToStaticMarkup(<HelpContentView content={content} />);
    expect(react).toContain("9,007,199,254,740,993");
    expect(react).not.toContain("<img");
    expect(escapeHelpHtml("'&<>\"" )).toBe("&#39;&amp;&lt;&gt;&quot;");
  });

  it("opts shared numbers into help only inside the supplied boundary", () => {
    const external = renderToStaticMarkup(<CompactNumberValue value="9007199254740993" />);
    const monitoring = renderToStaticMarkup(<HelpProvider><CompactNumberValue value="9007199254740993" /></HelpProvider>);
    expect(external).toContain('title="9,007,199,254,740,993"');
    expect(monitoring).not.toContain(' title=');
    expect(monitoring).toContain('aria-description="9,007,199,254,740,993"');
    expect(monitoring).not.toContain("tabindex");
    expect(renderToStaticMarkup(<Help as="button" title="Copy exact ID" type="button">Copy</Help>)).toContain('aria-description="Copy exact ID"');
  });

  it("renders series value colors consistently and rejects arbitrary CSS", () => {
    const content: HelpContent = { metrics: [
      { label: "Read", value: "1,234", valueColor: "#2563eb" },
      { label: "Invalid", value: "0", valueColor: 'red; background:url("x")' },
    ] };
    for (const html of [helpContentHtml(content), renderToStaticMarkup(<HelpContentView content={content} />)]) {
      expect(html).toContain('style="color:#2563eb"');
      expect(html).not.toContain("background");
      expect(html).toContain('class="help-tone-neutral">0</dd>');
    }
  });

  it("marks path values for natural full-width wrapping in both tooltip renderers", () => {
    const content: HelpContent = { metrics: [{ label: "Storage path", value: "./data/output/orders_delta", wrap: "path" }] };
    expect(helpContentHtml(content)).toContain('<div class="help-metric-path">');
    expect(renderToStaticMarkup(<HelpContentView content={content} />)).toContain('class="help-metric-path"');
  });

  it("accepts only allowlisted theme color tokens for tooltip values", () => {
    const content: HelpContent = { metrics: [
      { label: "Added", value: "4", valueColor: "var(--color-success)" },
      { label: "Invalid", value: "0", valueColor: "var(--arbitrary-token)" },
    ] };
    const html = helpContentHtml(content);
    expect(html).toContain('style="color:var(--color-success)"');
    expect(html).not.toContain("arbitrary-token");
  });

  it("closes the previous owner and consumes only the first Escape", () => {
    const first = {close: vi.fn()};
    const second = {close: vi.fn()};
    claimHelp(first); claimHelp(second);
    expect(first.close).toHaveBeenCalledOnce();
    releaseHelp(first);
    const event = {preventDefault:vi.fn(),stopPropagation:vi.fn(),stopImmediatePropagation:vi.fn()} as unknown as KeyboardEvent;
    expect(dismissActiveHelp(event)).toBe(true);
    expect(second.close).toHaveBeenCalledWith(true);
    expect(event.preventDefault).toHaveBeenCalledOnce();
    expect(event.stopImmediatePropagation).toHaveBeenCalledOnce();
    expect(dismissActiveHelp(event)).toBe(false);
    expect(claimHelp(first)).toBe(false);
    expect(dismissActiveHelp({defaultPrevented:true} as KeyboardEvent)).toBe(false);
    releaseHelp(first);
  });
});
