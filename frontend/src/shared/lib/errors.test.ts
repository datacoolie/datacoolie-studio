import { describe, expect, it } from "vitest";
import { apiErrorMessage, apiRequestError } from "./errors";

describe("apiErrorMessage", () => {
  it("extracts a FastAPI detail string", () => {
    expect(apiErrorMessage('{"detail":"Project already exists: demo"}', 409)).toBe("Project already exists: demo");
  });

  it("preserves non-JSON bodies and supplies a status fallback", () => {
    expect(apiErrorMessage("Service unavailable", 503)).toBe("Service unavailable");
    expect(apiErrorMessage("", 500)).toBe("Request failed: 500");
  });

  it("preserves typed FastAPI error details for feature recovery", () => {
    const error = apiRequestError(
      '{"detail":{"code":"analytics_rebuild_required","message":"Sync Log sources","action":"sync_log_sources"}}',
      409,
    );
    expect(error.message).toBe("Sync Log sources");
    expect(error.status).toBe(409);
    expect(error.code).toBe("analytics_rebuild_required");
  });
});
