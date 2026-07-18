export interface LineageDataflowFocusTarget {
  metadataSourceId?: number | null;
  dataflowId?: string | null;
  name?: string | null;
}

export function lineageDataflowFocusSearch(target: LineageDataflowFocusTarget) {
  const params = new URLSearchParams();
  if (target.metadataSourceId != null) params.set("focusDataflowSource", String(target.metadataSourceId));
  if (target.dataflowId?.trim()) params.set("focusDataflowId", target.dataflowId.trim());
  if (target.name?.trim()) params.set("focusDataflowName", target.name.trim());
  return params.toString();
}

export function lineageDataflowFocusFromSearch(search: string): LineageDataflowFocusTarget | null {
  const params = new URLSearchParams(search);
  const dataflowId = params.get("focusDataflowId")?.trim() || null;
  const name = params.get("focusDataflowName")?.trim() || null;
  if (!dataflowId && !name) return null;
  const parsedSourceId = Number(params.get("focusDataflowSource"));
  return {
    metadataSourceId: Number.isFinite(parsedSourceId) && parsedSourceId > 0 ? parsedSourceId : null,
    dataflowId,
    name,
  };
}
