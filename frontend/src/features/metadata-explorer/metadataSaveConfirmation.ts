import type { MetadataEditorDocument } from "../../shared/api/types";

const sheetKeys = ["connections", "dataflows", "schema_hints"] as const;

type SheetKey = typeof sheetKeys[number];
type SaveAction = "create" | "update";

export interface MetadataSaveImpact {
  action: SaveAction;
  key: string;
  label: string;
  sheets: string[];
}

export interface MetadataSaveConfirmation {
  impacts: MetadataSaveImpact[];
  title: string;
  description: string;
  detail: string;
}

export function metadataSaveConfirmation(
  sourceDocument: MetadataEditorDocument | null,
  pendingDocument: MetadataEditorDocument
): MetadataSaveConfirmation {
  const impacts = metadataSaveImpacts(sourceDocument, pendingDocument);
  const updateCount = impacts.filter((impact) => impact.action === "update").length;
  const createCount = impacts.length - updateCount;
  const sourceCount = impacts.length;
  const files = sourceCount === 1 ? "source file" : "source files";

  return {
    impacts,
    title: `Save ${sourceCount} ${files}?`,
    description: saveDescription(updateCount, createCount),
    detail: updateCount
      ? "Each updated source file is backed up first. You can restore a previous version from History if needed."
      : "The new source file will be saved with the changes shown above."
  };
}

export function metadataSaveImpacts(
  sourceDocument: MetadataEditorDocument | null,
  pendingDocument: MetadataEditorDocument
): MetadataSaveImpact[] {
  const registry = createSourceRegistry(sourceDocument, pendingDocument);
  const targets = new Map<string, SourceTarget>();

  for (const document of [sourceDocument, pendingDocument]) {
    if (!document) continue;
    for (const sheetKey of sheetKeys) {
      for (const row of rowsFor(document, sheetKey)) {
        const target = targetForRow(row, document, registry);
        if (target) targets.set(target.key, target);
      }
    }
  }

  if (pendingDocument.source.scope !== "environment") {
    const target = sourceTargetFromDocument(pendingDocument, registry);
    if (target) targets.set(target.key, target);
  }

  const impacts = [...targets.values()]
    .map((target) => ({
      ...target,
      sheets: changedSheets(sourceDocument, pendingDocument, target, registry)
    }))
    .filter((target) => target.sheets.length > 0)
    .map(({ action, key, label, sheets }) => ({
      action,
      key,
      label,
      sheets: sheets.map(sheetLabel)
    }));

  if (impacts.length) return impacts;

  const fallback = sourceTargetFromDocument(pendingDocument, registry);
  return fallback ? [{ ...fallback, sheets: ["Metadata"] }] : [];
}

type SourceTarget = {
  action: SaveAction;
  id: number | null;
  key: string;
  label: string;
  name: string;
  uri: string;
};

type SourceRegistry = {
  byId: Map<number, SourceTarget>;
  byToken: Map<string, SourceTarget>;
  newByToken: Map<string, SourceTarget>;
};

function createSourceRegistry(...documents: Array<MetadataEditorDocument | null>): SourceRegistry {
  const registry: SourceRegistry = {
    byId: new Map(),
    byToken: new Map(),
    newByToken: new Map()
  };

  for (const document of documents) {
    if (!document) continue;
    const revisions = document.source.revision?.sources;
    if (Array.isArray(revisions)) {
      for (const revision of revisions) {
        if (!revision || typeof revision !== "object") continue;
        const record = revision as Record<string, unknown>;
        registerKnownTarget(registry, sourceTarget(
          sourceId(record.source_id),
          text(record.name),
          text(record.uri)
        ));
      }
    }

    if (document.source.scope !== "environment") {
      registerKnownTarget(registry, sourceTarget(
        sourceId(document.source.source_id),
        document.source.name ?? "",
        document.source.uri
      ));
    }

    for (const sheetKey of sheetKeys) {
      for (const row of rowsFor(document, sheetKey)) {
        const id = sourceId(row.__metadata_source_id);
        if (id === null) continue;
        registerKnownTarget(registry, sourceTarget(
          id,
          text(row.__metadata_source_name),
          text(row.__metadata_source_uri)
        ));
      }
    }
  }

  return registry;
}

function registerKnownTarget(registry: SourceRegistry, target: SourceTarget | null) {
  if (!target || target.id === null) return;
  const existing = registry.byId.get(target.id);
  const resolved = existing ?? target;
  if (!existing || shouldReplaceTarget(existing, target)) registry.byId.set(target.id, target);
  for (const value of [resolved.name, resolved.uri, fileName(resolved.uri), fileStem(resolved.uri)]) {
    const token = sourceToken(value);
    if (token && !registry.byToken.has(token)) registry.byToken.set(token, resolved);
  }
}

function sourceTargetFromDocument(document: MetadataEditorDocument, registry: SourceRegistry) {
  const id = sourceId(document.source.source_id);
  return id === null
    ? null
    : registry.byId.get(id) ?? sourceTarget(id, document.source.name ?? "", document.source.uri);
}

function targetForRow(row: Record<string, unknown>, document: MetadataEditorDocument, registry: SourceRegistry) {
  const id = sourceId(row.__metadata_source_id);
  if (id !== null) {
    return registry.byId.get(id) ?? sourceTarget(id, text(row.__metadata_source_name), text(row.__metadata_source_uri));
  }

  const name = text(row.__metadata_source_name);
  const uri = text(row.__metadata_source_uri);
  const token = sourceToken(name) || sourceToken(uri);
  if (token) {
    const known = registry.byToken.get(token);
    if (known) return known;
    const created = registry.newByToken.get(token) ?? sourceTarget(null, name, uri);
    if (created) registry.newByToken.set(token, created);
    return created;
  }

  return document.source.scope === "environment" ? null : sourceTargetFromDocument(document, registry);
}

function sourceTarget(id: number | null, name: string, uri: string): SourceTarget | null {
  const label = uri || name;
  if (id === null && !label) return null;
  const key = id === null ? `new:${sourceToken(name || uri)}` : `id:${id}`;
  return {
    action: id === null ? "create" : "update",
    id,
    key,
    label: label || "New metadata source",
    name: name || fileName(uri),
    uri
  };
}

function changedSheets(
  sourceDocument: MetadataEditorDocument | null,
  pendingDocument: MetadataEditorDocument,
  target: SourceTarget,
  registry: SourceRegistry
) {
  return sheetKeys.filter((sheetKey) => {
    const sourceRows = rowsForTarget(sourceDocument, sheetKey, target.key, registry);
    const pendingRows = rowsForTarget(pendingDocument, sheetKey, target.key, registry);
    const rowsChanged = JSON.stringify(normalizeRows(sourceRows)) !== JSON.stringify(normalizeRows(pendingRows));
    const columnsChanged = pendingDocument.source.scope !== "environment"
      && JSON.stringify(columnsFor(sourceDocument, sheetKey)) !== JSON.stringify(columnsFor(pendingDocument, sheetKey));
    return rowsChanged || columnsChanged;
  });
}

function rowsForTarget(
  document: MetadataEditorDocument | null,
  sheetKey: SheetKey,
  targetKey: string,
  registry: SourceRegistry
) {
  if (!document) return [];
  return rowsFor(document, sheetKey).filter((row) => targetForRow(row, document, registry)?.key === targetKey);
}

function normalizeRows(rows: Array<Record<string, unknown>>) {
  return rows.map((row) => Object.fromEntries(Object.entries(row).filter(([key]) => !key.startsWith("__"))));
}

function columnsFor(document: MetadataEditorDocument | null, sheetKey: SheetKey) {
  return document?.sheets[sheetKey]?.columns
    .filter((column) => !column.key.startsWith("__"))
    .map((column) => column.key) ?? [];
}

function rowsFor(document: MetadataEditorDocument, sheetKey: SheetKey) {
  return document.sheets[sheetKey]?.rows ?? [];
}

function sourceId(value: unknown) {
  const id = Number(value);
  return Number.isInteger(id) && id > 0 ? id : null;
}

function text(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

function sourceToken(value: string) {
  return value.trim().replace(/\\/g, "/").toLowerCase();
}

function fileName(value: string) {
  return value.replace(/\\/g, "/").split("/").filter(Boolean).at(-1) ?? "";
}

function fileStem(value: string) {
  return fileName(value).replace(/\.[^.]+$/, "");
}

function shouldReplaceTarget(current: SourceTarget, next: SourceTarget) {
  return (!current.uri && Boolean(next.uri)) || (!current.name && Boolean(next.name));
}

function sheetLabel(sheetKey: SheetKey) {
  if (sheetKey === "schema_hints") return "Schema hints";
  return sheetKey === "dataflows" ? "Dataflows" : "Connections";
}

function saveDescription(updateCount: number, createCount: number) {
  const parts: string[] = [];
  if (updateCount) parts.push(`update ${updateCount} ${updateCount === 1 ? "source file" : "source files"}`);
  if (createCount) parts.push(`create ${createCount} ${createCount === 1 ? "source file" : "source files"}`);
  return `This will ${parts.join(" and ")}. Only the files listed below will be written.`;
}
