import { AlertTriangle, CheckCircle2, X, XCircle } from "lucide-react";

export interface OperationNotice {
  tone: "success" | "warning" | "error";
  title: string;
  detail: string;
  errors?: string[];
}

export function OperationNotification({ notice, onClose }: { notice: OperationNotice; onClose: () => void }) {
  const Icon = notice.tone === "success" ? CheckCircle2 : notice.tone === "warning" ? AlertTriangle : XCircle;
  const errors = notice.errors ?? [];
  return (
    <aside className={`operation-notification tone-${notice.tone}`} role={notice.tone === "error" ? "alert" : "status"} aria-live="polite">
      <Icon className="operation-notification-icon" size={18} aria-hidden="true" />
      <div className="operation-notification-body">
        <strong>{notice.title}</strong>
        <span>{notice.detail}</span>
        {errors.length ? (
          <ul>
            {errors.map((error, index) => <li key={`${error}-${index}`}>{error}</li>)}
          </ul>
        ) : null}
      </div>
      <button type="button" className="operation-notification-close" onClick={onClose} aria-label="Dismiss notification" title="Dismiss notification">
        <X size={15} />
      </button>
    </aside>
  );
}
