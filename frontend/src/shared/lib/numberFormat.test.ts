import { describe, expect, it } from "vitest";
import { compareMetricValues, formatAgeNumber, formatCompactNumber, formatExactBytes, formatExactNumber, formatTooltipNumber, metricDifference, presentNumber } from "./numberFormat";

describe("number presentation", () => {
  it.each([
    [1234.5678, "1,234.57"], [1234.5, "1,234.5"], [1234, "1,234"],
    ["9007199254740993.125", "9,007,199,254,740,993.13"],
    ["999.999", "1,000"], ["-0.125", "-0.13"], ["-0.004", "0"],
    ["1.234567e3", "1,234.57"], ["1e-7", "0"], [".126", "0.13"],
    ["9007199254740993", "9,007,199,254,740,993"], [null, "-"], [NaN, "-"],
  ])("rounds tooltip %s with grouping to %s without narrowing integer strings", (value, expected) => {
    expect(formatTooltipNumber(value)).toBe(expected);
  });
  it("allows one decimal and preserves the separate exact formatter", () => {
    expect(formatTooltipNumber("1234.567", 1)).toBe("1,234.6");
    expect(formatExactNumber("1234.567")).toBe("1,234.567");
  });
  it.each([
    [0, "0"], [999, "999"], [1000, "1k"], [1825, "1k8"],
    [1250, "1k3"], [-1825, "-1k8"], [999949, "999k9"],
    [999950, "1M"], [12345678, "12M3"], [1200000000, "1B2"],
    [1200000000000, "1T2"], [null, "-"], [Number.NaN, "-"],
  ])("supports opt-in infix compaction for %s", (value, display) => {
    expect(formatCompactNumber(value, "infix")).toBe(display);
  });

  it("keeps infix presentation exact for transported strings and semantic affixes", () => {
    expect(presentNumber("1825", { compactStyle: "infix", suffix: "/s" })).toEqual({ display: "1k8/s", exact: "1,825/s" });
    const value = "900719925474099312345678";
    expect(presentNumber(value, { compactStyle: "infix" })).toEqual({
      display: "900,719,925,474T1", exact: "900,719,925,474,099,312,345,678"
    });
    expect(formatCompactNumber(-0, "infix")).toBe("0");
    expect(formatCompactNumber(1.25, "infix")).toBe("1.3");
  });

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

  it("formats and sorts decimal-string metrics without narrowing them through Number", () => {
    const authored = "900719925474099312345678";
    expect(formatExactNumber(authored)).toBe("900,719,925,474,099,312,345,678");
    expect(formatCompactNumber(authored)).toBe("900,719,925,474.1T");
    expect(formatExactBytes(authored)).toBe("900,719,925,474,099,312,345,678 B");
    expect(compareMetricValues("900719925474099312345678", "900719925474099312345677")).toBe(1);
    expect(metricDifference("900719925474099312345678", "8")).toBe("900719925474099312345670");
  });

  it("keeps authored decimal text distinct from string counts", () => {
    expect(formatExactNumber("1.2300")).toBe("1.2300");
    expect(formatExactNumber("9007199254740993")).toBe("9,007,199,254,740,993");
    expect(formatExactNumber(null)).toBe("-");
  });
});
