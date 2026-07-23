import { X } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { AssetDefinitionResponse, LineageAsset } from "../../../shared/api/domainTypes";
import { SourceCodeViewer } from "../../assets/SourceCodeViewer";
import { assetIconKind, assetTypeTone, presentLineageAsset } from "../model/presentation";
import { LineageFormatIcon } from "./LineageFormatIcon";

interface LineageCodeDialogProps {
  asset: LineageAsset;
  definition: AssetDefinitionResponse | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
}

interface DefinitionView {
  id: "formatted" | "raw" | "source";
  label: string;
  language: "sql" | "python";
  content: string;
}

export function LineageCodeDialog({ asset, definition, loading, error, onClose }: LineageCodeDialogProps) {
  const titleId = useId();
  const dialogRef = useRef<HTMLElement | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const views = definitionViews(asset, definition);
  const [activeViewId, setActiveViewId] = useState(views[0]?.id || "raw");
  const activeView = views.find((view) => view.id === activeViewId) || views[0];
  const presentation = presentLineageAsset(asset);
  const tone = assetTypeTone(asset.asset_type);
  const typeLabel = asset.asset_type === "sql_query" ? "SQL query" : "Python function";
  const sourceContext = definitionSourceContext(definition, activeView?.content || "");

  useEffect(() => {
    if (asset.asset_type === "sql_query" && definition?.formatted?.trim()) {
      setActiveViewId("formatted");
    }
  }, [asset.asset_type, asset.id, definition?.formatted]);

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const focusFrame = window.requestAnimationFrame(() => closeButtonRef.current?.focus());

    function handleDialogKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        event.stopImmediatePropagation();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = [...dialogRef.current.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      )].filter((element) => !element.hidden);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", handleDialogKeyDown, true);
    return () => {
      window.cancelAnimationFrame(focusFrame);
      document.removeEventListener("keydown", handleDialogKeyDown, true);
      document.body.style.overflow = previousOverflow;
      window.requestAnimationFrame(() => previousFocus?.focus());
    };
  }, [onClose]);

  return createPortal(
    <div className={`lineage-code-dialog-backdrop asset-tone-${tone}`} onMouseDown={onClose}>
      <section
        ref={dialogRef}
        className="lineage-code-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="lineage-code-dialog-header">
          <span className="lineage-code-dialog-icon">
            <LineageFormatIcon kind={assetIconKind(asset.asset_type)} label={typeLabel} size={20} />
          </span>
          <div className="lineage-code-dialog-title">
            <span>{typeLabel}</span>
            <h2 id={titleId}>{presentation.locator}</h2>
            <small title={presentation.fullIdentity}>{presentation.fullIdentity}</small>
          </div>
          <button ref={closeButtonRef} className="icon-action" type="button" aria-label="Close code viewer" title="Close" onClick={onClose}>
            <X size={17} />
          </button>
        </header>

        <div className="lineage-code-dialog-context">
          <span>{loading ? "loading" : error ? "unavailable" : humanize(definition?.status || "available")}</span>
          {sourceContext.map((item) => <span key={item}>{item}</span>)}
        </div>

        <div className={`lineage-code-dialog-body${activeView ? "" : " is-status"}`}>
          {activeView ? (
            <SourceCodeViewer
              key={activeView.id}
              content={activeView.content}
              language={definition?.language || activeView.language}
              ariaLabel={`${activeView.label} for ${presentation.locator}`}
              defaultWrapped
              showHeightControl={false}
              toolbarLeading={views.length > 1 ? (
                <div className="lineage-code-dialog-tabs" role="tablist" aria-label="Definition view">
                  {views.map((view) => (
                    <button
                      key={view.id}
                      className={view.id === activeView.id ? "is-active" : ""}
                      type="button"
                      role="tab"
                      aria-selected={view.id === activeView.id}
                      onClick={() => setActiveViewId(view.id)}
                    >
                      {view.label}
                    </button>
                  ))}
                </div>
              ) : <span className="lineage-code-dialog-view-label">{activeView.label}</span>}
            />
          ) : (
            <div className={`lineage-code-dialog-status${error ? " is-error" : ""}`}>
              <strong>{loading ? "Loading code definition…" : error ? "Unable to load code" : "Code definition is unavailable"}</strong>
              <span>{loading ? "Resolving the definition from this environment." : error || definitionDiagnostic(definition)}</span>
            </div>
          )}
        </div>
      </section>
    </div>,
    document.body,
  );
}

function definitionViews(asset: LineageAsset, definition: AssetDefinitionResponse | null): DefinitionView[] {
  if (asset.asset_type === "python_function") {
    const source = definition?.source?.trim() || definition?.formatted?.trim() || definition?.raw?.trim() || "";
    return source ? [{ id: "source", label: "Source", language: "python", content: source }] : [];
  }

  const formatted = definition?.formatted?.trim() || "";
  const raw = definition?.raw?.trim() || asset.query?.trim() || "";
  const views: DefinitionView[] = [];
  if (formatted) views.push({ id: "formatted", label: "Formatted", language: "sql", content: formatted });
  if (raw && raw !== formatted) views.push({ id: "raw", label: "Raw", language: "sql", content: raw });
  return views;
}

function definitionSourceContext(definition: AssetDefinitionResponse | null, content: string) {
  const lineCount = definition?.line_count || (content ? content.split(/\r?\n/u).length : 0);
  return [
    definition?.relative_path,
    definition?.function_path,
    lineCount ? `${lineCount} ${lineCount === 1 ? "line" : "lines"}` : null,
  ].filter((value): value is string => Boolean(value));
}

function humanize(value: string) {
  return value.replace(/_/gu, " ");
}

function definitionDiagnostic(definition: AssetDefinitionResponse | null) {
  return definition?.diagnostics?.find((item) => item.message)?.message || "No code content is available for this asset.";
}
