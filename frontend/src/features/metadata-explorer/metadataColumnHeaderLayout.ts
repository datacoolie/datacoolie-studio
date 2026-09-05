import type { CSSProperties } from "react";

export const METADATA_COLUMN_HEADER_LAYOUT = {
  headerPaddingInline: 5,
  filterButtonSize: 18,
  filterIconSize: 12,
  filterGap: 1,
  resizerHitWidth: 11,
  columnBorderWidth: 1,
  minimumColumnWidth: 72
} as const;

export const METADATA_FILTER_CONTROL_RESERVE =
  METADATA_COLUMN_HEADER_LAYOUT.filterButtonSize + METADATA_COLUMN_HEADER_LAYOUT.filterGap;
export const METADATA_RESIZER_HALF_WIDTH = Math.floor(METADATA_COLUMN_HEADER_LAYOUT.resizerHitWidth / 2);

type MetadataHeaderLayoutCssVariables = {
  "--metadata-header-padding-inline": string;
  "--metadata-header-inline-outset": string;
  "--metadata-header-negative-inline-offset": string;
  "--metadata-filter-button-size": string;
  "--metadata-filter-button-gap": string;
  "--metadata-resizer-hit-width": string;
  "--metadata-resizer-boundary-offset": string;
  "--metadata-resizer-line-offset": string;
};

export type MetadataHeaderLayoutStyle = CSSProperties & MetadataHeaderLayoutCssVariables;

export const metadataHeaderLayoutStyle: MetadataHeaderLayoutStyle = {
  "--metadata-header-padding-inline": `${METADATA_COLUMN_HEADER_LAYOUT.headerPaddingInline}px`,
  "--metadata-header-inline-outset": `${METADATA_COLUMN_HEADER_LAYOUT.headerPaddingInline * 2}px`,
  "--metadata-header-negative-inline-offset": `${-METADATA_COLUMN_HEADER_LAYOUT.headerPaddingInline}px`,
  "--metadata-filter-button-size": `${METADATA_COLUMN_HEADER_LAYOUT.filterButtonSize}px`,
  "--metadata-filter-button-gap": `${METADATA_COLUMN_HEADER_LAYOUT.filterGap}px`,
  "--metadata-resizer-hit-width": `${METADATA_COLUMN_HEADER_LAYOUT.resizerHitWidth}px`,
  "--metadata-resizer-boundary-offset": `${-METADATA_RESIZER_HALF_WIDTH}px`,
  "--metadata-resizer-line-offset": `${METADATA_RESIZER_HALF_WIDTH}px`
};

function normalizedWidth(width: number, fallback: number) {
  return Number.isFinite(width) ? Math.round(width) : fallback;
}

export function requiredMetadataHeaderWidth(textWidth: number) {
  return Math.ceil(textWidth
    + METADATA_COLUMN_HEADER_LAYOUT.headerPaddingInline * 2
    + METADATA_FILTER_CONTROL_RESERVE
    + METADATA_COLUMN_HEADER_LAYOUT.columnBorderWidth);
}

export function fitMetadataColumnHeader(previousWidth: number, textWidth: number) {
  return Math.max(normalizeMetadataColumnWidth(previousWidth), requiredMetadataHeaderWidth(textWidth));
}

export function normalizeMetadataColumnWidth(width: number) {
  return Math.max(
    METADATA_COLUMN_HEADER_LAYOUT.minimumColumnWidth,
    normalizedWidth(width, METADATA_COLUMN_HEADER_LAYOUT.minimumColumnWidth)
  );
}
