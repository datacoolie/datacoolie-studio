import { describe, expect, it } from "vitest";
import type { MetadataWriteOperation, WriteSafety } from "../api/domainTypes";
import { metadataWriteConfirmation } from "./metadataSaveConfirmation";

function operation(
  states: Array<{
    kind: "metadata" | "sql";
    state: string;
    action: "create" | "update";
    provider?: string;
    write_safety?: WriteSafety;
  }>,
): MetadataWriteOperation {
  return {
    operation_key: "mwo_test",
    environment_id: 2,
    state: "prepared",
    created_at: "2026-09-18T00:00:00Z",
    updated_at: "2026-09-18T00:00:00Z",
    completed_at: null,
    expires_at: "2026-09-19T00:00:00Z",
    error: null,
    summary: { total: states.length, metadata: 0, sql: 0, pending: states.length, applied: 0, conflicted: 0 },
    items: states.map((item, index) => ({
      id: index + 1,
      order: index,
      kind: item.kind,
      state: item.state,
      source_id: 1,
      source_name: item.kind === "sql" ? "orders.sql" : "metadata.json",
      source_uri: item.kind === "sql" ? "sql/orders.sql" : "metadata.json",
      target_uri: item.kind === "sql" ? "sql/orders.sql" : "metadata.json",
      action: item.action,
      label: item.kind === "sql" ? "sql/orders.sql" : "metadata.json",
      sheets: item.kind === "sql" ? ["SQL file"] : ["Metadata"],
      provider: item.provider ?? "local",
      write_safety: item.write_safety ?? "cooperative",
      error: null,
    })),
  } as MetadataWriteOperation;
}

describe("server-authored metadata write confirmation", () => {
  it("groups the exact prepared metadata and SQL items", () => {
    const confirmation = metadataWriteConfirmation(operation([
      { kind: "metadata", state: "prepared", action: "update" },
      { kind: "sql", state: "prepared", action: "create" },
    ]));

    expect(confirmation.impacts).toHaveLength(1);
    expect(confirmation.sqlImpacts).toHaveLength(1);
    expect(confirmation.impacts[0].label).toBe("metadata.json");
    expect(confirmation.sqlImpacts[0].action).toBe("create");
    expect(confirmation.impacts[0].writeSafety).toBe("cooperative");
    expect(confirmation.title).toBe("Save 2 changes?");
  });

  it("shows one server-derived warning when any item uses verified writes", () => {
    const confirmation = metadataWriteConfirmation(operation([
      {
        kind: "metadata",
        state: "prepared",
        action: "update",
        provider: "dbfs",
        write_safety: "verified",
      },
      {
        kind: "sql",
        state: "prepared",
        action: "update",
        provider: "s3",
        write_safety: "strong_cas",
      },
    ]));

    expect(confirmation.safetyNotice).toContain("cannot prevent an external writer");
    expect(confirmation.impacts[0].provider).toBe("dbfs");
    expect(confirmation.sqlImpacts[0].writeSafety).toBe("strong_cas");
  });

  it("does not show the verified warning for strong CAS only", () => {
    const confirmation = metadataWriteConfirmation(operation([
      {
        kind: "metadata",
        state: "prepared",
        action: "update",
        provider: "s3",
        write_safety: "strong_cas",
      },
    ]));

    expect(confirmation.safetyNotice).toBeUndefined();
  });

  it("surfaces applied and conflict states after a partial retry", () => {
    const confirmation = metadataWriteConfirmation(operation([
      { kind: "metadata", state: "applied", action: "update" },
      { kind: "sql", state: "conflict", action: "update" },
    ]));

    expect(confirmation.impacts[0].status).toBe("applied");
    expect(confirmation.sqlImpacts[0].status).toBe("conflict");
    expect(confirmation.detail).toContain("changed outside Studio");
    expect(confirmation.safetyNotice).toBeUndefined();
  });
});
