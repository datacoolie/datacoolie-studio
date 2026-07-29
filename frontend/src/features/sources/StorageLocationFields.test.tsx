import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { oneLakePathIssue, StorageLocationFields } from "./StorageLocationFields";


describe("StorageLocationFields OneLake presentation", () => {
  it("shows Files-only guidance and Entra authentication choices", () => {
    const markup = renderToStaticMarkup(
      <StorageLocationFields
        binding={{
          provider: "onelake",
          auth_mode: "ambient",
          credential_profile_id: null,
          options: {},
        }}
        uri={"abfss://workspace@onelake.dfs.fabric.microsoft.com/lake.Lakehouse/Files/project"}
        disabled={false}
        onChange={() => undefined}
      />,
    );

    expect(markup).toContain("Microsoft OneLake");
    expect(markup).toContain("Lakehouse Files only");
    expect(markup).toContain("Azure sign-in (default)");
    expect(markup).toContain("Credential Profile");
    expect(markup).not.toContain(">Anonymous<");
  });

  it("rejects an obvious Tables path before a connection request", () => {
    expect(oneLakePathIssue(
      "abfss://workspace@onelake.dfs.fabric.microsoft.com/lake.Lakehouse/Tables/orders",
    )).toContain("Tables");
    expect(oneLakePathIssue(
      "https://onelake.dfs.fabric.microsoft.com/workspace/lake.Lakehouse/%54ables/orders",
    )).toContain("Tables");
    expect(oneLakePathIssue(
      "abfss://workspace@onelake.dfs.fabric.microsoft.com/lake.Lakehouse/Files/orders",
    )).toBeNull();
  });
});
