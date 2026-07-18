import { describe, expect, it } from "vitest";
import { lineageDataflowFocusFromSearch, lineageDataflowFocusSearch } from "./lineageNavigation";

describe("lineage dataflow navigation", () => {
  it("round-trips a source-qualified dataflow identity", () => {
    const search = lineageDataflowFocusSearch({ metadataSourceId: 7, dataflowId: "orders_to_silver", name: "Orders" });
    expect(lineageDataflowFocusFromSearch(search)).toEqual({
      metadataSourceId: 7,
      dataflowId: "orders_to_silver",
      name: "Orders",
    });
  });

  it("does not create a focus target without a dataflow id or name", () => {
    expect(lineageDataflowFocusFromSearch("focusDataflowSource=7")).toBeNull();
  });
});
