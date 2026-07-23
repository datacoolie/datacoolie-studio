import { describe, expect, it } from "vitest";
import type { AssetInventoryItem, AssetReferenceGroupItem } from "../../shared/api/domainTypes";
import { attentionContextLine, assetSearchValues, metadataQueryForAsset, presentAsset, presentReference, referenceConsumerTypeSummary, referenceContextLine, referenceProvenanceDescription, referenceProvenanceLabel, referenceProvenanceTone, referenceResolutionPresentation, referenceSearchValues } from "./assetsPresentation";

describe("assets presentation", () => {
  const asset: AssetInventoryItem = {
    id: "asset:abc",
    display_name: "orders",
    friendly_name: "orders",
    full_identity: "lake · main.warehouse.sales.orders",
    asset_type: "table",
    format: "delta",
    connection_name: "lake",
    connection_type: "lakehouse",
    catalog: "main",
    database: "warehouse",
    schema_name: "sales",
    table: "orders",
    path: null,
    query: null,
    python_function: null,
    roles: ["source", "destination"],
    metadata_source_ids: [1],
    metadata_sources: [{ id: 1, uri: "metadata.json" }],
    upstream_count: 1,
    downstream_count: 2,
    input_dataflow_count: 1,
    output_dataflow_count: 2,
    depends_on_count: 0,
    used_by_count: 0,
    attention_count: 0,
    attention_items: [],
    identifiers: [],
    observations: [],
  };

  const reference: AssetReferenceGroupItem = {
    id: "reference:abc",
    reference_type: "table_reference",
    normalized_value: "silver.customer",
    display_name: "silver.customer",
    resolution: { state: "unresolved", reason: "multiple_matches" },
    resolved_asset_id: null,
    resolved_asset_ids: [],
    resolved_asset: null,
    occurrence_ids: ["reference-occurrence:abc"],
    consumer_asset_ids: ["asset:query"],
    consumer_assets: [{
      id: "asset:query",
      display_name: "SQL query",
      friendly_name: "SQL query",
      asset_type: "sql_query",
      connection_name: "lake",
      format: "sql",
      attention_count: 0,
    }],
    provenances: ["sql"],
    candidate_asset_ids: ["asset:customer"],
    candidate_assets: [{
      id: "asset:customer",
      display_name: "customer",
      friendly_name: "customer",
      asset_type: "table",
      connection_name: "lake",
      format: "delta",
      attention_count: 0,
    }],
    dependency_count: 1,
    dataflow_ids: [],
    attention_count: 1,
    attention_items: [{
      severity: "warning",
      code: "reference_multiple_matches",
      message: "multiple matches reference: silver.customer",
      source_type: "sql_reference",
      subject_type: "reference",
      reference_id: "reference:abc",
      reference_occurrence_id: "reference-occurrence:abc",
      details: {},
    }],
    observations: [],
    manual_mapping: null,
  };

  it("uses lineage icon presentation while keeping asset identity", () => {
    const presented = presentAsset(asset);
    expect(presented.friendlyName).toBe("orders");
    expect(presented.fullIdentity).toContain("lake");
    expect(presented.iconKind).toBe("delta");
  });

  it("collects searchable values", () => {
    const values = assetSearchValues(asset);
    expect(values).toContain("asset:abc");
    expect(values).toContain("main.warehouse.sales.orders");
    expect(values).toContain("metadata.json");
  });

  it("chooses best metadata query", () => {
    expect(metadataQueryForAsset(asset)).toBe("orders");
  });

  it("presents reference evidence separately from assets", () => {
    const presented = presentReference(reference);
    expect(presented.badge).toBe("REF");
    expect(presented.fullIdentity).toContain("silver.customer");
    expect(referenceSearchValues(reference)).toContain("multiple_matches");
  });

  it("shows useful canonical reference context without inferred scope", () => {
    expect(referenceContextLine(reference)).toBe("table_reference");
    expect(referenceContextLine({
      ...reference,
      reference_type: "path_reference",
      display_name: "/landing/customer/*.parquet",
      normalized_value: "/landing/customer/*.parquet",
    })).toBe("path_reference");
    expect(referenceContextLine({
      ...reference,
      reference_type: "api_endpoint_reference",
      display_name: "Customer API",
      normalized_value: "GET https://api.example.com/v1/customer",
    })).toBe("api_endpoint_reference · GET /v1/customer");
  });

  it("keeps provenance labels compact and source-specific", () => {
    expect(referenceProvenanceLabel("python")).toBe("py");
    expect(referenceProvenanceLabel("python_sql")).toBe("py_sql");
    expect(referenceProvenanceTone("sql")).toBe("sql");
    expect(referenceProvenanceTone("python")).toBe("python");
    expect(referenceProvenanceTone("python_sql")).toBe("mixed");
    expect(referenceProvenanceDescription("python_sql")).toBe("Detected from embedded SQL in Python analysis");
  });

  it("summarizes consumer asset types without choosing an arbitrary consumer", () => {
    expect(referenceConsumerTypeSummary({
      ...reference,
      consumer_asset_ids: ["asset:one", "asset:two", "asset:three"],
      consumer_assets: [
        { ...reference.consumer_assets[0], id: "asset:one", asset_type: "python_function" },
        { ...reference.consumer_assets[0], id: "asset:two", asset_type: "python_function" },
        { ...reference.consumer_assets[0], id: "asset:three", asset_type: "sql_query" },
      ],
    })).toBe("2 py_function, 1 sql_query");
  });

  it("summarizes attention origin, condition, and fix target", () => {
    expect(attentionContextLine(reference.attention_items[0])).toBe("sql_reference · multiple matches · fix: reference mapping");
    expect(attentionContextLine({
      ...reference.attention_items[0],
      code: "reference_unresolved",
      source_type: "python_sql_reference",
    })).toBe("py_sql_reference · unresolved · fix: reference mapping");
  });

  it("uses shared primary resolution labels and retains technical detail", () => {
    expect(referenceResolutionPresentation({ ...reference, resolution: { state: "automatic" } })).toEqual({
      state: "automatic",
      label: "Automatic",
      detail: "Resolved target",
    });
    expect(referenceResolutionPresentation({
      ...reference,
      resolution: { state: "manual" },
      manual_mapping: { mapping_id: 7 },
    })).toEqual({
      state: "manual",
      label: "Manual",
      detail: "Mapping #7",
    });
  });
});
