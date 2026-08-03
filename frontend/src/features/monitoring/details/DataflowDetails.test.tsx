import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DataflowDetailSections } from "./DataflowDetails";
import { JSON_BLOCK_FIELDS } from "./detailPrimitives";

describe("dataflow transform details", () => {
  it("renders direct transform attributes and keeps configure values grouped", () => {
    const html = renderToStaticMarkup(<DataflowDetailSections row={{
      transform_select_columns: '["id", "email"]',
      transform_drop_columns: "[]",
      transform_rename_columns: '{"email": "contact_email"}',
      transform_value_rules: '[{"operation": "trim", "columns": ["email"]}]',
      transform_hash_columns: '[{"target_column": "email_hash", "columns": ["email"]}]',
      transform_masking_rules: '[{"method": "redact", "columns": ["email"], "value": "[PRIVATE]"}]',
      transform_missing_column_policy: "ignore",
      transform_configure: '{"missing_column_policy": "ignore"}',
    }} />);

    for (const label of [
      "Select columns",
      "Drop columns",
      "Rename columns",
      "Value rules",
      "Hash columns",
      "Masking rules",
      "Configure",
    ]) {
      expect(html).toContain(label);
    }
    expect(html).not.toContain("Missing column policy");
    expect(html).toContain("missing_column_policy");
    expect(html).toContain("contact_email");
    expect(html).toContain("[PRIVATE]");
    expect(html).toContain("ignore");
  });

  it("formats expanded transform collections as JSON blocks", () => {
    for (const field of [
      "transform_select_columns",
      "transform_drop_columns",
      "transform_rename_columns",
      "transform_value_rules",
      "transform_hash_columns",
      "transform_masking_rules",
    ]) {
      expect(JSON_BLOCK_FIELDS.has(field)).toBe(true);
    }
  });
});
