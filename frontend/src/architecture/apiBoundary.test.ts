import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const sourceRoot = dirname(fileURLToPath(import.meta.url));
const frontendRoot = join(sourceRoot, "..");
const contractTypesFile = join(frontendRoot, "shared", "api", "contractTypes.ts");
const domainTypesFile = join(frontendRoot, "shared", "api", "domainTypes.ts");

describe("frontend API boundaries", () => {
  it("keeps generated OpenAPI schema ownership in contractTypes", () => {
    const violations = sourceFiles(frontendRoot)
      .filter((file) => file !== contractTypesFile)
      .flatMap((file) =>
        importSpecifiers(file)
          .filter((specifier) => /(?:^|\/)generated\/schema$/.test(specifier))
          .map((specifier) => `${file}: ${specifier}`),
      );

    expect(violations).toEqual([]);
  });

  it("keeps stable transport aliases out of the hand-authored domain model", () => {
    const source = readFileSync(domainTypesFile, "utf8");
    const duplicatedTransportInterfaces = [
      "Project",
      "ProjectEnvironmentSummary",
      "ProjectSummary",
      "ProjectReferenceMapping",
      "Environment",
      "MonitoringRecord",
      "JobRecord",
      "NamedCount",
    ].filter((name) => new RegExp(`export interface ${name}\\b`).test(source));

    expect(duplicatedTransportInterfaces).toEqual([]);
    expect(source).not.toContain("generated/schema");
  });

  it("does not retain retired Monitoring facade files or imports", () => {
    const retiredFiles = [
      join(frontendRoot, "features", "monitoring", "components", "monitoringCore.tsx"),
      join(frontendRoot, "features", "monitoring", "components", "monitoringPrimitives.tsx"),
    ];
    expect(retiredFiles.filter(existsSync)).toEqual([]);

    const violations = sourceFiles(frontendRoot).flatMap((file) =>
      importSpecifiers(file)
        .filter((specifier) => /(?:^|\/)monitoring(?:Core|Primitives)$/.test(specifier))
        .map((specifier) => `${file}: ${specifier}`),
    );
    expect(violations).toEqual([]);
  });
  it("keeps shared modules neutral and Assets/Sources dependency directions explicit", () => {
    const violations = sourceFiles(frontendRoot).flatMap(file => importSpecifiers(file).filter(specifier =>
      (file.includes(join("shared", "")) && /(?:^|\/)features\//.test(specifier))
      || (file.includes(join("features", "assets")) && /(?:^|\/)lineage\//.test(specifier))
      || (file.includes(join("features", "sources")) && specifier.includes("app/environmentSourcesResource")),
    ).map(specifier => `${file}: ${specifier}`));
    expect(violations).toEqual([]);
    expect(readFileSync(join(frontendRoot, "shared", "api", "client.ts"), "utf8")).not.toMatch(/applyMetadataEditorWrite:\s/);
  });
});

function importSpecifiers(file: string): string[] {
  return [...readFileSync(file, "utf8").matchAll(/(?:from|import)\s*["']([^"']+)["']/g)].map(
    (match) => match[1],
  );
}

function sourceFiles(root: string): string[] {
  return readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
    const path = join(root, entry.name);
    if (entry.isDirectory()) {
      return entry.name === "generated" ? [] : sourceFiles(path);
    }
    return /\.(ts|tsx)$/.test(entry.name) ? [path] : [];
  });
}
