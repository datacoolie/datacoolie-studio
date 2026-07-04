import { Copy, Database, GitBranch, X } from "lucide-react";
import type { AssetInventoryItem } from "../../shared/api/types";
import { LineageFormatIcon } from "../lineage/components/LineageFormatIcon";
import { metadataQueryForAsset, presentAsset } from "./assetsPresentation";

interface AssetsDrawerProps {
  asset: AssetInventoryItem;
  onClose: () => void;
  onFocusInLineage: (assetId: string) => void;
  onOpenMetadata: (query: string) => void;
}

export function AssetsDrawer({
  asset,
  onClose,
  onFocusInLineage,
  onOpenMetadata,
}: AssetsDrawerProps) {
  const presentation = presentAsset(asset);
  const metadataQuery = metadataQueryForAsset(asset);
  const identifierCount = asset.identifiers.length;
  const observationCount = asset.observations.length;

  async function copyAssetId() {
    if (!navigator.clipboard?.writeText) return;
    await navigator.clipboard.writeText(asset.id);
  }

  return (
    <div className="metadata-drawer-backdrop" onMouseDown={onClose}>
      <aside className="metadata-drawer assets-drawer" aria-label="Asset details" onMouseDown={(event) => event.stopPropagation()}>
        <header className="metadata-drawer-header assets-drawer-header">
          <div className="assets-drawer-title">
            <span className="eyebrow">Asset</span>
            <h2>
              <LineageFormatIcon kind={presentation.iconKind} label={presentation.badge} size={20} />
              <span>{presentation.friendlyName}</span>
            </h2>
            <small title={presentation.fullIdentity}>{presentation.fullIdentity}</small>
          </div>
          <div className="assets-drawer-header-actions">
            <button className="icon-action small" type="button" title="Copy asset id" onClick={() => void copyAssetId()}>
              <Copy size={14} />
            </button>
            <button className="icon-action small" type="button" title="Close" onClick={onClose}>
              <X size={14} />
            </button>
          </div>
        </header>

        <div className="metadata-drawer-body assets-drawer-body">
          <section className="assets-drawer-section">
            <h3>Identity</h3>
            <dl>
              <div><dt>Canonical id</dt><dd><code>{asset.id}</code></dd></div>
              <div><dt>Status</dt><dd>{asset.declaration_status}</dd></div>
              <div><dt>Role</dt><dd>{asset.roles.length ? asset.roles.join(", ") : "-"}</dd></div>
              <div><dt>Identifiers</dt><dd>{identifierCount}</dd></div>
              <div><dt>Observations</dt><dd>{observationCount}</dd></div>
            </dl>
          </section>

          <section className="assets-drawer-section">
            <h3>Location</h3>
            <dl>
              <div><dt>Connection</dt><dd>{asset.connection_name || "-"}</dd></div>
              <div><dt>Type</dt><dd>{asset.connection_type || "-"}</dd></div>
              <div><dt>Catalog</dt><dd>{asset.catalog || "-"}</dd></div>
              <div><dt>Database</dt><dd>{asset.database || "-"}</dd></div>
              <div><dt>Schema</dt><dd>{asset.schema_name || "-"}</dd></div>
              <div><dt>Table</dt><dd>{asset.table || "-"}</dd></div>
              <div><dt>Path</dt><dd>{asset.path || "-"}</dd></div>
              <div><dt>Format</dt><dd>{asset.format || "-"}</dd></div>
            </dl>
          </section>

          <section className="assets-drawer-section">
            <h3>Lineage Context</h3>
            <dl>
              <div><dt>Upstream</dt><dd>{asset.upstream_count}</dd></div>
              <div><dt>Downstream</dt><dd>{asset.downstream_count}</dd></div>
              <div><dt>Input flows</dt><dd>{asset.input_dataflow_count}</dd></div>
              <div><dt>Output flows</dt><dd>{asset.output_dataflow_count}</dd></div>
              <div><dt>Dependencies</dt><dd>{asset.dependency_count}</dd></div>
              <div><dt>Issues</dt><dd>{asset.issue_count}</dd></div>
            </dl>
          </section>

          <section className="assets-drawer-section">
            <h3>Provenance</h3>
            {asset.metadata_sources.length ? (
              <ul className="assets-list">
                {asset.metadata_sources.map((source) => (
                  <li key={source.id} title={source.uri}>
                    <strong>#{source.id}</strong>
                    <span>{source.uri}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="assets-empty-inline">No metadata source is linked to this asset.</div>
            )}
          </section>

          <section className="assets-drawer-section">
            <h3>Issues</h3>
            {asset.issues.length ? (
              <ul className="assets-list assets-issues-list">
                {asset.issues.map((issue, index) => (
                  <li key={`${issue.code}-${index}`}>
                    <strong className={`assets-severity assets-severity-${issue.severity}`}>{issue.severity}</strong>
                    <span>{issue.message}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="assets-empty-inline">No issue is currently linked to this asset.</div>
            )}
          </section>

          <section className="assets-drawer-section">
            <h3>Actions</h3>
            <div className="assets-drawer-actions">
              <button
                className="text-action"
                type="button"
                onClick={() => {
                  onFocusInLineage(asset.id);
                  onClose();
                }}
              >
                <GitBranch size={14} />
                Focus In Lineage
              </button>
              <button
                className="text-action"
                type="button"
                onClick={() => {
                  onOpenMetadata(metadataQuery);
                  onClose();
                }}
              >
                <Database size={14} />
                Open Metadata
              </button>
            </div>
          </section>
        </div>
      </aside>
    </div>
  );
}
