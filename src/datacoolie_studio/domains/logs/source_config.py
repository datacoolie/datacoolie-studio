"""Resolve the DataCoolie v3 log tree for one Studio log source."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from datacoolie_studio.db.models import EnvironmentSource
from datacoolie_studio.domains.storage.uri import join_uri, parse_storage_uri, uri_basename


@dataclass(frozen=True)
class LogSourcePaths:
    mode: str
    log_base_path: str | None
    execution_logs_uri: str | None
    system_logs_uri: str | None


def log_source_config(source: EnvironmentSource) -> dict[str, Any]:
    source_config_json = getattr(source, "source_config_json", None)
    if not source_config_json:
        return {}
    try:
        value = json.loads(source_config_json)
    except (json.JSONDecodeError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def resolve_log_source_paths(source: EnvironmentSource) -> LogSourcePaths:
    config = log_source_config(source)
    mode = str(config.get("mode") or "").strip()
    log_base_path = _clean_uri(config.get("log_base_path"))
    execution_uri = _clean_uri(config.get("execution_logs_uri"))
    system_uri = _clean_uri(config.get("system_logs_uri"))

    if mode == "separate_paths" or execution_uri or system_uri:
        base = log_base_path
        if base is None:
            anchor = execution_uri or system_uri or source.uri
            base = _infer_from_uri(anchor)["log_base_path"]
        inferred = _infer_from_uri(base or source.uri)
        return LogSourcePaths(
            mode="separate_paths",
            log_base_path=base,
            execution_logs_uri=execution_uri or inferred["execution_logs_uri"],
            system_logs_uri=system_uri or inferred["system_logs_uri"],
        )

    inferred = _infer_from_uri(log_base_path or source.uri)
    return LogSourcePaths(
        mode="log_base_path",
        log_base_path=inferred["log_base_path"],
        execution_logs_uri=inferred["execution_logs_uri"],
        system_logs_uri=inferred["system_logs_uri"],
    )


def normalized_log_source_config(source: EnvironmentSource) -> dict[str, Any]:
    paths = resolve_log_source_paths(source)
    return {
        "mode": paths.mode,
        "log_base_path": paths.log_base_path,
        "execution_logs_uri": paths.execution_logs_uri,
        "system_logs_uri": paths.system_logs_uri,
    }


def _clean_uri(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _infer_from_uri(uri: str) -> dict[str, str | None]:
    """Infer the v3 base and category roots from a configured URI."""

    parsed = parse_storage_uri(uri)
    known_names = {"execution_logs", "system_logs", "job_run_log", "dataflow_run_log"}
    if parsed.is_local:
        path = parsed.local_path or Path(uri).expanduser()
        if path.name in known_names:
            base_path = path
            for _ in range(
                2 if path.name in {"job_run_log", "dataflow_run_log"} else 1
            ):
                base_path = base_path.parent
            return {
                "log_base_path": base_path.as_posix(),
                "execution_logs_uri": (base_path / "execution_logs").as_posix(),
                "system_logs_uri": (base_path / "system_logs").as_posix(),
            }
        return {
            "log_base_path": path.as_posix(),
            "execution_logs_uri": (path / "execution_logs").as_posix(),
            "system_logs_uri": (path / "system_logs").as_posix(),
        }

    name = uri_basename(uri)
    if name in known_names:
        base = uri.rstrip("/\\")
        for _ in range(2 if name in {"job_run_log", "dataflow_run_log"} else 1):
            base = base.rsplit("/", 1)[0]
        return {
            "log_base_path": base,
            "execution_logs_uri": join_uri(base, "execution_logs"),
            "system_logs_uri": join_uri(base, "system_logs"),
        }
    return {
        "log_base_path": uri,
        "execution_logs_uri": join_uri(uri, "execution_logs"),
        "system_logs_uri": join_uri(uri, "system_logs"),
    }
