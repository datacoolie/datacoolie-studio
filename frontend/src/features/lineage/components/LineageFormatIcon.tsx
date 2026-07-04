import { Icon } from "@iconify/react";
import apacheParquetIcon from "@iconify-icons/simple-icons/apacheparquet";
import avroIcon from "@iconify-icons/vscode-icons/file-type-avro";
import binaryIcon from "@iconify-icons/vscode-icons/file-type-binary";
import excelIcon from "@iconify-icons/vscode-icons/file-type-excel";
import jsonIcon from "@iconify-icons/vscode-icons/file-type-light-json";
import pythonIcon from "@iconify-icons/vscode-icons/file-type-python";
import plsqlIcon from "@iconify-icons/vscode-icons/file-type-plsql";
import textIcon from "@iconify-icons/vscode-icons/file-type-text";
import xmlIcon from "@iconify-icons/vscode-icons/file-type-xml";
import yamlIcon from "@iconify-icons/vscode-icons/file-type-yaml";
import zipIcon from "@iconify-icons/vscode-icons/file-type-zip";
import {
  Archive,
  Binary,
  Code,
  Columns3,
  Database,
  File,
  FileCode2,
  FileText,
  Globe,
  Layers3,
  ListTree,
  PanelsTopLeft,
  Sheet,
  Snowflake,
  Table2,
  TableProperties,
  type LucideIcon
} from "lucide-react";
import apiIcon from "../../../assets/lineage/api.svg";
import apacheIcebergIcon from "../../../assets/lineage/apache-iceberg.png";
import csvIcon from "../../../assets/lineage/csv.png";
import deltaLakeIcon from "../../../assets/lineage/delta-lake.svg";
import pythonFunctionIcon from "../../../assets/lineage/python-function.png";
import type { AssetIconKind } from "../model/presentation";

const LOCAL_ICON_ASSETS: Partial<Record<AssetIconKind, string>> = {
  api: apiIcon,
  code: pythonFunctionIcon,
  csv: csvIcon,
  delta: deltaLakeIcon,
  iceberg: apacheIcebergIcon,
  python: pythonFunctionIcon
};

const ICONIFY_ICONS: Partial<Record<AssetIconKind, typeof apacheParquetIcon>> = {
  archive: zipIcon,
  avro: avroIcon,
  binary: binaryIcon,
  excel: excelIcon,
  json: jsonIcon,
  parquet: apacheParquetIcon,
  python: pythonIcon,
  sql: plsqlIcon,
  text: textIcon,
  xml: xmlIcon,
  yaml: yamlIcon
};

const ICONIFY_COLORS: Partial<Record<AssetIconKind, string>> = {
  parquet: "#2396c8"
};

const LUCIDE_ICONS: Record<AssetIconKind, LucideIcon> = {
  api: Globe,
  archive: Archive,
  avro: FileCode2,
  binary: Binary,
  code: Code,
  csv: TableProperties,
  database: Database,
  delta: Layers3,
  excel: Sheet,
  file: File,
  iceberg: Snowflake,
  json: FileCode2,
  orc: Columns3,
  parquet: PanelsTopLeft,
  python: FileCode2,
  sql: Database,
  table: Table2,
  text: FileText,
  xml: Code,
  yaml: ListTree
};

export function LineageFormatIcon({
  kind,
  label,
  size = 18
}: {
  kind: AssetIconKind;
  label: string;
  size?: number;
}) {
  const localAsset = LOCAL_ICON_ASSETS[kind];
  if (localAsset) {
    return (
      <img
        alt={label}
        className="lineage-format-icon lineage-format-icon-brand"
        height={size}
        src={localAsset}
        width={size}
      />
    );
  }

  const icon = ICONIFY_ICONS[kind];
  if (icon) {
    return (
      <Icon
        aria-label={label}
        className="lineage-format-icon lineage-format-icon-brand"
        color={ICONIFY_COLORS[kind]}
        height={size}
        icon={icon}
        role="img"
        width={size}
      />
    );
  }

  const FallbackIcon = LUCIDE_ICONS[kind];
  if (FallbackIcon) {
    return <FallbackIcon aria-label={label} className="lineage-format-icon" role="img" size={size} />;
  }

  return (
    <span
      aria-label={label}
      className="lineage-format-icon lineage-format-icon-text"
      role="img"
      style={{ width: size, height: size }}
    >
      {fallbackText(label)}
    </span>
  );
}

function fallbackText(label: string) {
  return label.replace(/[^a-z0-9]/gi, "").slice(0, 3).toUpperCase() || "?";
}
