import { describe, expect, it } from "vitest";
import {
  DEFAULT_SOURCE_CHECK_INTERVAL_SECONDS,
  sourceCheckIntervalMs,
} from "./environmentHeaderResource";

describe("sourceCheckIntervalMs", () => {
  it("uses the configured bounded value", () => {
    expect(sourceCheckIntervalMs(45)).toBe(45_000);
    expect(sourceCheckIntervalMs(5)).toBe(5_000);
    expect(sourceCheckIntervalMs(3600)).toBe(3_600_000);
  });

  it("falls back to the 30 second default", () => {
    expect(sourceCheckIntervalMs(undefined)).toBe(DEFAULT_SOURCE_CHECK_INTERVAL_SECONDS * 1_000);
    expect(sourceCheckIntervalMs(4)).toBe(DEFAULT_SOURCE_CHECK_INTERVAL_SECONDS * 1_000);
    expect(sourceCheckIntervalMs(12.5)).toBe(DEFAULT_SOURCE_CHECK_INTERVAL_SECONDS * 1_000);
  });
});
