import { describe, expect, it } from "vitest";
import { formatTimestampForDisplay, resolveIntlTimezone } from "./time";

describe("timezone display", () => {
  it("keeps supported IANA timezone names", () => {
    expect(resolveIntlTimezone("Asia/Bangkok")).toBe("Asia/Bangkok");
  });

  it("falls back safely when the server exposes a Windows timezone label", () => {
    const fallback = resolveIntlTimezone("SE Asia Standard Time");
    expect(() =>
      new Intl.DateTimeFormat("en-US", { timeZone: fallback }).format()
    ).not.toThrow();
    expect(() =>
      formatTimestampForDisplay("2026-07-23T06:00:00Z", "SE Asia Standard Time")
    ).not.toThrow();
  });
});
