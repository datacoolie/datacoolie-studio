import { describe, expect, it } from "vitest";
import type { StudioSettings } from "../../shared/api/domainTypes";
import {
  buildStudioSettingsPatch,
  createStudioSettingsDraft,
  isStudioSettingsDraftValid,
} from "./settingsModel";

const configured: StudioSettings = {
  timezone: "Asia/Ho_Chi_Minh",
  timezone_source: "configured",
  timezone_offset_minutes: 420,
  source_check_mode: "adaptive",
  source_check_interval_seconds: 30,
  source_check_max_interval_seconds: 300,
};

describe("Studio Settings draft", () => {
  it("does not submit unchanged values", () => {
    expect(buildStudioSettingsPatch(configured, createStudioSettingsDraft(configured))).toEqual({});
  });

  it("builds only the changed fields", () => {
    expect(buildStudioSettingsPatch(configured, {
      ...createStudioSettingsDraft(configured),
      sourceCheckIntervalInput: "45",
    })).toEqual({ source_check_interval_seconds: 45 });
  });

  it("preserves timezone source transitions even when the label matches", () => {
    const serverDefault: StudioSettings = { ...configured, timezone_source: "server_default" };
    expect(buildStudioSettingsPatch(serverDefault, {
      ...createStudioSettingsDraft(serverDefault),
      useServerDefaultTimezone: false,
    })).toEqual({ timezone: "Asia/Ho_Chi_Minh" });
    expect(buildStudioSettingsPatch(configured, {
      ...createStudioSettingsDraft(configured),
      useServerDefaultTimezone: true,
    })).toEqual({ timezone: null });
  });

  it("rejects incomplete and out-of-range drafts", () => {
    expect(isStudioSettingsDraftValid({
      timezoneInput: "",
      sourceCheckMode: "adaptive",
      sourceCheckIntervalInput: "30",
      sourceCheckMaxIntervalInput: "300",
      useServerDefaultTimezone: false,
    })).toBe(false);
    expect(isStudioSettingsDraftValid({
      timezoneInput: "UTC",
      sourceCheckMode: "adaptive",
      sourceCheckIntervalInput: "4",
      sourceCheckMaxIntervalInput: "300",
      useServerDefaultTimezone: false,
    })).toBe(false);
  });
});
