import { describe, expect, it } from "vitest";
import {
  formatMaintenanceLag,
  maintenanceFormatIconKind,
  maintenanceTableHealthClass,
  maintenanceTableHealthLabel,
  maintenanceTableHealthTone,
} from "./maintenancePresentation";

describe("maintenance presentation", () => {
  it.each([
    ["healthy", "Healthy", "healthy", "health-healthy"],
    ["warning", "Warning", "warning", "health-warning"],
    ["missing", "Missing", "warning", "health-warning"],
    ["has_issues", "Has issues", "issues", "health-issues"],
    ["no_evidence", "No evidence", "neutral", "health-neutral"],
    ["unknown", "unknown", "neutral", "health-neutral"],
  ] as const)("maps %s to a stable label and tone", (value, label, tone, className) => {
    expect(maintenanceTableHealthLabel(value)).toBe(label);
    expect(maintenanceTableHealthTone(value)).toBe(tone);
    expect(maintenanceTableHealthClass(value)).toBe(className);
  });

  it.each([
    ["delta", "delta"],
    ["apache_iceberg", "iceberg"],
    ["SQL query", "sql"],
    ["python function", "python"],
    ["lakehouse", "database"],
    ["custom", "table"],
  ] as const)("maps %s to the shared lineage icon %s", (format, icon) => {
    expect(maintenanceFormatIconKind(format)).toBe(icon);
  });

  it("formats maintenance lag consistently across page and drawer", () => {
    expect(formatMaintenanceLag(0)).toBe("-");
    expect(formatMaintenanceLag(7_200)).toBe("2h");
    expect(formatMaintenanceLag(102_527)).toBe("1.2d");
  });
});
