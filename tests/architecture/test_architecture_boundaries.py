from __future__ import annotations

import ast
from pathlib import Path
import re

from tests.support.paths import REPOSITORY_ROOT


DOMAINS_ROOT = REPOSITORY_ROOT / "src" / "datacoolie_studio" / "domains"
API_ROOT = REPOSITORY_ROOT / "src" / "datacoolie_studio" / "api" / "v1" / "routes"
DOMAIN_PREFIX = ("datacoolie_studio", "domains")
FRONTEND_ROOT = REPOSITORY_ROOT / "frontend" / "src"
FRONTEND_SHARED_ROOT = FRONTEND_ROOT / "shared"
MONITORING_METRICS_ROOT = DOMAINS_ROOT / "monitoring" / "metrics"
ASSETS_ROOT = DOMAINS_ROOT / "assets"
WORKSPACE_ROOT = DOMAINS_ROOT / "workspace"

# These are characterization fences for the remaining route-boundary debt. The
# owning route/application slices may remove entries, but a new direct model
# dependency must not appear silently.
ROUTE_MODEL_IMPORT_ALLOWLIST = {
    "sql.py": {"EnvironmentSource"},
    "sources.py": {"Environment"},
}


def test_analytics_does_not_import_logs_or_monitoring() -> None:
    edges = _domain_import_edges("analytics")

    assert not (edges & {("analytics", "logs"), ("analytics", "monitoring")})


def test_low_level_analytics_paths_do_not_import_upgrade_facade() -> None:
    source_files = [
        *(DOMAINS_ROOT / "analytics").rglob("*.py"),
        DOMAINS_ROOT / "logs" / "ingestion.py",
        DOMAINS_ROOT / "sources" / "operations.py",
    ]
    violations: list[str] = []
    for source_file in source_files:
        tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
        violations.extend(
            f"{source_file.relative_to(REPOSITORY_ROOT)} -> {module}"
            for module in _imported_modules(tree)
            if module == "datacoolie_studio.domains.analytics_upgrade"
            or module.startswith("datacoolie_studio.domains.analytics_upgrade.")
        )
    assert not violations, (
        "Low-level analytics and source refresh paths must use the neutral "
        "analytics ownership policy, not the upgrade application facade:\n"
        + "\n".join(violations)
    )


def test_logs_does_not_import_monitoring() -> None:
    edges = _domain_import_edges("logs")

    assert ("logs", "monitoring") not in edges


def test_monitoring_does_not_import_logs() -> None:
    edges = _domain_import_edges("monitoring")

    assert ("monitoring", "logs") not in edges


def test_sources_does_not_import_workspace_or_higher_level_services() -> None:
    edges = _domain_import_edges("sources")

    assert ("sources", "workspace") not in edges


def test_assets_and_overview_use_narrow_read_contracts() -> None:
    read_model_files = [
        DOMAINS_ROOT / "assets" / "service.py",
        DOMAINS_ROOT / "overview" / "service.py",
    ]
    violations: list[str] = []
    for source_file in read_model_files:
        tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
        for module in _imported_modules(tree):
            if module in {
                "datacoolie_studio.domains.workspace",
                "datacoolie_studio.domains.workspace.service",
                "datacoolie_studio.domains.lineage.service",
            }:
                violations.append(f"{source_file.relative_to(REPOSITORY_ROOT)} -> {module}")
    assert not violations, "Read-model domains must use repository/query contracts:\n" + "\n".join(violations)


def test_assets_service_is_a_thin_application_facade() -> None:
    service_file = ASSETS_ROOT / "service.py"
    tree = ast.parse(service_file.read_text(encoding="utf-8"), filename=str(service_file))
    function_names = {
        node.name
        for node in ast.iter_child_nodes(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert function_names == {
        "get_environment_asset",
        "get_environment_asset_reference",
        "get_environment_asset_source",
        "get_reference_occurrence_source",
        "list_environment_asset_references",
        "list_environment_assets",
        "list_project_reference_registry",
    }
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert {
        "datacoolie_studio.domains.assets.catalog",
        "datacoolie_studio.domains.assets.definitions",
        "datacoolie_studio.domains.assets.detail",
        "datacoolie_studio.domains.assets.filtering",
        "datacoolie_studio.domains.assets.inventory",
        "datacoolie_studio.domains.assets.serialization",
    } <= imported_modules


def test_assets_collaborators_do_not_import_the_application_facade() -> None:
    violations: list[str] = []
    for source_file in ASSETS_ROOT.glob("*.py"):
        if source_file.name == "service.py":
            continue
        tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
        for module in _imported_modules(tree):
            if module == "datacoolie_studio.domains.assets.service":
                violations.append(str(source_file.relative_to(REPOSITORY_ROOT)))
    assert not violations, "Assets collaborators must not depend on the application facade:\n" + "\n".join(violations)


def test_workspace_service_is_a_thin_lifecycle_facade() -> None:
    service_file = WORKSPACE_ROOT / "service.py"
    tree = ast.parse(service_file.read_text(encoding="utf-8"), filename=str(service_file))
    function_names = {
        node.name
        for node in ast.iter_child_nodes(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert function_names == {"delete_environment", "delete_project"}
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert {
        "datacoolie_studio.domains.workspace.derived_cleanup",
        "datacoolie_studio.domains.workspace.lifecycle",
        "datacoolie_studio.domains.workspace.sources_query",
    } <= imported_modules


def test_workspace_collaborators_do_not_import_the_application_facade() -> None:
    violations: list[str] = []
    for source_file in WORKSPACE_ROOT.glob("*.py"):
        if source_file.name == "service.py":
            continue
        tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
        if "datacoolie_studio.domains.workspace.service" in _imported_modules(tree):
            violations.append(str(source_file.relative_to(REPOSITORY_ROOT)))
    assert not violations, "Workspace collaborators must not depend on the facade:\n" + "\n".join(violations)


def test_workspace_source_queries_do_not_retain_misleading_validation_names() -> None:
    source_file = WORKSPACE_ROOT / "sources_query.py"
    assert "with_validation" not in source_file.read_text(encoding="utf-8")


def test_analytics_upgrade_uses_state_and_publication_contracts() -> None:
    source_file = DOMAINS_ROOT / "analytics_upgrade" / "orchestration.py"
    tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
    imported_modules = _imported_modules(tree)
    assert "datacoolie_studio.domains.analytics" in imported_modules
    imported_symbols = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "datacoolie_studio.domains.analytics"
        for alias in node.names
    }
    assert {"cache_state", "publication"} <= imported_symbols
    state_or_publication_names = {
        "analytics_meta",
        "candidate_is_resumable",
        "cache_source_ids",
        "candidate_source_ids",
        "cache_source_generations",
        "finalize_candidate",
        "validate_analytics_candidate",
    }
    violations = [
        f"{source_file.relative_to(REPOSITORY_ROOT)} -> store.{node.attr}"
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "store"
            and node.attr in state_or_publication_names
        )
    ]
    assert not violations, "Analytics upgrade must use narrow state/publication contracts:\n" + "\n".join(violations)


def test_analytics_upgrade_service_is_a_facade_over_lifecycle_boundaries() -> None:
    upgrade_root = DOMAINS_ROOT / "analytics_upgrade"
    service_file = upgrade_root / "service.py"
    tree = ast.parse(service_file.read_text(encoding="utf-8"), filename=str(service_file))
    function_names = {
        node.name
        for node in ast.iter_child_nodes(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert function_names == {
        "_build_sources_parallel",
        "analytics_upgrade_loop",
        "run_analytics_upgrade_once",
    }
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert {
        "datacoolie_studio.domains.analytics_upgrade.orchestration",
        "datacoolie_studio.domains.analytics_upgrade.source_replay",
        "datacoolie_studio.domains.analytics_upgrade.status",
    } <= imported_modules


def test_analytics_upgrade_collaborators_do_not_import_the_facade() -> None:
    upgrade_root = DOMAINS_ROOT / "analytics_upgrade"
    violations: list[str] = []
    for source_file in upgrade_root.glob("*.py"):
        if source_file.name == "service.py":
            continue
        tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
        if "datacoolie_studio.domains.analytics_upgrade.service" in _imported_modules(tree):
            violations.append(str(source_file.relative_to(REPOSITORY_ROOT)))
    assert not violations, "Analytics-upgrade collaborators must not depend on the facade:\n" + "\n".join(violations)


def test_log_ingestion_delegates_refresh_boundaries() -> None:
    source_file = DOMAINS_ROOT / "logs" / "ingestion.py"
    tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
    imported_symbols = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "datacoolie_studio.domains.logs"
        for alias in node.names
    }
    assert {
        "control_state",
        "file_state",
        "refresh_planning",
        "source_snapshot",
        "staging",
        "upgrade_staging",
    } <= imported_symbols
    extracted_function_names = {
        "merge_rebuild_candidates",
        "planner_state_from_row",
        "stream_state_update",
        "upsert_manifest",
        "deferred_control_payload",
        "create_staging_dir",
        "materialize_log_uri",
        "stage_upgrade_log_batches",
    }
    violations = [
        f"{source_file.relative_to(REPOSITORY_ROOT)} -> {node.name}"
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in extracted_function_names
    ]
    assert not violations, "Log refresh orchestration must delegate extracted boundaries:\n" + "\n".join(violations)


def test_monitoring_page_service_delegates_page_boundaries() -> None:
    source_file = DOMAINS_ROOT / "monitoring" / "page_service.py"
    tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert {
        "datacoolie_studio.domains.monitoring.page_cache",
        "datacoolie_studio.domains.monitoring.page_dispatch",
        "datacoolie_studio.domains.monitoring.page_evidence",
        "datacoolie_studio.domains.monitoring.page_projection",
    } <= imported_modules
    assert not [
        node.name
        for node in ast.iter_child_nodes(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ], "Monitoring page service must remain a facade over page boundaries"


def test_monitoring_metrics_use_public_cross_module_contracts() -> None:
    violations: list[str] = []
    for source_file in MONITORING_METRICS_ROOT.glob("*.py"):
        tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if not node.module.startswith("datacoolie_studio.domains.monitoring.metrics"):
                continue
            violations.extend(
                f"{source_file.relative_to(REPOSITORY_ROOT)} -> {node.module}.{alias.name}"
                for alias in node.names
                if alias.name.startswith("_")
            )
    assert not violations, "Monitoring metrics must not import private cross-module symbols:\n" + "\n".join(violations)
    assert not (MONITORING_METRICS_ROOT / "common_projections.py").exists()


def test_source_routes_do_not_own_workspace_or_transactions() -> None:
    source_route = API_ROOT / "sources.py"
    tree = ast.parse(source_route.read_text(encoding="utf-8"), filename=str(source_route))
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "datacoolie_studio.domains.workspace" not in imported_modules
    assert "datacoolie_studio.domains.workspace.service" not in imported_modules
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "commit"
        for node in ast.walk(tree)
    )


def test_routes_do_not_import_shared_dependencies_from_a_sibling_route() -> None:
    violations: list[str] = []
    for route_file in API_ROOT.glob("*.py"):
        if route_file.name == "credentials.py":
            continue
        tree = ast.parse(route_file.read_text(encoding="utf-8"), filename=str(route_file))
        if "datacoolie_studio.api.v1.routes.credentials" in _imported_modules(tree):
            violations.append(str(route_file.relative_to(REPOSITORY_ROOT)))
    assert not violations, "Shared API dependencies must live outside sibling route modules:\n" + "\n".join(violations)


def test_credential_route_delegates_shared_dependencies() -> None:
    source_file = API_ROOT / "credentials.py"
    tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
    function_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "get_credential_secret_store" not in function_names
    assert "require_loopback_client" not in function_names
    assert "datacoolie_studio.api.v1.dependencies" in _imported_modules(tree)


def test_domains_do_not_import_api_or_route_modules() -> None:
    violations: list[str] = []
    for source_file in DOMAINS_ROOT.rglob("*.py"):
        tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
        for module in _imported_modules(tree):
            if module.startswith("datacoolie_studio.api"):
                violations.append(f"{source_file.relative_to(REPOSITORY_ROOT)} -> {module}")
    assert not violations, "Domain code must not depend on the HTTP/API layer:\n" + "\n".join(violations)


def test_metadata_document_modules_do_not_import_persistence() -> None:
    forbidden = {
        "datacoolie_studio.db",
        "datacoolie_studio.domains.metadata.editor",
        "datacoolie_studio.domains.metadata.storage_io",
    }
    violations: list[str] = []
    for source_file in (DOMAINS_ROOT / "metadata").glob("document_*.py"):
        tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
        for module in _imported_modules(tree):
            if module in forbidden or any(module.startswith(f"{prefix}.") for prefix in forbidden):
                violations.append(f"{source_file.relative_to(REPOSITORY_ROOT)} -> {module}")
    assert not violations, "Pure metadata document modules must not depend on persistence:\n" + "\n".join(violations)


def test_api_routes_do_not_commit_directly() -> None:
    violations: list[str] = []
    for route_file in API_ROOT.glob("*.py"):
        tree = ast.parse(route_file.read_text(encoding="utf-8"), filename=str(route_file))
        if any(
            isinstance(node, ast.Attribute) and node.attr == "commit"
            for node in ast.walk(tree)
        ):
            violations.append(str(route_file.relative_to(REPOSITORY_ROOT)))
    assert not violations, "API routes must delegate transaction ownership:\n" + "\n".join(violations)


def test_api_routes_do_not_import_private_domain_symbols() -> None:
    violations: list[str] = []
    for route_file in API_ROOT.glob("*.py"):
        tree = ast.parse(route_file.read_text(encoding="utf-8"), filename=str(route_file))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if not node.module.startswith("datacoolie_studio.domains"):
                continue
            private_names = [alias.name for alias in node.names if alias.name.startswith("_")]
            violations.extend(
                f"{route_file.relative_to(REPOSITORY_ROOT)} -> {node.module}.{name}"
                for name in private_names
            )
    assert not violations, "API routes must use public domain contracts:\n" + "\n".join(violations)


def test_metadata_consumers_do_not_import_private_metadata_symbols() -> None:
    consumer_files = [
        DOMAINS_ROOT / "metadata" / "service.py",
        DOMAINS_ROOT / "metadata" / "backup_application.py",
        DOMAINS_ROOT / "lineage" / "service.py",
        API_ROOT / "metadata.py",
    ]
    violations: list[str] = []
    for source_file in consumer_files:
        tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
        for node in ast.walk(tree):
            if (
                not isinstance(node, ast.ImportFrom)
                or not node.module
                or not node.module.startswith("datacoolie_studio.domains.metadata")
            ):
                continue
            violations.extend(
                f"{source_file.relative_to(REPOSITORY_ROOT)} -> {node.module}.{alias.name}"
                for alias in node.names
                if alias.name.startswith("_")
            )
    assert not violations, "Metadata consumers must use public contracts:\n" + "\n".join(violations)


def test_metadata_consumers_do_not_use_local_domain_imports() -> None:
    consumer_files = [
        DOMAINS_ROOT / "metadata" / "service.py",
        DOMAINS_ROOT / "metadata" / "backup_application.py",
        DOMAINS_ROOT / "metadata" / "sql_writes.py",
        DOMAINS_ROOT / "lineage" / "service.py",
    ]
    violations: list[str] = []
    for source_file in consumer_files:
        tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)) or node.col_offset == 0:
                continue
            modules = (
                [node.module]
                if isinstance(node, ast.ImportFrom)
                else [alias.name for alias in node.names]
            )
            violations.extend(
                f"{source_file.relative_to(REPOSITORY_ROOT)} -> {module}"
                for module in modules
                if module and module.startswith("datacoolie_studio.domains")
            )
    assert not violations, (
        "Metadata and lineage consumers must resolve domain dependencies at module scope:\n"
        + "\n".join(violations)
    )


def test_api_route_model_access_does_not_expand() -> None:
    actual: dict[str, set[str]] = {}
    for route_file in API_ROOT.glob("*.py"):
        tree = ast.parse(route_file.read_text(encoding="utf-8"), filename=str(route_file))
        names = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "datacoolie_studio.db.models"
            for alias in node.names
        }
        if names:
            actual[route_file.name] = names
    assert actual == {
        name: names
        for name, names in ROUTE_MODEL_IMPORT_ALLOWLIST.items()
        if names
    }


def test_frontend_shared_code_does_not_import_features() -> None:
    feature_root = FRONTEND_ROOT / "features"
    violations: list[str] = []
    for source_file in FRONTEND_SHARED_ROOT.rglob("*.ts"):
        violations.extend(_frontend_feature_imports(source_file, feature_root))
    for source_file in FRONTEND_SHARED_ROOT.rglob("*.tsx"):
        violations.extend(_frontend_feature_imports(source_file, feature_root))
    assert not violations, "Shared frontend code must not depend on feature modules:\n" + "\n".join(violations)


def test_workspace_service_does_not_expose_source_mutation_use_cases() -> None:
    workspace_service = DOMAINS_ROOT / "workspace" / "service.py"
    tree = ast.parse(
        workspace_service.read_text(encoding="utf-8"),
        filename=str(workspace_service),
    )
    source_mutation_names = {
        "update_code_artifact",
        "delete_code_artifact",
        "code_artifact_delete_impact",
        "update_metadata_source",
        "delete_metadata_source",
        "metadata_source_delete_impact",
        "update_sql_source",
        "delete_sql_source",
        "sql_source_delete_impact",
        "update_log_source",
        "delete_log_source",
        "log_source_delete_impact",
    }
    assert not {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    } & source_mutation_names


def _domain_import_edges(source_domain: str) -> set[tuple[str, str]]:
    edges: set[tuple[str, str]] = set()
    for source_file in (DOMAINS_ROOT / source_domain).rglob("*.py"):
        tree = ast.parse(source_file.read_text(encoding="utf-8"), filename=str(source_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                target = _imported_domain(node.module)
                if target:
                    edges.add((source_domain, target))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    target = _imported_domain(alias.name)
                    if target:
                        edges.add((source_domain, target))
    return edges


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def _frontend_feature_imports(source_file: Path, feature_root: Path) -> list[str]:
    violations: list[str] = []
    source_text = source_file.read_text(encoding="utf-8")
    for specifier in re.findall(r"(?:from|import)\s*[\"']([^\"']+)[\"']", source_text):
        if not specifier.startswith("."):
            continue
        target = (source_file.parent / specifier).resolve()
        try:
            target.relative_to(feature_root.resolve())
        except ValueError:
            continue
        violations.append(
            f"{source_file.relative_to(REPOSITORY_ROOT)} -> {specifier}"
        )
    return violations


def _imported_domain(module: str) -> str | None:
    parts = tuple(module.split("."))
    if len(parts) < 3 or parts[:2] != DOMAIN_PREFIX:
        return None
    return parts[2]
