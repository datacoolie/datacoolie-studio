import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import { join, resolve } from "node:path";

const frontendRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));
const openapiInput = resolve(frontendRoot, "../openapi/openapi.json");
const canonicalOutput = resolve(
  frontendRoot,
  "src/shared/api/generated/schema.ts",
);
const tempDirectory = mkdtempSync(join(tmpdir(), "datacoolie-api-check-"));
const generatedOutput = join(tempDirectory, "schema.ts");
const generator = resolve(
  frontendRoot,
  "node_modules/openapi-typescript/bin/cli.js",
);

try {
  const result = spawnSync(
    process.execPath,
    [generator, openapiInput, "-o", generatedOutput],
    { cwd: frontendRoot, stdio: "inherit" },
  );
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error(`openapi-typescript exited with code ${result.status ?? 1}`);
  }

  const expected = readFileSync(canonicalOutput);
  const actual = readFileSync(generatedOutput);
  if (!expected.equals(actual)) {
    throw new Error(
      "Generated API types are out of date. Run `npm run api:generate` and review the result.",
    );
  }
  console.log("Generated API types are up to date.");
} catch (error) {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
} finally {
  rmSync(tempDirectory, { recursive: true, force: true });
}
