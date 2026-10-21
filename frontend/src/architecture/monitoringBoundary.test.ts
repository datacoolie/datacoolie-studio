import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const sourceRoot = dirname(fileURLToPath(import.meta.url));
const frontendRoot = join(sourceRoot, "..");

describe("monitoring frontend boundaries", () => {
  it("keeps shared table, number, and icon primitives independent from feature modules", () => {
    const sharedFiles = [
      join(frontendRoot, "shared", "components", "DataTable.tsx"),
      join(frontendRoot, "shared", "components", "CompactNumberValue.tsx"),
      join(frontendRoot, "shared", "components", "FormatIcon.tsx"),
      join(frontendRoot, "shared", "lib", "numberFormat.ts"),
      join(frontendRoot, "shared", "formatPresentation.ts")
    ];
    const violations = sharedFiles.flatMap((file) =>
      importSpecifiers(file)
        .filter((specifier) => specifier.includes("features"))
        .map((specifier) => `${file}: ${specifier}`)
    );

    expect(violations).toEqual([]);
  });

  it("keeps monitoring shared presentation independent from page modules", () => {
    const files = [
      join(frontendRoot, "features", "monitoring", "components", "monitoringPresentation.tsx"),
      join(frontendRoot, "features", "monitoring", "components", "monitoringTime.ts"),
      join(frontendRoot, "features", "monitoring", "components", "monitoringChartOptions.ts"),
      join(frontendRoot, "features", "monitoring", "components", "monitoringFormatting.ts"),
      join(frontendRoot, "features", "monitoring", "components", "monitoringDurationPresentation.tsx"),
    ];
    const violations = files.flatMap((file) =>
      importSpecifiers(file)
        .filter((specifier) => specifier.includes("/pages/") || specifier.includes("lineage"))
        .map((specifier) => `${file}: ${specifier}`)
    );

    expect(violations).toEqual([]);
  });

  it("keeps page presentation modules independent from support modules and the primitive barrel", () => {
    const files = [
      join(frontendRoot, "features", "monitoring", "pages", "JobsPagePresentation.tsx"),
      join(frontendRoot, "features", "monitoring", "pages", "DataflowsPagePresentation.tsx"),
      join(frontendRoot, "features", "monitoring", "pages", "FailurePagePresentation.ts")
    ];
    const violations = files.flatMap((file) =>
      importSpecifiers(file)
        .filter((specifier) => specifier.includes("PageSupport") || specifier.endsWith("monitoringPresentation"))
        .map((specifier) => `${file}: ${specifier}`)
    );

    expect(violations).toEqual([]);
  });
});

function importSpecifiers(file: string): string[] {
  return [...readFileSync(file, "utf8").matchAll(/(?:from|import)\s*["']([^"']+)["']/g)].map(
    (match) => match[1]
  );
}
