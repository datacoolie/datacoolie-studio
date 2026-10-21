import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { MaintenanceDetailSections } from "./MaintenanceDetails";

describe("Maintenance destination activity", () => {
  it("renders the current contributing-dataflows view without issue wording", () => {
    const html = renderToStaticMarkup(<MaintenanceDetailSections
      row={{}} relatedDataflows={[{ dataflow_id: "orders", status: "skipped", message: "No new rows" }]}
      total={1} loading={false} offset={0} limit={100}
      sort={{ sortBy: "message", sortDir: "asc" }} onSort={() => undefined}
      onPageChange={() => undefined} onPageSizeChange={() => undefined}
    />);
    expect(html).toContain("Contributing dataflows");
    expect(html).not.toContain(">Issue<");
  });
});
