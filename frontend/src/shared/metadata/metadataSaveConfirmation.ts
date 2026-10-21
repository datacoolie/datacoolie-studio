import type { MetadataWriteOperation, WriteSafety } from "../api/domainTypes";

export interface MetadataSaveImpact {
  action: "create" | "update";
  key: string;
  label: string;
  sheets: string[];
  provider?: string | null;
  writeSafety?: WriteSafety | null;
  status?: "pending" | "applied" | "conflict" | "failed";
}

export interface MetadataSaveConfirmation {
  impacts: MetadataSaveImpact[];
  sqlImpacts: MetadataSaveImpact[];
  title: string;
  description: string;
  detail: string;
  safetyNotice?: string;
  refreshOnly?: boolean;
}

/** Build confirmation solely from the server's durable operation plan. */
export function metadataWriteConfirmation(
  operation: MetadataWriteOperation,
): MetadataSaveConfirmation {
  if (operation.state === "succeeded") {
    return { impacts: [], sqlImpacts: [], title: "Source saved", description: "Publication succeeded. Refresh the workspace to load the saved document.", detail: "Refreshing reads the workspace; it does not write the source again.", refreshOnly: true };
  }
  const impacts = operation.items
    .filter((item) => item.kind !== "sql")
    .map((item) => impactFromItem(operation.operation_key, item));
  const sqlImpacts = operation.items
    .filter((item) => item.kind === "sql")
    .map((item) => impactFromItem(operation.operation_key, item));
  const changeCount = impacts.length + sqlImpacts.length;
  const changes = changeCount === 1 ? "change" : "changes";
  const hasApplied = operation.items.some((item) => item.state === "applied");
  const hasConflict = operation.items.some((item) => item.state === "conflict");
  const hasVerifiedWrite = operation.items.some(
    (item) => item.write_safety === "verified",
  );

  return {
    impacts,
    sqlImpacts,
    title: `Save ${changeCount} ${changes}?`,
    description: `The server prepared ${changeCount || "no"} source ${changes}. Only the listed targets will be written.`,
    detail: hasConflict
      ? "A target changed outside Studio. Reload or abandon this operation before preparing a new write."
      : hasApplied
        ? "A previous attempt already published one or more targets. Confirming retry verifies their content and continues with unresolved targets."
        : "Changed metadata files are backed up before publication. Targets are protected by the storage capability shown by the server.",
    safetyNotice: hasVerifiedWrite
      ? "This provider verifies content before and after writing, but cannot prevent an external writer from changing the file during publication."
      : undefined,
  };
}

function impactFromItem(
  operationKey: string,
  item: MetadataWriteOperation["items"][number],
): MetadataSaveImpact {
  return {
    action: item.action === "create" ? "create" : "update",
    key: `operation:${operationKey}:${item.id}`,
    label: item.label || item.target_uri,
    sheets: item.sheets,
    provider: item.provider,
    writeSafety: item.write_safety,
    status: item.state === "applied"
      ? "applied"
      : item.state === "conflict"
        ? "conflict"
        : item.state === "failed"
          ? "failed"
          : "pending",
  };
}
