import { connectionStageFamily } from "../../shared/connectionOrder";

/**
 * Returns the stable CSS class used for connection-owned stage presentation.
 *
 * Classification remains in shared/connectionOrder.ts; this module owns only
 * the Monitoring presentation contract so endpoint components do not depend on
 * the large monitoring primitives module.
 */
export function monitoringConnectionStageClass(connectionName: unknown): string {
  const family = connectionStageFamily(connectionName);
  return family ? `monitor-stage-${family}` : "";
}
