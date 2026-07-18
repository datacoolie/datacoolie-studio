import { describe, expect, it } from "vitest";
import {
  defaultPerformanceEfficiencyScope,
  filterPerformanceEfficiencyRows,
  performancePressureIntent,
  sortPerformanceRows
} from "./performancePageModel";

describe("performance page model", () => {
  it("sorts the full collection before callers paginate it", () => {
    const rows = [{ duration_seconds: 2 }, { duration_seconds: 30 }, { duration_seconds: 10 }];
    const sorted = sortPerformanceRows(rows, { sortBy: "duration_seconds", sortDir: "desc" });
    expect(sorted.map((row) => row.duration_seconds)).toEqual([30, 10, 2]);
  });

  it("separates pipeline and maintenance workload evidence", () => {
    const rows = [{ operation_type: "etl" }, { operation_type: "replay" }, { operation_type: "maintenance" }];
    expect(filterPerformanceEfficiencyRows(rows, "etl")).toHaveLength(2);
    expect(filterPerformanceEfficiencyRows(rows, "maintenance")).toEqual([{ operation_type: "maintenance" }]);
    expect(defaultPerformanceEfficiencyScope(rows)).toBe("etl");
  });

  it("does not mark high relative but low absolute pressure as healthy", () => {
    expect(performancePressureIntent(58, 11.8)).toBe("neutral");
    expect(performancePressureIntent(6, 35)).toBe("warning");
    expect(performancePressureIntent(12, 65)).toBe("bad");
  });
});
