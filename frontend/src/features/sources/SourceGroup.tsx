import type { ReactNode } from "react";
import { Clock } from "lucide-react";

import type { SourcePath } from "../../shared/api/domainTypes";
import type { SourceSyncStatus } from "../../shared/api/contractTypes";
import { sourceKey, type SourceKind } from "../../shared/lib/sources";
import { SourceCard } from "./SourceCard";
import type { LogSyncActivities } from "./logSyncModel";
import type { SourceOperations } from "./sourceWorkspaceModel";
import { sourceOperationFor } from "./sourceWorkspaceModel";
import type {
  SourceDeleteHandler,
  SourceRetryObservationHandler,
  SourceSyncHandler,
  SourceUpdateHandler,
  SourceValidateHandler,
} from "./sourcePageTypes";

export type SourceGroupProps = {
  title: string;
  description: string;
  icon: ReactNode;
  kind: SourceKind;
  items: SourcePath[];
  emptyTitle: string;
  emptyDetail: string;
  busy: boolean;
  onUpdate: SourceUpdateHandler;
  onDelete: SourceDeleteHandler;
  onValidate: SourceValidateHandler;
  onSync: SourceSyncHandler;
  onRetryObservation: SourceRetryObservationHandler;
  onRequestLogSync?: (id: number) => void;
  syncStatuses: Record<string, SourceSyncStatus>;
  sourceOperations: SourceOperations;
  environmentId: number | null;
  timezoneName: string | null;
  externalSyncActivities?: LogSyncActivities;
};

export function SourceGroup({
  title,
  description,
  icon,
  kind,
  items,
  emptyTitle,
  emptyDetail,
  busy,
  onUpdate,
  onDelete,
  onValidate,
  onSync,
  onRetryObservation,
  onRequestLogSync,
  syncStatuses,
  sourceOperations,
  environmentId,
  timezoneName,
  externalSyncActivities = {},
}: SourceGroupProps) {
  const enabled = items.filter((item) => item.enabled).length;

  async function validate(id: number) {
    await onValidate(kind, id);
  }

  async function sync(id: number) {
    if (kind === "logs" && onRequestLogSync) {
      onRequestLogSync(id);
      return;
    }
    await onSync(kind, id);
  }

  return (
    <section className="source-group">
      <div className="source-group-header">
        <div className="source-group-title">
          {icon}
          <div>
            <strong>{title}</strong>
            <span>{description}</span>
          </div>
        </div>
        <div className="source-group-toolbar">
          <em>{enabled}/{items.length} enabled</em>
        </div>
      </div>
      <div className="source-card-list">
        {items.map((item) => (
          <SourceCard
            key={item.id}
            item={item}
            kind={kind}
            busy={busy}
            operation={sourceOperationFor(sourceOperations, environmentId, kind, item.id)}
            syncActivity={externalSyncActivities[item.id]}
            validation={item.latest_validation ?? null}
            syncStatus={syncStatuses[sourceKey(kind, item.id)] ?? null}
            onUpdate={onUpdate}
            onDelete={onDelete}
            onValidate={validate}
            onSync={sync}
            onRetryObservation={onRetryObservation}
            timezoneName={timezoneName}
          />
        ))}
        {!items.length ? (
          <div className="source-card-empty">
            <Clock size={18} />
            <div>
              <strong>{emptyTitle}</strong>
              <span>{emptyDetail}</span>
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}
