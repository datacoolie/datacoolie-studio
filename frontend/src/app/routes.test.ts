import { describe, expect, it } from "vitest";
import { modulePath, parseRoute } from "./routes";

describe("studio routes", () => {
  it("parses assets pages from the URL", () => {
    expect(parseRoute("/projects/1/envs/2/assets")).toMatchObject({
      projectId: 1,
      environmentId: 2,
      module: "assets",
    });
  });

  it("parses monitoring subpages from the URL", () => {
    expect(parseRoute("/projects/1/envs/2/monitoring/jobs")).toMatchObject({
      projectId: 1,
      environmentId: 2,
      module: "monitoring",
      monitoringPage: "jobs"
    });
  });

  it("builds canonical monitoring subpage paths", () => {
    expect(modulePath(1, 2, "monitoring", "failures")).toBe("/projects/1/envs/2/monitoring/failures");
  });

  it("builds canonical assets paths", () => {
    expect(modulePath(1, 2, "assets")).toBe("/projects/1/envs/2/assets");
  });
});
