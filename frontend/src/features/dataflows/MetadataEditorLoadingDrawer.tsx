import { X } from "lucide-react";
import { createPortal } from "react-dom";
import { EmptyState } from "../../shared/components/EmptyState";
import { useDrawerEscape } from "../../shared/hooks/useDrawerEscape";

export function MetadataEditorLoadingDrawer({ error, loading, onRetry, onClose }: {
  error: string | null;
  loading: boolean;
  onRetry: () => Promise<void>;
  onClose: () => void;
}) {
  useDrawerEscape(onClose);
  return createPortal(<aside className="metadata-drawer dataflow-drawer" role="dialog" aria-label="Dataflow details" aria-busy={loading}>
    <header className="assets-drawer-header"><h2>Dataflow details</h2><button type="button" onClick={onClose} aria-label="Close"><X size={16} /></button></header>
    <EmptyState title={loading ? "Loading metadata…" : "Metadata could not load"} detail={error ?? undefined}
      action={!loading ? <button type="button" onClick={() => void onRetry()}>Retry</button> : undefined} />
  </aside>, document.body);
}
