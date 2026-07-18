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
    base_log_uri: str | None
    etl_logs_uri: str | None
    system_logs_uri: str | None


def log_source_config(source: EnvironmentSource) -> dict[str, Any]:
    source_config_json = getattr(source, "source_config_json", None)
    if not source_config_json:
        return {}
    try:
        value = json.loads(source_config_json)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def resolve_log_source_paths(source: EnvironmentSource) -> LogSourcePaths:
    config = log_source_config(source)
    mode = str(config.get("mode") or config.get("log_source_mode") or "").strip()
    base_log_uri = _clean_uri(config.get("base_log_uri") or config.get("base_log_path"))
    etl_logs_uri = _clean_uri(config.get("etl_logs_uri") or config.get("etl_log_uri") or config.get("etl_logs_path"))
    system_logs_uri = _clean_uri(config.get("system_logs_uri") or config.get("system_log_uri") or config.get("system_logs_path"))

    if mode == "separate_paths" or etl_logs_uri or system_logs_uri:
        return LogSourcePaths(
            mode="separate_paths",
            base_log_uri=base_log_uri,
            etl_logs_uri=_analyst_etl_uri(etl_logs_uri or source.uri),
            system_logs_uri=system_logs_uri,
        )

    base = base_log_uri or source.uri
    inferred = _infer_from_uri(base)
    return LogSourcePaths(
        mode="base_log_path",
        base_log_uri=inferred["base_log_uri"],
        etl_logs_uri=inferred["etl_logs_uri"],
        system_logs_uri=inferred["system_logs_uri"],
    )


def normalized_log_source_config(source: EnvironmentSource) -> dict[str, Any]:
    paths = resolve_log_source_paths(source)
    return {
        "mode": paths.mode,
        "base_log_uri": paths.base_log_uri,
        "etl_logs_uri": paths.etl_logs_uri,
        "system_logs_uri": paths.system_logs_uri,
    }


def _clean_uri(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _infer_from_uri(uri: str) -> dict[str, str | None]:
    parsed = parse_storage_uri(uri)
    if not parsed.is_local:
        name = uri_basename(uri)
        if name == "etl_logs":
            base = uri.rstrip("/").rsplit("/", 1)[0]
            return {
                "base_log_uri": base,
                "etl_logs_uri": join_uri(uri, "analyst"),
                "system_logs_uri": join_uri(base, "system_logs"),
            }
        if name == "system_logs":
            base = uri.rstrip("/").rsplit("/", 1)[0]
            return {
                "base_log_uri": base,
                "etl_logs_uri": join_uri(base, "etl_logs", "analyst"),
                "system_logs_uri": uri,
            }
        return {
            "base_log_uri": uri,
            "etl_logs_uri": join_uri(uri, "etl_logs", "analyst"),
            "system_logs_uri": join_uri(uri, "system_logs"),
        }

    path = parsed.local_path or Path(uri).expanduser()
    if path.name == "etl_logs":
        base = path.parent
        return {
            "base_log_uri": base.as_posix(),
            "etl_logs_uri": (path / "analyst").as_posix(),
            "system_logs_uri": (base / "system_logs").as_posix(),
        }
    if path.name == "system_logs":
        base = path.parent
        return {
            "base_log_uri": base.as_posix(),
            "etl_logs_uri": (base / "etl_logs" / "analyst").as_posix(),
            "system_logs_uri": path.as_posix(),
        }
    if _looks_like_local_base_log_root(path):
        return {
            "base_log_uri": path.as_posix(),
            "etl_logs_uri": (path / "etl_logs" / "analyst").as_posix(),
            "system_logs_uri": (path / "system_logs").as_posix(),
        }
    if _looks_like_local_etl_log_root(path):
        base, system = _local_base_and_system_for_etl(path)
        return {
            "base_log_uri": base.as_posix(),
            "etl_logs_uri": path.as_posix(),
            "system_logs_uri": system.as_posix(),
        }
    if _looks_like_local_system_log_root(path):
        base, etl = _local_base_and_etl_for_system(path)
        return {
            "base_log_uri": base.as_posix(),
            "etl_logs_uri": etl.as_posix(),
            "system_logs_uri": path.as_posix(),
        }
    return {
        "base_log_uri": path.as_posix(),
        "etl_logs_uri": (path / "etl_logs" / "analyst").as_posix(),
        "system_logs_uri": (path / "system_logs").as_posix(),
    }


def _analyst_etl_uri(uri: str) -> str:
    name = uri_basename(uri)
    return join_uri(uri, "analyst") if name == "etl_logs" else uri


def _looks_like_local_base_log_root(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    return (path / "etl_logs").is_dir() or (path / "system_logs").is_dir()


def _looks_like_local_etl_log_root(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    return (path / "dataflow_run_log").exists() or (path / "job_run_log").exists()


def _looks_like_local_system_log_root(path: Path) -> bool:
    if not path.exists() or not path.is_dir():
        return False
    return any(path.glob("system_log_*.jsonl")) or (path / "system_logs").exists()


def _local_base_and_system_for_etl(path: Path) -> tuple[Path, Path]:
    if path.parent.name == "etl_logs":
        base = path.parent.parent
        return base, base / "system_logs" / path.name
    base = path.parent
    return base, base / "system_logs"


def _local_base_and_etl_for_system(path: Path) -> tuple[Path, Path]:
    if path.parent.name == "system_logs":
        base = path.parent.parent
        return base, base / "etl_logs" / path.name
    base = path.parent
    return base, base / "etl_logs" / "analyst"
