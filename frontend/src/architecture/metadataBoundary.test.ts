import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const sourceRoot = dirname(fileURLToPath(import.meta.url));
const featureRoot = join(sourceRoot, "..", "features");
const sharedMetadataRoot = join(sourceRoot, "..", "shared", "metadata");

describe("metadata frontend boundaries", () => {
  it("keeps the dataflow feature independent from metadata-explorer", () => {
    const violations = importSpecifiers(join(featureRoot, "dataflows"))
      .filter(({ specifier }) => specifier.includes("metadata-explorer"))
      .map(({ file, specifier }) => `${file}: ${specifier}`);

    expect(violations).toEqual([]);
  });

  it("keeps the neutral metadata layer independent from feature modules", () => {
    const violations = importSpecifiers(sharedMetadataRoot)
      .filter(({ specifier }) => specifier.includes("features"))
      .map(({ file, specifier }) => `${file}: ${specifier}`);

    expect(violations).toEqual([]);
  });
});

function importSpecifiers(root: string): Array<{ file: string; specifier: string }> {
  return sourceFiles(root).flatMap((file) => {
    const source = readFileSync(file, "utf8");
    return [...source.matchAll(/(?:from|import)\s*["']([^"']+)["']/g)].map((match) => ({
      file,
      specifier: match[1]
    }));
  });
}

function sourceFiles(root: string): string[] {
  return readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
    const path = join(root, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return /\.(ts|tsx)$/.test(entry.name) ? [path] : [];
  });
}
