import { describe, expect, it } from "vitest";
import { dataflowMetadataStageSearch, dataflowMetadataTextSearch } from "./OverviewPage";

describe("Overview metadata navigation", () => {
  it("encodes stage navigation as an exact structured filter", () => {
    const search = dataflowMetadataStageSearch("Silver 2/curated");
    const params = new URLSearchParams(search);

    expect(params.get("sheet")).toBe("dataflows");
    expect(params.get("stage")).toBe("Silver 2/curated");
    expect(params.has("q")).toBe(false);
  });

  it("keeps load type navigation as free-text search", () => {
    const params = new URLSearchParams(dataflowMetadataTextSearch("merge_upsert"));

    expect(params.get("sheet")).toBe("dataflows");
    expect(params.get("q")).toBe("merge_upsert");
    expect(params.has("stage")).toBe(false);
  });
});
