import type { SheetRow } from "./metadataSheetTypes";

export type MetadataArrowKey = "ArrowDown" | "ArrowLeft" | "ArrowRight" | "ArrowUp";

interface CellPosition {
  col: number;
  row: number;
}

const arrowOffsets: Record<MetadataArrowKey, CellPosition> = {
  ArrowDown: { col: 0, row: 1 },
  ArrowLeft: { col: -1, row: 0 },
  ArrowRight: { col: 1, row: 0 },
  ArrowUp: { col: 0, row: -1 }
};

export function findMetadataBoundaryCell(
  rows: SheetRow[],
  columnKeys: string[],
  current: CellPosition,
  key: MetadataArrowKey
): CellPosition {
  const offset = arrowOffsets[key];
  const currentEmpty = isMetadataCellEmpty(rows[current.row]?.[columnKeys[current.col]]);
  let next = moveCell(current, offset);
  if (!isInsideGrid(next, rows.length, columnKeys.length)) return current;

  const nextEmpty = isMetadataCellEmpty(rows[next.row]?.[columnKeys[next.col]]);
  if (!currentEmpty && !nextEmpty) {
    let target = next;
    next = moveCell(target, offset);
    while (isInsideGrid(next, rows.length, columnKeys.length)) {
      if (isMetadataCellEmpty(rows[next.row]?.[columnKeys[next.col]])) return target;
      target = next;
      next = moveCell(target, offset);
    }
    return target;
  }

  let target = current;
  while (isInsideGrid(next, rows.length, columnKeys.length)) {
    if (!isMetadataCellEmpty(rows[next.row]?.[columnKeys[next.col]])) return next;
    target = next;
    next = moveCell(target, offset);
  }

  return target;
}

function isMetadataCellEmpty(value: unknown) {
  return value == null || value === "";
}

function moveCell(cell: CellPosition, offset: CellPosition): CellPosition {
  return {
    col: cell.col + offset.col,
    row: cell.row + offset.row
  };
}

function isInsideGrid(cell: CellPosition, rowCount: number, columnCount: number) {
  return cell.row >= 0
    && cell.row < rowCount
    && cell.col >= 0
    && cell.col < columnCount;
}
