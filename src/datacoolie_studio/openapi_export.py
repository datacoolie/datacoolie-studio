from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any


DEFAULT_OUTPUT = Path("openapi/openapi.json")


def normalized_openapi() -> str:
    """Return the application OpenAPI document with deterministic ordering."""
    with tempfile.TemporaryDirectory(prefix="datacoolie-openapi-") as directory:
        root = Path(directory)
        previous = {
            name: os.environ.get(name)
            for name in (
                "DATACOOLIE_STUDIO_DB",
                "DATACOOLIE_STUDIO_RESULT_CACHE_URL",
            )
        }
        os.environ["DATACOOLIE_STUDIO_DB"] = str(root / "studio.db")
        os.environ["DATACOOLIE_STUDIO_RESULT_CACHE_URL"] = "memory://"
        try:
            from datacoolie_studio.main import app

            document: dict[str, Any] = app.openapi()
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def export_openapi(output: Path = DEFAULT_OUTPUT, *, check: bool = False) -> bool:
    rendered = normalized_openapi()
    if check:
        return output.exists() and output.read_text(encoding="utf-8") == rendered
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8", newline="\n")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Export deterministic DataCoolie Studio OpenAPI")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if export_openapi(args.output, check=args.check):
        return 0
    print(f"OpenAPI artifact is stale: {args.output}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
