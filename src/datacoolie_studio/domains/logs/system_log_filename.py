"""Pure parsing and lookup normalization for producer system-log filenames."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote, unquote

from datacoolie_studio.domains.storage.uri import uri_basename


_FILENAME_RE = re.compile(
    r"^system_(?P<stamp>[0-9]{8}_[0-9]{6})_"
    r"(?P<job_num>[0-9]+)_(?P<job_index>[0-9]+)_(?P<token>.+)\.json$"
)
_PART_SUFFIX_RE = re.compile(r"^(?P<job>.+)_part_(?P<sequence>[0-9]{8,})$")
_TOKEN_SAFE = "-_.~"


@dataclass(frozen=True)
class ParsedSystemLogFilename:
    """Filename-derived candidates and the producer start timestamp."""

    job_id: str | None = None
    job_id_alt: str | None = None
    log_timestamp: datetime | None = None


def normalize_filename_job_id(job_id: object) -> str:
    """Normalize a requested identity to the producer's filename token value."""

    value = "" if job_id is None else str(job_id).strip()
    return value or "default"


def parse_system_log_filename(file_uri: str) -> ParsedSystemLogFilename:
    """Parse one producer filename without touching the referenced storage.

    The basename is extracted at the URI boundary exactly once.  A valid
    producer token is then decoded exactly once and checked against the
    canonical percent-encoding used by ``safe_filename_token``.  Unknown or
    noncanonical names return an all-``None`` result so selection can retain
    them as conservative on-demand candidates.
    """

    try:
        basename = uri_basename(str(file_uri))
    except (TypeError, ValueError, OSError):
        return ParsedSystemLogFilename()
    match = _FILENAME_RE.fullmatch(basename)
    if match is None:
        return ParsedSystemLogFilename()

    try:
        job_num = int(match.group("job_num"), 10)
        job_index = int(match.group("job_index"), 10)
        if job_num < 1 or job_index < 0 or job_index >= job_num:
            return ParsedSystemLogFilename()
        timestamp = datetime.strptime(
            match.group("stamp"), "%Y%m%d_%H%M%S"
        ).replace(tzinfo=timezone.utc)
        token = match.group("token")
        decoded, job_id_alt = _decode_filename_token(token)
        if decoded is None:
            return ParsedSystemLogFilename()
    except (TypeError, ValueError, UnicodeError):
        return ParsedSystemLogFilename()

    return ParsedSystemLogFilename(
        job_id=decoded,
        job_id_alt=job_id_alt,
        log_timestamp=timestamp,
    )


def _decode_filename_token(token: str) -> tuple[str | None, str | None]:
    """Decode a complete token, preserving the producer's part ambiguity."""

    suffix = _PART_SUFFIX_RE.fullmatch(token)
    if suffix is not None and _valid_part_sequence(suffix.group("sequence")):
        base = _decode_canonical_token(suffix.group("job"))
        if base is not None:
            sequence = suffix.group("sequence")
            return f"{base}_part_{sequence}", base
    decoded = _decode_canonical_token(token)
    return decoded, None


def _decode_canonical_token(token: str) -> str | None:
    """Decode one filename token only when it is producer-canonical."""

    try:
        decoded = unquote(token, encoding="utf-8", errors="strict")
    except (UnicodeDecodeError, ValueError):
        return None
    if _canonical_token(decoded) != token:
        return None
    return decoded


def _valid_part_sequence(sequence: str) -> bool:
    """Accept the producer's minimum-width positive decimal sequence."""

    return len(sequence) >= 8 and sequence.isdecimal() and sequence.lstrip("0") != ""


def _canonical_token(value: str) -> str | None:
    """Return the producer token for a normalized raw identity."""

    raw = str(value).strip()
    if not raw:
        return "default"
    token = quote(raw, safe=_TOKEN_SAFE, encoding="utf-8", errors="strict")
    if raw.startswith(".") and token.startswith("."):
        token = "%2E" + token[1:]
    if raw.endswith(".") and token.endswith("."):
        token = token[:-1] + "%2E"
    return token or "default"
