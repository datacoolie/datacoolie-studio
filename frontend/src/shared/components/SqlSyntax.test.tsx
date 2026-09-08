import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { formatPrettySql, highlightSql } from "./SqlSyntax";

describe("SQL syntax presentation", () => {
  it("keeps the Metadata formatter line-break rules", () => {
    expect(formatPrettySql("SELECT id, name FROM customers WHERE id = 42")).toBe(
      "SELECT id,\n  name \nFROM customers \nWHERE id = 42"
    );
  });

  it("emits the token classes used by the shared SQL palette", () => {
    const markup = renderToStaticMarkup(highlightSql("SELECT id FROM customers WHERE name = 'Ada' AND id = 42 -- filter"));

    expect(markup).toContain("sql-token sql-keyword");
    expect(markup).toContain("sql-token sql-string");
    expect(markup).toContain("sql-token sql-number");
    expect(markup).toContain("sql-token sql-comment");
  });
});
