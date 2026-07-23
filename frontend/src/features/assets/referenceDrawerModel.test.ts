import { describe, expect, it } from "vitest";
import type { AssetReferenceGroupItem, AssetReferenceOccurrenceItem } from "../../shared/api/domainTypes";
import { groupReferenceUsage, occurrenceLocationLabel, occurrenceScopeLabel, referenceResolutionStory, shouldShowNormalizedValue } from "./referenceDrawerModel";

function reference(overrides: Partial<AssetReferenceGroupItem> = {}) {
  return {
    id: "reference:customer",
    reference_type: "table_reference",
    normalized_value: "silver.customer",
    display_name: "silver.customer",
    resolution: { state: "unresolved", reason: "no_match" },
    resolved_asset_id: null,
    resolved_asset_ids: [],
    candidate_asset_ids: [],
    candidate_assets: [],
    occurrence_ids: ["occurrence:1", "occurrence:2"],
    consumer_asset_ids: ["asset:a", "asset:b"],
    consumer_assets: [],
    provenances: ["python"],
    dependency_count: 2,
    dataflow_ids: [],
    attention_count: 2,
    attention_items: [],
    observations: [],
    manual_mapping: null,
    ...overrides,
  } as AssetReferenceGroupItem;
}

function occurrence(id: string, consumerAssetId: string, line: number): AssetReferenceOccurrenceItem {
  return {
    id,
    reference_id: "reference:customer",
    reference_type: "table_reference",
    raw_value: "silver.customer",
    normalized_value: "silver.customer",
    display_name: "silver.customer",
    provenance: "python",
    consumer_asset_id: consumerAssetId,
    resolution: { state: "unresolved", reason: "no_match" },
    resolution_method: "no_declared_asset_match",
    candidate_asset_ids: [],
    candidate_assets: [],
    dependency_count: 1,
    dataflow_ids: [],
    attention_count: 1,
    attention_items: [],
    observations: [{ location: { path: "sources.py", line, column: 11 } }],
    manual_mapping: null,
  } as AssetReferenceOccurrenceItem;
}

describe("reference drawer model", () => {
  it("summarizes one canonical unresolved problem instead of occurrence-level duplicates", () => {
    expect(referenceResolutionStory(reference(), [])).toEqual({
      tone: "attention",
      title: "No declared canonical asset matches this reference.",
      detail: "No candidate was found.",
    });
  });

  it("describes a unique resolved target and manual mapping scope", () => {
    const story = referenceResolutionStory(reference({
      resolution: { state: "manual" },
      resolved_asset_id: "asset:customer",
      resolved_asset: { id: "asset:customer", display_name: "customer", friendly_name: "customer", asset_type: "table", attention_count: 0 },
      manual_mapping: { mapping_id: 7 },
    }), []);
    expect(story.title).toBe("All occurrences resolve to customer.");
    expect(story.detail).toContain("project-level manual mapping");
  });

  it.each([
    ["multiple_matches", "2 canonical candidates match this reference."],
    ["target_missing", "The mapped target is missing from this environment."],
    ["incomplete", "1 of 2 occurrences resolve successfully."],
    ["conflicting_targets", "Occurrences resolve to 2 different assets."],
  ] as const)("builds the %s resolution narrative", (reason, expectedTitle) => {
    const occurrences = reason === "incomplete"
      ? [
        { ...occurrence("occurrence:1", "asset:a", 8), resolution: { state: "automatic" as const } },
        occurrence("occurrence:2", "asset:b", 11),
      ]
      : [];
    const story = referenceResolutionStory(reference({
      resolution: { state: "unresolved", reason },
      candidate_asset_ids: reason === "multiple_matches" ? ["asset:a", "asset:b"] : [],
      resolved_asset_ids: reason === "conflicting_targets" ? ["asset:a", "asset:b"] : [],
      manual_mapping: reason === "target_missing"
        ? { mapping_id: 9, target_normalized_value: "catalog.database.silver.customer" }
        : null,
    }), occurrences);
    expect(story.title).toBe(expectedTitle);
  });

  it("formats source locations and groups evidence by consumer", () => {
    const occurrences = [occurrence("occurrence:1", "asset:a", 8), occurrence("occurrence:2", "asset:a", 11)];
    expect(occurrenceLocationLabel(occurrences[0])).toBe("sources.py:8:11");
    expect(groupReferenceUsage(reference(), occurrences)).toMatchObject([{ id: "asset:a", occurrences: [{ id: "occurrence:1" }, { id: "occurrence:2" }] }]);
  });

  it("keeps non-identity context scope with the occurrence", () => {
    const item = { ...occurrence("occurrence:1", "asset:a", 8), context_scope: "catalog:main:warehouse" };
    expect(occurrenceScopeLabel(item)).toBe("context: catalog:main:warehouse");
  });

  it("uses SQL query context when source coordinates are unavailable", () => {
    const sqlOccurrence = occurrence("occurrence:sql", "asset:sql", 8);
    sqlOccurrence.observations = [{ sql: "select * from silver.customer", location: null }];
    expect(occurrenceLocationLabel(sqlOccurrence)).toBe("SQL query");
  });

  it("suppresses a normalized value that duplicates the display name", () => {
    expect(shouldShowNormalizedValue(reference())).toBe(false);
    expect(shouldShowNormalizedValue(reference({ display_name: "customer" }))).toBe(true);
  });
});
