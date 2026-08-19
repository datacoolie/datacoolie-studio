import type { ContextMenuComponentProps } from "react-datasheet-grid";
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from "react";

interface MetadataSheetContextMenuProps extends ContextMenuComponentProps {
  columnKey: string | null;
  rowIndex: number | null;
  onAddField: () => void;
  onAddRowBelow: (rowIndex: number | null) => void;
  onCopyColumn: (columnKey: string | null) => void;
  onCopyRow: (rowIndex: number | null) => void;
  onDeleteRow: (rowIndex: number | null) => void;
  onDuplicateRow: (rowIndex: number | null) => void;
  onMoveRow: (rowIndex: number | null, offset: -1 | 1) => void;
  canMoveUp: boolean;
  canMoveDown: boolean;
  movementDisabledReason: string;
  onPasteColumn: (columnKey: string | null, rowIndex: number | null) => void;
  onPasteRow: (rowIndex: number | null) => void;
}

export function MetadataSheetContextMenu({
  clientX,
  clientY,
  close,
  columnKey,
  rowIndex,
  onAddField,
  onAddRowBelow,
  onCopyColumn,
  onCopyRow,
  onDeleteRow,
  onDuplicateRow,
  onMoveRow,
  canMoveUp,
  canMoveDown,
  movementDisabledReason,
  onPasteColumn,
  onPasteRow
}: MetadataSheetContextMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<CSSProperties>({ left: clientX, top: clientY, visibility: "hidden" });

  useEffect(() => {
    const closeWhenOutside = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node | null)) close();
    };
    document.addEventListener("pointerdown", closeWhenOutside, true);
    return () => document.removeEventListener("pointerdown", closeWhenOutside, true);
  }, [close]);

  useLayoutEffect(() => {
    const menu = menuRef.current;
    if (!menu) return;
    const margin = 8;
    const rect = menu.getBoundingClientRect();
    const left = Math.min(Math.max(margin, clientX), Math.max(margin, window.innerWidth - rect.width - margin));
    const preferredTop = clientY;
    const flippedTop = clientY - rect.height;
    const top = preferredTop + rect.height + margin > window.innerHeight
      ? Math.max(margin, flippedTop)
      : Math.min(Math.max(margin, preferredTop), Math.max(margin, window.innerHeight - rect.height - margin));
    setPosition({ left, top, visibility: "visible" });
  }, [clientX, clientY]);

  function run(action: () => void) {
    action();
    close();
  }

  return (
    <div ref={menuRef} className="metadata-context-menu" style={position} role="menu" onPointerDown={(event) => event.stopPropagation()}>
      <button type="button" onClick={() => run(() => onAddRowBelow(rowIndex))} role="menuitem">
        Insert row below
      </button>
      <button type="button" onClick={() => run(() => onDuplicateRow(rowIndex))} disabled={rowIndex == null} role="menuitem">
        Duplicate row
      </button>
      <button type="button" onClick={() => run(() => onDeleteRow(rowIndex))} disabled={rowIndex == null} role="menuitem">
        Delete row
      </button>
      <button type="button" onClick={() => run(() => onMoveRow(rowIndex, -1))} disabled={!canMoveUp} title={!canMoveUp ? movementDisabledReason : undefined} role="menuitem">
        Move row up
      </button>
      <button type="button" onClick={() => run(() => onMoveRow(rowIndex, 1))} disabled={!canMoveDown} title={!canMoveDown ? movementDisabledReason : undefined} role="menuitem">
        Move row down
      </button>
      <hr />
      <button type="button" onClick={() => run(onAddField)} role="menuitem">
        Add metadata field
      </button>
      <button type="button" onClick={() => run(() => void onCopyRow(rowIndex))} disabled={rowIndex == null} role="menuitem">
        Copy row
      </button>
      <button type="button" onClick={() => run(() => void onPasteRow(rowIndex))} disabled={rowIndex == null} role="menuitem">
        Paste row below
      </button>
      <button type="button" onClick={() => run(() => void onCopyColumn(columnKey))} disabled={!columnKey} role="menuitem">
        Copy column
      </button>
      <button type="button" onClick={() => run(() => void onPasteColumn(columnKey, rowIndex))} disabled={!columnKey || rowIndex == null} role="menuitem">
        Paste column
      </button>
    </div>
  );
}
