import { Boxes, Database, Table2 } from "lucide-react";
import { Tag } from "../../shared/components/Tag";

/**
 * Placeholder page for the upcoming Master Data module. The capability is
 * registered in the catalog (disabled by default) so the architecture and
 * navigation are ready before the feature ships.
 */
export function MasterDataPage() {
  return (
    <div className="module-placeholder">
      <div className="module-placeholder-icon">
        <Boxes size={30} />
      </div>
      <Tag tone="info">Coming soon</Tag>
      <h2>Master Data</h2>
      <p>
        Define database connections and manage centralized reference tables from one place,
        instead of maintaining many manual spreadsheets.
      </p>
      <div className="module-placeholder-features">
        <Tag tone="neutral">
          <Database size={13} /> Define connections
        </Tag>
        <Tag tone="neutral">
          <Table2 size={13} /> Create tables
        </Tag>
        <Tag tone="neutral">
          <Boxes size={13} /> Centralized data entry
        </Tag>
      </div>
    </div>
  );
}
