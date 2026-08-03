import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { FreshnessDetailSections } from "./FreshnessDetails";

describe("freshness transform details", () => {
  it("renders direct transform attributes and keeps configure values grouped", () => {
    const html = renderToStaticMarkup(<FreshnessDetailSections
      row={{
        dataflow_id: "customers",
        transform_select_columns: '["id", "email"]',
        transform_drop_columns: "[]",
        transform_rename_columns: '{"email": "contact_email"}',
        transform_value_rules: '[{"operation": "trim", "columns": ["email"]}]',
        transform_hash_columns: '[{"target_column": "email_hash", "columns": ["email"]}]',
        transform_masking_rules: '[{"method": "redact", "columns": ["email"]}]',
        transform_missing_column_policy: "ignore",
        transform_configure: '{"missing_column_policy": "ignore"}',
      }}
      relatedDataflows={[]}
      total={0}
      offset={0}
      limit={25}
      sort={{ sortBy: "start_time", sortDir: "desc" }}
      onSort={() => undefined}
      onPageChange={() => undefined}
      onPageSizeChange={() => undefined}
    />);

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
  });
});
