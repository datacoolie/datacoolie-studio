"""Audit log artifact schemas and optionally apply lossless repairs."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from datacoolie_studio.domains.analytics.log_schema_audit import audit_log_root
from datacoolie_studio.domains.analytics.log_schema_repair import apply_repairs


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="log root to audit")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="acknowledge the write operation and rewrite only validated lossless drift",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        help="external backup directory used with --apply",
    )
    parser.add_argument("--output", type=Path, help="also write the JSON report here")
    return parser


def _default_backup_dir(root: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return root.parent / f"{root.name}.schema-repair-{timestamp}"


def _render_report(report: dict[str, object], output: Path | None) -> None:
    rendered = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    print(rendered)
    if output is not None:
        output.write_text(rendered + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        root = args.root.expanduser().resolve()
        report = dict(audit_log_root(root))
        if args.apply:
            backup_dir = (args.backup_dir or _default_backup_dir(root)).expanduser().resolve()
            report["repair_mode"] = "apply"
            report["backup_dir"] = str(backup_dir)
            report["applied"] = apply_repairs(report, backup_dir=backup_dir)
            report["post_apply"] = audit_log_root(root)
        else:
            report["repair_mode"] = "dry-run"
        _render_report(report, args.output)
    except Exception as exc:  # noqa: BLE001 - CLI boundary maps operational errors to a receipt
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 2 if report["incompatible_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
