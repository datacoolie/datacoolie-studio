import type { MetadataWriteOperation } from "../api/domainTypes";

/** Server operation identity survives a failed publication or follow-up read. */
export class MetadataPublicationError extends Error {
  constructor(
    message: string,
    readonly operation: MetadataWriteOperation,
    readonly phase: "publication" | "workspace-refresh",
    readonly reason?: unknown,
  ) {
    super(message);
    this.name = "MetadataPublicationError";
  }
}
