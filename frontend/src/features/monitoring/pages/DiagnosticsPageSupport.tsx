import { reportChartGrid } from "../components/monitoringPresentation";

export const REPORT_CHART_TIGHT_GRID_BOTTOM = 2;

export function reportTightChartGrid(overrides: Record<string, string | number | boolean> = {}) {
  return {
    ...reportChartGrid(overrides),
    bottom: REPORT_CHART_TIGHT_GRID_BOTTOM
  };
}
