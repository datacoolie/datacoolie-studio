import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DrawerWatermarkBadge, FreshnessDetailSections } from "./FreshnessDetails";
import { DataflowConfigurationBody } from "./DataflowStoryConfiguration";

describe("freshness transform details", () => {
  it("presents Empty movement as a neutral watermark result", () => {
    const html = renderToStaticMarkup(<DrawerWatermarkBadge row={{ movement_state: "empty" }} />);
    expect(html).toContain("badge-neutral");
    expect(html).toContain(">Empty</span>");
  });

  it("renders direct transform attributes and keeps configure values grouped", () => {
    const row = {
        dataflow_id: "customers",
        transform_select_columns: '["id", "email"]',
        transform_drop_columns: "[]",
        transform_rename_columns: '{"email": "contact_email"}',
        transform_value_rules: '[{"operation": "trim", "columns": ["email"]}]',
        transform_hash_columns: '[{"target_column": "email_hash", "columns": ["email"]}]',
        transform_masking_rules: '[{"method": "redact", "columns": ["email"]}]',
        transform_missing_column_policy: "ignore",
        transform_configure: '{"missing_column_policy": "ignore"}',
      };
    const html = renderToStaticMarkup(<DataflowConfigurationBody row={row} />);
    const drawerHtml = renderToStaticMarkup(<FreshnessDetailSections
      row={row}
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
    expect(drawerHtml).toContain("Configuration");
  });

  it("shows a nullable latest canonical message without falling back to an older error", () => {
    const html = renderToStaticMarkup(<FreshnessDetailSections
      row={{
        dataflow_id: "customers",
        latest_run_at: "2026-09-11T00:00:31Z",
        latest_run_status: "succeeded",
        latest_message: null,
        latest_error_message: "old failure text",
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

    expect(html).toContain("<h3>Message</h3>");
    expect(html).toContain(">unk</div>");
    expect(html).not.toContain("old failure text");
  });

  it("shows the explanation of a skipped latest run", () => {
    const html = renderToStaticMarkup(<FreshnessDetailSections
      row={{ dataflow_id: "customers", latest_run_status: "skipped", latest_message: "No new rows matched" }}
      relatedDataflows={[]} total={0} offset={0} limit={25}
      sort={{ sortBy: "start_time", sortDir: "desc" }} onSort={() => undefined}
      onPageChange={() => undefined} onPageSizeChange={() => undefined}
    />);
    expect(html).toContain("No new rows matched");
    expect(html).toContain("skipped");
  });

  it("separates freshness checks from run status and keeps the full dataflow id visible", () => {
    const id = "28151c10-14c9-4e5b-8d4f-823622847559";
    const html = renderToStaticMarkup(<FreshnessDetailSections
      row={{
        dataflow_id: id,
        latest_freshness_status: "fresh",
        latest_run_status: "succeeded",
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

    expect(html).toContain(id);
    expect(html).toContain("Freshness status");
    expect(html).toContain("Latest run status");
    expect(html).not.toContain(">Total</span>");
  });
});
