import { describe, expect, it } from "vitest";
import { formatAbsoluteTime, formatRelativeTime, formatTimestampForDisplay, resolveIntlTimezone } from "./time";

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

  it("treats API timestamps without a suffix as UTC instants", () => {
    expect(
      formatRelativeTime(
        "2026-07-28T14:40:00",
        Date.parse("2026-07-28T14:40:30Z"),
      ),
    ).toBe("just now");
  });

  it("formats API timestamps in the configured Studio timezone", () => {
    expect(
      formatAbsoluteTime("2026-07-28T14:40:00", "Asia/Ho_Chi_Minh"),
    ).toContain("2026-07-28 21:40:00");
  });
});
