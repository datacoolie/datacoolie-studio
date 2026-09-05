import { describe, expect, it } from "vitest";
import { fitMetadataColumnHeader, normalizeMetadataColumnWidth } from "./metadataColumnHeaderLayout";

describe("metadata column header sizing", () => {
  it("keeps existing width when the label and controls already fit", () => {
    expect(fitMetadataColumnHeader(180, 120)).toBe(180);
    expect(fitMetadataColumnHeader(180, 150)).toBe(180);
  });

  it("extends only by the missing space, including padding, controls and border", () => {
    expect(fitMetadataColumnHeader(180, 165)).toBe(195);
    expect(fitMetadataColumnHeader(180, 165.2)).toBe(196);
  });

  it("does not accumulate width when fitting repeatedly", () => {
    const fitted = fitMetadataColumnHeader(180, 165);
    expect(fitMetadataColumnHeader(fitted, 165)).toBe(fitted);
  });

  it("preserves manual widths without reserving filter space", () => {
    expect(normalizeMetadataColumnWidth(100)).toBe(100);
    expect(normalizeMetadataColumnWidth(180 + 10)).toBe(190);
    expect(normalizeMetadataColumnWidth(30)).toBe(72);
    expect(normalizeMetadataColumnWidth(Number.NaN)).toBe(72);
  });
});
