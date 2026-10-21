import { describe, expect, it } from "vitest";

import {
  addSourcePath,
  formatSourceImportSummary,
  manualSourceCopy,
  removeSourcePath,
  replaceSourcePath,
  sourceComponentProvenanceLabel,
  sourcePathValues,
} from "./sourceSetupModel";

describe("source setup model", () => {
  it("describes the different manual path semantics", () => {
    expect(manualSourceCopy("metadata")).toMatchObject({
      pathLabel: "Metadata path",
      description: "Metadata file or folder.",
      pathHint: "A folder imports its metadata files.",
      submitLabel: "Import metadata",
    });
    expect(manualSourceCopy("sql")).toMatchObject({
      pathLabel: "SQL file or root",
      description: "One SQL file or folder.",
      pathHint: "One path creates one SQL source.",
      submitLabel: "Add SQL source",
    });
    expect(manualSourceCopy("code")).toMatchObject({
      pathLabel: "Code artifact",
      description: "One source-code artifact path.",
      pathHint: "One path creates one code artifact.",
      submitLabel: "Add code artifact",
    });
  });

  it("normalizes repeatable fallback rows without splitting path text", () => {
    expect(sourcePathValues([" sql/core ", "sql1,sql2", " "])).toEqual([
      "sql/core",
      "sql1,sql2",
    ]);
    expect(sourcePathValues(["", "  "])).toBeUndefined();
    expect(replaceSourcePath(["sql", "finance"], 1, "sql/finance")).toEqual([
      "sql",
      "sql/finance",
    ]);
    expect(addSourcePath(["sql"])).toEqual(["sql", ""]);
    expect(removeSourcePath(["sql", "finance"], 0)).toEqual(["finance"]);
    expect(removeSourcePath(["sql"], 0)).toEqual([""]);
  });

  it("includes SQL source counts while preserving created and reused counts", () => {
    expect(formatSourceImportSummary({
      summary: {
        created: 3,
        existing: 2,
        metadata_sources: 4,
        sql_sources: 2,
        code_artifacts: 1,
      },
    })).toBe("3 created · 2 reused · 4 metadata · 2 SQL sources · 1 code");
  });

  it("falls back to response list lengths when summary counts are absent", () => {
    expect(formatSourceImportSummary({
      summary: {},
      created: 1,
      existing: 2,
    })).toBe("1 created · 2 reused · 0 metadata · 0 SQL sources · 0 code");
  });

  it.each([
    ["datacoolie.yml", "From datacoolie.yml"],
    ["manifest.json", "From manifest.json"],
    ["default_input", "From submitted fallback"],
    ["default", "From built-in default"],
  ])("labels %s provenance", (value, expected) => {
    expect(sourceComponentProvenanceLabel("sql", {
      discovery_managed: true,
      component_resolved_from: { sql: value },
    })).toBe(expected);
  });

  it("does not invent provenance for manual or incomplete source rows", () => {
    expect(sourceComponentProvenanceLabel("metadata", {
      component_resolved_from: { metadata: "datacoolie.yml" },
    })).toBeNull();
    expect(sourceComponentProvenanceLabel("metadata", {
      discovery_managed: true,
      component_resolved_from: { metadata: "unknown" },
    })).toBeNull();
    expect(sourceComponentProvenanceLabel("metadata", null)).toBeNull();
  });
});
