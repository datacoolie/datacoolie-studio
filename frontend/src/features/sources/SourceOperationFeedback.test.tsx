import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  SourceOperationIcon,
  SourceOperationPill,
  sourceOperationStatusSlot,
} from "./SourceOperationFeedback";

describe("SourceOperationFeedback", () => {
  it("renders the working icon directly as an SVG so compact button span rules cannot hide it", () => {
    const markup = renderToStaticMarkup(<SourceOperationIcon />);

    expect(markup).toContain("<svg");
    expect(markup).toContain('class="lucide lucide-loader-circle source-operation-icon"');
    expect(markup).not.toContain('<span class="source-operation-icon"');
  });

  it("renders an accessible operation status", () => {
    const markup = renderToStaticMarkup(
      <SourceOperationPill action="validate" />,
    );

    expect(markup).toContain('role="status"');
    expect(markup).toContain("Validating");
  });

  it("maps each operation to the status it temporarily replaces", () => {
    expect(sourceOperationStatusSlot("validate")).toBe("read");
    expect(sourceOperationStatusSlot("sync")).toBe("cache");
    expect(sourceOperationStatusSlot("delete")).toBe("all");
    expect(sourceOperationStatusSlot("retry")).toBe("all");
  });

  it("renders retry progress with an accessible status", () => {
    const markup = renderToStaticMarkup(
      <SourceOperationPill action="retry" />,
    );

    expect(markup).toContain("Retrying");
    expect(markup).toContain("is-retry");
  });
});
