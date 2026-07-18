import { describe, expect, it } from "vitest";
import { alignedVolumeAxisBounds, sortVolumeRows } from "./volumePageModel";

describe("volume page model", () => {
  it("preserves backend priority order until the user chooses a table sort", () => {
    const rows = [{ volume_candidate_priority: 4 }, { volume_candidate_priority: 2 }, { volume_candidate_priority: 3 }];
    expect(sortVolumeRows(rows)).toBe(rows);
  });

  it("sorts the full candidate collection before callers paginate it", () => {
    const rows = [{ volume_rows_read: 2 }, { volume_rows_read: 30 }, { volume_rows_read: 10 }];
    const sorted = sortVolumeRows(rows, { sortBy: "volume_rows_read", sortDir: "desc" });
    expect(sorted.map((row) => row.volume_rows_read)).toEqual([30, 10, 2]);
  });

  it("keeps equal values stable and places missing values last", () => {
    const rows = [{ volume_rows_read: 10, id: "first" }, { id: "missing" }, { volume_rows_read: 10, id: "second" }];
    const sorted = sortVolumeRows(rows, { sortBy: "volume_rows_read", sortDir: "desc" });
    expect(sorted.map((row) => row.id)).toEqual(["first", "second", "missing"]);
  });

  it("aligns the zero position for byte and file axes", () => {
    const bounds = alignedVolumeAxisBounds([-80, 20], [4, 10]);
    const primaryZero = -bounds.primaryMin / (bounds.primaryMax - bounds.primaryMin);
    const secondaryZero = -bounds.secondaryMin / (bounds.secondaryMax - bounds.secondaryMin);

    expect(primaryZero).toBeCloseTo(secondaryZero, 8);
    expect(bounds.secondaryMin).toBeLessThan(0);
  });

  it("keeps both axes anchored at the bottom when byte deltas are non-negative", () => {
    const bounds = alignedVolumeAxisBounds([0, 20], [4, 10]);
    expect(bounds.primaryMin).toBe(0);
    expect(bounds.secondaryMin).toBe(0);
  });
});
