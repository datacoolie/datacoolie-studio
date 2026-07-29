import { describe, expect, it } from "vitest";
import { orderedEnvironmentNamesWithMissing } from "./environmentOrder";

describe("environment ordering", () => {
  it("preserves entered casing while recognizing preset names case-insensitively", () => {
    expect(orderedEnvironmentNamesWithMissing([{ name: "uAt" }, { name: "Dev" }])).toEqual([
      "Dev",
      "uAt",
      "test",
      "prod",
    ]);
  });
});
