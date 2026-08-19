import { describe, expect, it } from "vitest";
import { formatAgeNumber, formatCompactNumber, formatExactBytes, formatExactNumber, presentNumber } from "./monitoringNumberFormat";

describe("monitoring number presentation", () => {
  it.each([
    [999, "999", "999"],
    [1000, "1k", "1,000"],
    [1250, "1.3k", "1,250"],
    [999949, "999.9k", "999,949"],
    [999950, "1M", "999,950"],
    [12345678, "12.3M", "12,345,678"],
    [898500000, "898.5M", "898,500,000"],
    [1200000000000, "1.2T", "1,200,000,000,000"]
  ])("formats %s as %s with exact value %s", (value, display, exact) => {
    expect(formatCompactNumber(value)).toBe(display);
    expect(formatExactNumber(value)).toBe(exact);
  });

  it("preserves signs and handles zero and non-finite values", () => {
    expect(formatCompactNumber(-1250)).toBe("-1.3k");
    expect(formatCompactNumber(-0)).toBe("0");
    expect(formatExactNumber(-0)).toBe("0");
    expect(formatCompactNumber(Number.NaN)).toBe("-");
    expect(formatExactNumber(Number.POSITIVE_INFINITY)).toBe("-");
  });

  it("formats age values with grouping and at most one decimal place", () => {
    expect(formatAgeNumber(1234.56)).toBe("1,234.6");
    expect(formatAgeNumber(12)).toBe("12");
    expect(formatAgeNumber(Number.NaN)).toBe("-");
  });

  it("retains semantic affixes in compact and exact forms", () => {
    expect(presentNumber(1250, { suffix: "/s" })).toEqual({ display: "1.3k/s", exact: "1,250/s" });
    expect(formatExactBytes(1024)).toBe("1,024 B");
  });
});
