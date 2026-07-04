export type SheetKey = "connections" | "dataflows" | "schema_hints";

export type SheetRow = Record<string, unknown> & {
  __rowId: string;
  __rowIndex: number;
  __isAddRow?: boolean;
};

export type SelectionState = {
  min: { row: number; col: number; colId?: string };
  max: { row: number; col: number; colId?: string };
} | null;
