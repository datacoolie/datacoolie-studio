import type { FitViewOptions } from "@xyflow/react";

export const LINEAGE_AUTO_FIT_ENTITY_LIMIT = 60;
export const LINEAGE_FIT_PADDING = 0.18;
export const LINEAGE_FIT_MIN_ZOOM = 0.34;
export const LINEAGE_FIT_MAX_ZOOM = 1;
export const LINEAGE_LARGE_GRAPH_MIN_ZOOM = 0.42;
export const LINEAGE_LARGE_GRAPH_MAX_ZOOM = 0.5;
export const LINEAGE_FOCUS_FIT_MIN_ZOOM = 0.42;
export const LINEAGE_FIT_DURATION = 220;

export function shouldAutoFitLineage(entityCount: number) {
  return entityCount <= LINEAGE_AUTO_FIT_ENTITY_LIMIT;
}

export function initialLargeGraphFitOptions(): FitViewOptions {
  return {
    padding: LINEAGE_FIT_PADDING,
    minZoom: LINEAGE_LARGE_GRAPH_MIN_ZOOM,
    maxZoom: LINEAGE_LARGE_GRAPH_MAX_ZOOM,
  };
}

export function focusLineageFitOptions(): FitViewOptions {
  return {
    padding: LINEAGE_FIT_PADDING,
    minZoom: LINEAGE_FOCUS_FIT_MIN_ZOOM,
    maxZoom: LINEAGE_FIT_MAX_ZOOM,
  };
}

export function visibleLineageFitOptions(): FitViewOptions {
  return {
    padding: LINEAGE_FIT_PADDING,
    minZoom: LINEAGE_FIT_MIN_ZOOM,
    maxZoom: LINEAGE_FIT_MAX_ZOOM,
  };
}
