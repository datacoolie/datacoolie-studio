from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from datacoolie_studio.domains.lineage.service import load_or_build_lineage
from datacoolie_studio.domains.metadata.service import load_environment_metadata
from datacoolie_studio.domains.workspace import service as workspace


@dataclass(frozen=True)
class AssetIssue:
    severity: str
    code: str
    message: str
    dataflow_id: str | None = None
    metadata_source_id: int | None = None
    reference_id: str | None = None
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "dataflow_id": self.dataflow_id,
            "metadata_source_id": self.metadata_source_id,
            "reference_id": self.reference_id,
            "details": self.details or {},
        }


def list_environment_assets(session: Session, environment_id: int) -> dict[str, Any]:
    sources = workspace.list_metadata_sources(session, environment_id)
    code_artifacts = workspace.list_code_artifacts(session, environment_id)
    metadata = load_environment_metadata(session, sources)
    lineage = load_or_build_lineage(session, metadata, environment_id, code_artifacts)
    source_uri_by_id = _metadata_source_uri_by_id(metadata, lineage)
    return build_assets_inventory(lineage, source_uri_by_id)


def get_environment_asset(session: Session, environment_id: int, asset_id: str) -> dict[str, Any] | None:
    payload = list_environment_assets(session, environment_id)
    asset = next((item for item in payload["assets"] if item["id"] == asset_id), None)
    if asset is None:
        return None
    return {
        "asset": asset,
        "diagnostics": asset["issues"],
    }


def build_assets_inventory(
    lineage: dict[str, Any],
    metadata_source_uri_by_id: dict[int, str],
) -> dict[str, Any]:
    assets = list(lineage.get("assets") or [])
    dataflows = list(lineage.get("dataflows") or [])
    dependencies = list(lineage.get("dependencies") or [])
    references = list(lineage.get("references") or [])
    diagnostics = list(lineage.get("diagnostics") or [])

    input_dataflow_counts = Counter(str(item.get("destination_asset_id")) for item in dataflows)
    output_dataflow_counts = Counter(str(item.get("source_asset_id")) for item in dataflows)
    dependency_counts = Counter(str(item.get("target_asset_id")) for item in dependencies)

    upstream_assets: dict[str, set[str]] = defaultdict(set)
    downstream_assets: dict[str, set[str]] = defaultdict(set)
    dataflow_asset_links: dict[str, set[str]] = defaultdict(set)
    for dataflow in dataflows:
        source_id = str(dataflow.get("source_asset_id"))
        destination_id = str(dataflow.get("destination_asset_id"))
        dataflow_id = str(dataflow.get("dataflow_id") or "")
        if source_id and destination_id:
            upstream_assets[destination_id].add(source_id)
            downstream_assets[source_id].add(destination_id)
        if dataflow_id:
            dataflow_asset_links[dataflow_id].update({source_id, destination_id})

    for dependency in dependencies:
        source = dependency.get("source") or {}
        source_id = str(source.get("id") or "")
        target_id = str(dependency.get("target_asset_id") or "")
        if source.get("entity_type") == "asset" and source_id and target_id:
            upstream_assets[target_id].add(source_id)
            downstream_assets[source_id].add(target_id)

    issues_by_asset: dict[str, list[AssetIssue]] = defaultdict(list)
    issue_keys_by_asset: dict[str, set[tuple[str, str, str | None, str | None]]] = defaultdict(set)
    for diagnostic in diagnostics:
        issue = _issue_from_diagnostic(diagnostic)
        asset_id = _string_or_none(diagnostic.get("asset_id"))
        if asset_id:
            _append_issue(issues_by_asset, issue_keys_by_asset, asset_id, issue)
            continue
        dataflow_id = _string_or_none(diagnostic.get("dataflow_id"))
        if dataflow_id:
            for related_asset_id in dataflow_asset_links.get(dataflow_id, set()):
                if related_asset_id:
                    _append_issue(issues_by_asset, issue_keys_by_asset, related_asset_id, issue)

    for reference in references:
        resolution = str(reference.get("resolution_status") or "")
        if resolution not in {"ambiguous", "unresolved"}:
            continue
        reference_id = _string_or_none(reference.get("id"))
        reference_message = str(reference.get("raw_value") or reference.get("display_name") or reference_id or "reference")
        reference_issue = AssetIssue(
            severity="warning" if resolution == "ambiguous" else "info",
            code=f"reference_{resolution}",
            message=f"{resolution.replace('_', ' ')} reference: {reference_message}",
            dataflow_id=_string_or_none(reference.get("dataflow_id")),
            reference_id=reference_id,
            details={"candidate_asset_ids": list(reference.get("candidate_asset_ids") or [])},
        )
        target_asset_id = _string_or_none(reference.get("target_asset_id"))
        if target_asset_id:
            _append_issue(issues_by_asset, issue_keys_by_asset, target_asset_id, reference_issue)
        for candidate_asset_id in reference.get("candidate_asset_ids") or []:
            candidate_id = _string_or_none(candidate_asset_id)
            if candidate_id:
                _append_issue(issues_by_asset, issue_keys_by_asset, candidate_id, reference_issue)

    asset_rows: list[dict[str, Any]] = []
    for asset in assets:
        asset_id = str(asset.get("id"))
        if not asset_id:
            continue
        metadata_source_ids = sorted({
            source_id
            for source_id in (_to_int(value) for value in (asset.get("metadata_source_ids") or []))
            if source_id is not None
        })
        metadata_sources = _metadata_sources_for_asset(asset, metadata_source_ids, metadata_source_uri_by_id)
        declaration_status = _declaration_status(asset, metadata_source_ids)
        issues = [item.to_dict() for item in issues_by_asset.get(asset_id, [])]
        issues.sort(key=lambda item: (_severity_rank(item["severity"]), item["code"], item["message"]))
        display_name = str(asset.get("display_name") or asset.get("label") or asset_id)
        connection_name = _string_or_none(asset.get("connection_name")) or _first_string(asset.get("connection_names") or [])
        kind = str(asset.get("kind") or "unresolved")
        format_value = _string_or_none(asset.get("format"))
        roles = sorted({str(role) for role in (asset.get("roles") or []) if str(role)})
        full_identity = _full_identity(asset, display_name, connection_name)
        asset_rows.append({
            "id": asset_id,
            "display_name": display_name,
            "friendly_name": _friendly_name(asset, display_name),
            "full_identity": full_identity,
            "kind": kind,
            "format": format_value,
            "connection_name": connection_name,
            "connection_type": _string_or_none(asset.get("connection_type")),
            "catalog": _string_or_none(asset.get("catalog")),
            "database": _string_or_none(asset.get("database")),
            "schema_name": _string_or_none(asset.get("schema_name")),
            "table": _string_or_none(asset.get("table")),
            "path": _string_or_none(asset.get("path")),
            "query": _string_or_none(asset.get("query")),
            "python_function": _string_or_none(asset.get("python_function")),
            "declaration_status": declaration_status,
            "roles": roles,
            "metadata_source_ids": metadata_source_ids,
            "metadata_sources": metadata_sources,
            "upstream_count": len(upstream_assets.get(asset_id, set())),
            "downstream_count": len(downstream_assets.get(asset_id, set())),
            "input_dataflow_count": int(input_dataflow_counts.get(asset_id, 0)),
            "output_dataflow_count": int(output_dataflow_counts.get(asset_id, 0)),
            "dependency_count": int(dependency_counts.get(asset_id, 0)),
            "issue_count": len(issues),
            "issues": issues,
            "identifiers": list(asset.get("identifiers") or []),
            "observations": list(asset.get("observations") or []),
        })

    asset_rows.sort(key=lambda item: (
        str(item.get("connection_name") or "").lower(),
        str(item.get("friendly_name") or "").lower(),
        item["id"],
    ))
    filter_options = _filter_options(asset_rows)
    summary = {
        "assets": len(asset_rows),
        "declared": sum(1 for item in asset_rows if item["declaration_status"] == "declared"),
        "discovered_only": sum(1 for item in asset_rows if item["declaration_status"] == "discovered_only"),
        "stitched": sum(1 for item in asset_rows if len(item["metadata_source_ids"]) > 1),
        "with_issues": sum(1 for item in asset_rows if item["issue_count"] > 0),
    }
    return {
        "summary": summary,
        "assets": asset_rows,
        "filter_options": filter_options,
        "diagnostics": diagnostics,
    }


def _metadata_source_uri_by_id(metadata: dict[str, Any], lineage: dict[str, Any]) -> dict[int, str]:
    values: dict[int, str] = {}
    for source in metadata.get("sources") or []:
        source_id = _to_int(source.get("id"))
        source_uri = _string_or_none(source.get("uri"))
        if source_id is not None and source_uri:
            values[source_id] = source_uri
    for asset in lineage.get("assets") or []:
        for observation in asset.get("observations") or []:
            source_id = _to_int(observation.get("metadata_source_id"))
            source_uri = _string_or_none(observation.get("metadata_source_uri"))
            if source_id is not None and source_uri and source_id not in values:
                values[source_id] = source_uri
    return values


def _filter_options(assets: list[dict[str, Any]]) -> dict[str, list[str]]:
    issues = {("with_issues" if item["issue_count"] > 0 else "clean") for item in assets}
    return {
        "connections": sorted({item["connection_name"] for item in assets if item["connection_name"]}),
        "formats": sorted({item["format"] for item in assets if item["format"]}),
        "kinds": sorted({item["kind"] for item in assets if item["kind"]}),
        "roles": sorted({role for item in assets for role in item["roles"]}),
        "declaration_statuses": sorted({item["declaration_status"] for item in assets}),
        "issue_states": sorted(issues),
    }


def _metadata_sources_for_asset(
    asset: dict[str, Any],
    metadata_source_ids: list[int],
    metadata_source_uri_by_id: dict[int, str],
) -> list[dict[str, Any]]:
    values: dict[int, str] = {}
    for source_id in metadata_source_ids:
        uri = metadata_source_uri_by_id.get(source_id)
        if uri:
            values[source_id] = uri
    for observation in asset.get("observations") or []:
        source_id = _to_int(observation.get("metadata_source_id"))
        source_uri = _string_or_none(observation.get("metadata_source_uri"))
        if source_id is not None and source_uri:
            values[source_id] = source_uri
    return [
        {"id": source_id, "uri": values[source_id]}
        for source_id in sorted(values)
    ]


def _append_issue(
    issues_by_asset: dict[str, list[AssetIssue]],
    issue_keys_by_asset: dict[str, set[tuple[str, str, str | None, str | None]]],
    asset_id: str,
    issue: AssetIssue,
) -> None:
    key = (issue.code, issue.message, issue.dataflow_id, issue.reference_id)
    if key in issue_keys_by_asset[asset_id]:
        return
    issue_keys_by_asset[asset_id].add(key)
    issues_by_asset[asset_id].append(issue)


def _issue_from_diagnostic(diagnostic: dict[str, Any]) -> AssetIssue:
    details = diagnostic.get("details")
    return AssetIssue(
        severity=_severity(str(diagnostic.get("severity") or "warning")),
        code=str(diagnostic.get("code") or "lineage_diagnostic"),
        message=str(diagnostic.get("message") or "Lineage diagnostic"),
        dataflow_id=_string_or_none(diagnostic.get("dataflow_id")),
        metadata_source_id=_to_int(diagnostic.get("metadata_source_id")),
        reference_id=_string_or_none((details or {}).get("reference_id") if isinstance(details, dict) else None),
        details=dict(details) if isinstance(details, dict) else {},
    )


def _declaration_status(asset: dict[str, Any], metadata_source_ids: list[int]) -> str:
    status = _string_or_none(asset.get("declaration_status"))
    if status in {"declared", "discovered_only"}:
        return status
    return "declared" if metadata_source_ids else "discovered_only"


def _qualified_table(asset: dict[str, Any]) -> str | None:
    table = _string_or_none(asset.get("table"))
    if not table:
        return None
    parts = [
        _string_or_none(asset.get("catalog")),
        _string_or_none(asset.get("database")),
        _string_or_none(asset.get("schema_name")),
        table,
    ]
    values = [value for value in parts if value]
    return ".".join(values) if values else None


def _full_identity(asset: dict[str, Any], display_name: str, connection_name: str | None) -> str:
    locator = _string_or_none(asset.get("path")) or _qualified_table(asset) or display_name
    connection = connection_name or "unknown connection"
    return " · ".join(part for part in (connection, locator) if part)


def _friendly_name(asset: dict[str, Any], display_name: str) -> str:
    python_function = _string_or_none(asset.get("python_function"))
    if python_function:
        return python_function.split(".")[-1] or python_function
    if _string_or_none(asset.get("query")):
        return "SQL query"
    table = _string_or_none(asset.get("table"))
    if table:
        return table
    path = _string_or_none(asset.get("path"))
    if path:
        normalized = path.replace("\\", "/").rstrip("/")
        if normalized and "/" in normalized:
            return normalized.rsplit("/", 1)[-1]
        return normalized or display_name
    return display_name


def _severity(value: str) -> str:
    return value if value in {"info", "warning", "error"} else "warning"


def _severity_rank(value: str) -> int:
    return {"error": 0, "warning": 1, "info": 2}.get(value, 3)


def _first_string(values: list[Any]) -> str | None:
    for value in values:
        text = _string_or_none(value)
        if text:
            return text
    return None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _to_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
