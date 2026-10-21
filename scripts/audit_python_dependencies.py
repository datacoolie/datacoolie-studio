"""Audit the exact Python dependencies selected by Studio's uv.lock."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="studio-dependency-audit-") as directory:
        requirements = Path(directory) / "requirements.txt"
        subprocess.run(
            [
                sys.executable, "-m", "uv", "export", "--locked", "--all-extras",
                "--no-emit-project", "--python", sys.executable,
                "--output-file", str(requirements),
            ],
            cwd=root,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                sys.executable, "-m", "pip_audit", "--requirement", str(requirements),
                "--require-hashes", "--disable-pip", "--strict",
                "--progress-spinner", "off",
            ],
            cwd=root,
            check=True,
        )


if __name__ == "__main__":
    main()
