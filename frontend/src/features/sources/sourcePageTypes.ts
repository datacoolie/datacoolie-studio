import type {
  LogSyncRequest,
  SourceDeleteImpact,
  SourceReadCheckResult,
  SourceSyncStatus,
} from "../../shared/api/contractTypes";
import type { SourcePath, StorageBinding } from "../../shared/api/domainTypes";
import type { SourceKind } from "../../shared/lib/sources";

export type SourceUpdatePayload = {
  uri?: string;
  label?: string | null;
  enabled?: boolean;
  source_config?: Record<string, unknown>;
  storage?: StorageBinding;
  sync_schedule_enabled?: boolean;
  sync_interval_minutes?: number | null;
};

export type SourceUpdateHandler = (
  kind: SourceKind,
  id: number,
  payload: SourceUpdatePayload,
) => Promise<void>;

export type SourceDeleteHandler = (kind: SourceKind, id: number) => Promise<void>;

export type SourceValidateHandler = (kind: SourceKind, id: number) => Promise<SourceReadCheckResult>;

export type SourceSyncHandler = (
  kind: SourceKind,
  id: number,
  logSyncRequest?: LogSyncRequest,
) => Promise<SourceSyncStatus>;

export type SourceRetryObservationHandler = (kind: SourceKind, id: number) => Promise<SourceSyncStatus>;

export type SourceEntry = {
  source: SourcePath;
  kind: SourceKind;
};

export type SourceDeletePrompt = {
  entries: SourceEntry[];
  impacts: SourceDeleteImpact[];
};

export type SourceLogSyncPrompt = {
  entries: SourceEntry[];
};
