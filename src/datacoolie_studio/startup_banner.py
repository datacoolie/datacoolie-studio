"""Formatting and terminal detection for the Studio command-line introduction."""

from __future__ import annotations

import shutil
from typing import TextIO


DATACOOLIE_REPOSITORY_URL = "https://github.com/datacoolie/datacoolie"
DATACOOLIE_STUDIO_REPOSITORY_URL = "https://github.com/datacoolie/datacoolie-studio"

DATACOOLIE_WORDMARK = r"""
 ____        _         ____            _ _
|  _ \  __ _| |_ __ _ / ___|___   ___ | (_) ___
| | | |/ _` | __/ _` | |   / _ \ / _ \| | |/ _ \
| |_| | (_| | || (_| | |__| (_) | (_) | | |  __/
|____/ \__,_|\__\__,_|\____\___/ \___/|_|_|\___|
""".strip("\n")
WORDMARK_WIDTH = max(len(line) for line in DATACOOLIE_WORDMARK.splitlines())

_STUDIO_DESCRIPTION = "Explore DataCoolie metadata, lineage, and ETL logs."


def build_application_url(host: str, port: int) -> str | None:
    """Build the URL users can use to reach a bound Studio process.

    Wildcard bind addresses are replaced with loopback addresses because they
    describe a bind interface, not a useful browser destination. Port zero is
    left to the server because its final value is not known before binding.
    """

    if port == 0:
        return None

    display_host = host.strip()
    if display_host == "0.0.0.0":
        display_host = "127.0.0.1"
    elif display_host == "::":
        display_host = "::1"

    if display_host.startswith("[") and display_host.endswith("]"):
        display_host = display_host[1:-1]
    if ":" in display_host:
        display_host = f"[{display_host}]"

    return f"http://{display_host}:{port}"


def terminal_supports_wordmark(
    stream: TextIO,
    *,
    terminal_width: int | None = None,
) -> bool:
    """Return whether ``stream`` can display the full wordmark safely."""

    try:
        if not stream.isatty():
            return False
    except (AttributeError, OSError, ValueError):
        return False

    if terminal_width is None:
        try:
            terminal_width = shutil.get_terminal_size(fallback=(0, 0)).columns
        except (OSError, ValueError):
            return False

    return terminal_width >= WORDMARK_WIDTH


def format_startup_banner(
    version: str,
    application_url: str | None,
    *,
    use_wordmark: bool,
) -> str:
    """Format the introduction printed before the server starts."""

    if use_wordmark:
        heading = f"{DATACOOLIE_WORDMARK}\n\nStudio v{version}"
    else:
        heading = f"DataCoolie Studio v{version}"

    repositories = "\n".join(
        (
            "DataCoolie - Metadata-driven ETL framework",
            f"  {DATACOOLIE_REPOSITORY_URL}",
            "DataCoolie Studio - Local web app for DataCoolie projects",
            f"  {DATACOOLIE_STUDIO_REPOSITORY_URL}",
        )
    )
    endpoint = (
        f"Starting at: {application_url}"
        if application_url is not None
        else "Starting at: an automatically assigned port (see server logs)."
    )

    blocks = (
        f"{heading}\n{_STUDIO_DESCRIPTION}",
        repositories,
        endpoint,
        "Press Ctrl+C to stop.",
    )
    return "\n\n".join(blocks)
