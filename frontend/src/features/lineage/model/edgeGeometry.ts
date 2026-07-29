export type LabelSegmentPreference = "source" | "target" | "longest";
export type LabelAlignment = "start" | "center" | "end";

export interface EdgeLabelPlacement {
  segment: "source" | "target";
  alignment: LabelAlignment;
}

export const EDGE_LABEL_MAX_WIDTH = 320;
export const EDGE_LABEL_HORIZONTAL_PADDING = 14;
export const EDGE_LABEL_STRAIGHT_TOLERANCE = 8;

export function resolveLabelSegment(
  preference: LabelSegmentPreference,
  sourceSegmentLength: number,
  targetSegmentLength: number
): "source" | "target" {
  if (preference !== "longest") return preference;
  return sourceSegmentLength >= targetSegmentLength ? "source" : "target";
}

export function resolveLabelPlacement(
  preference: LabelSegmentPreference,
  sourceSegmentLength: number,
  targetSegmentLength: number,
  verticalDisplacement = 0
): EdgeLabelPlacement {
  const effectivePreference = preference === "longest"
    && Math.abs(verticalDisplacement) > EDGE_LABEL_STRAIGHT_TOLERANCE
    ? "target"
    : preference;
  const segment = resolveLabelSegment(effectivePreference, sourceSegmentLength, targetSegmentLength);
  if (effectivePreference === "source") return { segment, alignment: "start" };
  if (effectivePreference === "target") return { segment, alignment: "end" };
  return { segment, alignment: "center" };
}

export function labelTransform(
  alignment: LabelAlignment,
  x: number,
  y: number
) {
  const horizontal = alignment === "start" ? "0%" : alignment === "end" ? "-100%" : "-50%";
  return `translate(${horizontal}, -100%) translate(${x}px, ${y}px)`;
}

export function labelAnchorX(
  alignment: LabelAlignment,
  sourceX: number,
  targetX: number,
  inset = 12
) {
  if (alignment === "start") return sourceX + inset;
  if (alignment === "end") return targetX - inset;
  return sourceX + (targetX - sourceX) / 2;
}

export function estimateLabelWidth(label: string) {
  const measured = measureWithCanvas(label);
  const contentWidth = measured ?? [...label].reduce((width, character) => (
    width + (/[MW@#%&]/.test(character) ? 8.2 : /[ilI1.,'|]/.test(character) ? 3.4 : 6.1)
  ), 0);
  return Math.min(
    EDGE_LABEL_MAX_WIDTH,
    Math.ceil(contentWidth + EDGE_LABEL_HORIZONTAL_PADDING)
  );
}

export function recommendedLayerSpacing(labels: string[]) {
  if (!labels.length) return 150;
  const widths = labels.map(estimateLabelWidth).sort((left, right) => left - right);
  const percentileIndex = Math.min(widths.length - 1, Math.floor((widths.length - 1) * 0.9));
  return Math.max(150, Math.min(380, widths[percentileIndex] + 48));
}

function measureWithCanvas(label: string) {
  if (typeof document === "undefined") return null;
  const context = document.createElement("canvas").getContext("2d");
  if (!context) return null;
  context.font = "750 10px Inter, Arial, sans-serif";
  return context.measureText(label).width;
}
