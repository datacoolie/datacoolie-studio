import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CompactNumberValue } from "./CompactNumberValue";

describe("CompactNumberValue", () => {
  it("renders compact text while exposing the grouped exact value", () => {
    const markup = renderToStaticMarkup(<CompactNumberValue value={898500000} />);

    expect(markup).toContain(">898.5M</span>");
    expect(markup).toContain('title="898,500,000"');
    expect(markup).toContain('aria-label="898,500,000"');
  });

  it("retains semantic suffixes in both visible and exact values", () => {
    const markup = renderToStaticMarkup(<CompactNumberValue value={1250} suffix="/s" />);

    expect(markup).toContain(">1.3k/s</span>");
    expect(markup).toContain('title="1,250/s"');
  });
});
