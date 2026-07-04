import { Boxes, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { AssetInventoryItem, AssetsResponse } from "../../shared/api/types";
import { EmptyState } from "../../shared/components/EmptyState";
import { DataTable, formatNumber, type TableColumn } from "../monitoring/MonitoringCharts";
import { LineageFormatIcon } from "../lineage/components/LineageFormatIcon";
import { assetSearchValues, presentAsset } from "./assetsPresentation";
import { AssetsDrawer } from "./AssetsDrawer";

interface AssetsViewProps {
  assets: AssetsResponse | null;
  loading: boolean;
  routeSearch?: string;
  onFocusInLineage: (assetId: string) => void;
  onOpenMetadata: (query: string) => void;
}

type AssetRow = AssetInventoryItem & Record<string, unknown>;

interface AssetFilters {
  connection: string;
  format: string;
  kind: string;
  role: string;
  status: string;
  issueState: string;
}

const EMPTY_FILTERS: AssetFilters = {
  connection: "",
  format: "",
  kind: "",
  role: "",
  status: "",
  issueState: "",
};

export function AssetsView({
  assets,
  loading,
  routeSearch,
  onFocusInLineage,
  onOpenMetadata,
}: AssetsViewProps) {
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState<AssetFilters>(EMPTY_FILTERS);
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(routeSearch ?? "");
    const requestedQuery = params.get("q");
    if (requestedQuery !== null) {
      setQuery(requestedQuery);
    }
    const requestedAssetId = params.get("assetId");
    if (requestedAssetId) {
      setSelectedAssetId(requestedAssetId);
    }
  }, [routeSearch]);

  const allAssets = assets?.assets ?? [];
  const filteredAssets = useMemo(
    () => filterAssets(allAssets, query, filters) as AssetRow[],
    [allAssets, query, filters]
  );
  const selectedAsset = useMemo(
    () => allAssets.find((asset) => asset.id === selectedAssetId) ?? null,
    [allAssets, selectedAssetId]
  );
  const columns = useMemo<TableColumn<AssetRow>[]>(() => [
    {
      key: "asset",
      label: "Asset",
      sortable: true,
      sortKey: "display_name",
      minWidth: 300,
      fillPriority: "last",
      render: (asset) => <AssetCell asset={asset} />,
    },
    {
      key: "connection_name",
      label: "Connection",
      sortable: true,
      autoFit: true,
      minWidth: 180,
      maxWidth: 260,
      render: (asset) => <ConnectionCell asset={asset} />,
    },
    {
      key: "kind",
      label: "Kind",
      sortable: true,
      autoFit: true,
      minWidth: 90,
      maxWidth: 130,
      render: (asset) => <span className="assets-pill">{humanize(asset.kind)}</span>,
    },
    {
      key: "format",
      label: "Format",
      sortable: true,
      autoFit: true,
      minWidth: 90,
      maxWidth: 120,
      render: (asset) => <span className="assets-pill">{humanize(asset.format)}</span>,
    },
    {
      key: "roles",
      label: "Role",
      sortable: true,
      autoFit: true,
      minWidth: 88,
      maxWidth: 150,
      render: (asset) => <span className="assets-pill">{asset.roles.length ? asset.roles.join(", ") : "-"}</span>,
    },
    {
      key: "lineage",
      label: "Lineage",
      sortable: true,
      sortKey: "downstream_count",
      autoFit: true,
      minWidth: 110,
      maxWidth: 170,
      render: (asset) => <LineageCell asset={asset} />,
    },
    {
      key: "declaration_status",
      label: "Declaration",
      sortable: true,
      autoFit: true,
      minWidth: 100,
      maxWidth: 132,
      render: (asset) => <StatusCell status={asset.declaration_status} />,
    },
    {
      key: "issue_count",
      label: "Issues",
      sortable: true,
      autoFit: true,
      minWidth: 68,
      maxWidth: 92,
      render: (asset) => <IssueCountCell count={asset.issue_count} />,
    },
    {
      key: "metadata_sources",
      label: "Sources",
      sortable: true,
      sortKey: "metadata_source_ids",
      autoFit: true,
      minWidth: 70,
      maxWidth: 90,
      render: (asset) => formatNumber(asset.metadata_source_ids.length),
    },
  ], []);

  if (!assets && !loading) {
    return <EmptyState icon={<Boxes size={24} />} title="Add metadata source to view assets" />;
  }
  if (!assets) {
    return <EmptyState icon={<Boxes size={24} />} title="Loading assets" />;
  }

  return (
    <div className="view-stack assets-view">
      <section className="table-panel assets-panel">
        <div className="panel-toolbar compact assets-toolbar">
          <div>
            <h2>Assets</h2>
            <span>Environment asset inventory derived from lineage identity.</span>
          </div>
        </div>

        <div className="assets-summary-strip">
          <SummaryTile label="Assets" value={assets.summary.assets} />
          <SummaryTile label="Declared" value={assets.summary.declared} />
          <SummaryTile label="Discovered only" value={assets.summary.discovered_only} />
          <SummaryTile label="Stitched" value={assets.summary.stitched} />
          <SummaryTile label="With issues" value={assets.summary.with_issues} tone={assets.summary.with_issues > 0 ? "warning" : "neutral"} />
        </div>

        <div className="assets-filters">
          <label className="search-box assets-search">
            <Search size={14} />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search asset id, table, path, connection, format…"
            />
          </label>
          <select value={filters.connection} onChange={(event) => setFilters((current) => ({ ...current, connection: event.target.value }))}>
            <option value="">All connections</option>
            {assets.filter_options.connections.map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
          <select value={filters.format} onChange={(event) => setFilters((current) => ({ ...current, format: event.target.value }))}>
            <option value="">All formats</option>
            {assets.filter_options.formats.map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
          <select value={filters.kind} onChange={(event) => setFilters((current) => ({ ...current, kind: event.target.value }))}>
            <option value="">All kinds</option>
            {assets.filter_options.kinds.map((value) => <option key={value} value={value}>{humanize(value)}</option>)}
          </select>
          <select value={filters.role} onChange={(event) => setFilters((current) => ({ ...current, role: event.target.value }))}>
            <option value="">All roles</option>
            {assets.filter_options.roles.map((value) => <option key={value} value={value}>{value}</option>)}
          </select>
          <select value={filters.status} onChange={(event) => setFilters((current) => ({ ...current, status: event.target.value }))}>
            <option value="">All declaration states</option>
            {assets.filter_options.declaration_statuses.map((value) => <option key={value} value={value}>{humanize(value)}</option>)}
          </select>
          <select value={filters.issueState} onChange={(event) => setFilters((current) => ({ ...current, issueState: event.target.value }))}>
            <option value="">All issue states</option>
            {assets.filter_options.issue_states.map((value) => <option key={value} value={value}>{humanize(value)}</option>)}
          </select>
        </div>

        {filteredAssets.length ? (
          <DataTable<AssetRow>
            className="assets-table"
            columns={columns}
            fixedLayout
            maxRows={Math.max(filteredAssets.length, 12)}
            onRowClick={(asset) => setSelectedAssetId(asset.id)}
            rows={filteredAssets}
          />
        ) : (
          <div className="table-empty">No assets match the current filters.</div>
        )}
      </section>

      {selectedAsset ? (
        <AssetsDrawer
          asset={selectedAsset}
          onClose={() => setSelectedAssetId(null)}
          onFocusInLineage={onFocusInLineage}
          onOpenMetadata={onOpenMetadata}
        />
      ) : null}
    </div>
  );
}

function SummaryTile({ label, value, tone = "neutral" }: { label: string; value: number; tone?: "neutral" | "warning" }) {
  return (
    <div className={`assets-summary-tile assets-summary-${tone}`}>
      <span>{label}</span>
      <strong>{formatNumber(value)}</strong>
    </div>
  );
}

function AssetCell({ asset }: { asset: AssetInventoryItem }) {
  const presentation = presentAsset(asset);
  return (
    <span className="assets-asset-cell">
      <span className="assets-asset-icon">
        <LineageFormatIcon kind={presentation.iconKind} label={presentation.badge} size={18} />
      </span>
      <span className="assets-asset-copy">
        <strong>{presentation.friendlyName}</strong>
        <small title={presentation.fullIdentity}>{presentation.fullIdentity}</small>
      </span>
    </span>
  );
}

function ConnectionCell({ asset }: { asset: AssetInventoryItem }) {
  return (
    <span className="assets-connection-cell">
      <strong>{asset.connection_name || "-"}</strong>
      <small>{asset.connection_type || "-"}</small>
    </span>
  );
}

function LineageCell({ asset }: { asset: AssetInventoryItem }) {
  return (
    <span className="assets-lineage-cell">
      <strong>{asset.upstream_count} up · {asset.downstream_count} down</strong>
      <small>{asset.input_dataflow_count} in · {asset.output_dataflow_count} out</small>
    </span>
  );
}

function IssueCountCell({ count }: { count: number }) {
  return (
    <span className={count > 0 ? "assets-issue-count has-issues" : "assets-issue-count"}>
      {formatNumber(count)}
    </span>
  );
}

function StatusCell({ status }: { status: string }) {
  return <span className={`assets-status-chip status-${status}`}>{humanize(status)}</span>;
}

function filterAssets(assets: AssetInventoryItem[], query: string, filters: AssetFilters) {
  const needle = query.trim().toLowerCase();
  return assets.filter((asset) => {
    if (needle) {
      const values = assetSearchValues(asset).map((value) => value.toLowerCase());
      if (!values.some((value) => value.includes(needle))) return false;
    }
    if (filters.connection && asset.connection_name !== filters.connection) return false;
    if (filters.format && (asset.format || "") !== filters.format) return false;
    if (filters.kind && asset.kind !== filters.kind) return false;
    if (filters.role && !asset.roles.includes(filters.role)) return false;
    if (filters.status && asset.declaration_status !== filters.status) return false;
    if (filters.issueState === "with_issues" && asset.issue_count === 0) return false;
    if (filters.issueState === "clean" && asset.issue_count > 0) return false;
    return true;
  });
}

function humanize(value: string | null | undefined) {
  if (!value) return "-";
  return value.replace(/_/g, " ");
}
