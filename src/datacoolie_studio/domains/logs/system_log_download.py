"""Prepare bounded, byte preserving downloads of original system log lines."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import ExitStack, contextmanager, nullcontext
from dataclasses import dataclass, replace
from itertools import islice
from pathlib import Path
from threading import BoundedSemaphore
from typing import BinaryIO, Iterator

from sqlalchemy.orm import Session

from datacoolie_studio.db.models import EnvironmentSource, LogFileManifest
from datacoolie_studio.domains.credentials.store import (
    CredentialSecretStore,
    KeyringCredentialSecretStore,
)
from datacoolie_studio.domains.logs import file_state, staging as log_staging
from datacoolie_studio.domains.logs import batching
from datacoolie_studio.domains.logs.reader import (
    MAX_SYSTEM_LOG_LINE_BYTES,
    iter_system_log_lines,
)
from datacoolie_studio.domains.logs.system_log_selection import (
    filename_job_candidates,
    record_matches_filename,
    system_log_manifests,
)
from datacoolie_studio.domains.sources.storage_binding import binding_from_source
from datacoolie_studio.domains.storage.adapters import (
    LocalStorageAdapter,
    StorageRevision,
)
from datacoolie_studio.domains.storage.errors import StorageConflictError
from datacoolie_studio.domains.storage.factory import create_storage_adapter
from datacoolie_studio.domains.storage.uri import uri_basename
from datacoolie_studio.db.session import ensure_workspace_transaction


# These are module constants so tests can lower the limits without creating
# large fixtures.  A prepared download holds the lease through streaming.
RAM_SPOOL_THRESHOLD = 1 * 1024 * 1024
MAX_LINE_BYTES = MAX_SYSTEM_LOG_LINE_BYTES
MAX_OUTPUT_BYTES = 1 * 1024 * 1024 * 1024
MAX_REMOTE_OBJECT_BYTES = 1 * 1024 * 1024 * 1024
MANIFEST_PAGE_SIZE = 128
STREAM_CHUNK_SIZE = 64 * 1024
MAX_CONCURRENT_DOWNLOADS = 2


_download_leases = BoundedSemaphore(MAX_CONCURRENT_DOWNLOADS)
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class SystemLogDownloadError(RuntimeError):
    """A safe, user-facing failure raised before a download is sent."""

    status_code = 422
    code = "system_log_download_failed"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code:
            self.code = code

    def detail(self) -> dict[str, str]:
        return {"code": self.code, "message": str(self)}


class DownloadBusyError(SystemLogDownloadError):
    status_code = 429
    code = "system_log_download_busy"


class DownloadTooLargeError(SystemLogDownloadError):
    status_code = 413
    code = "system_log_download_too_large"


class DownloadAdmissionError(SystemLogDownloadError):
    status_code = 422
    code = "system_log_download_admission_failed"


class DownloadRevisionConflictError(SystemLogDownloadError):
    status_code = 409
    code = "storage_revision_conflict"


class DownloadStorageError(SystemLogDownloadError):
    status_code = 502
    code = "storage_read_failed"


@dataclass
class PreparedSystemLogDownload:
    """A fully prepared spool whose lease lasts until the stream closes."""

    spool: BinaryIO
    size: int
    filename: str
    _lease: BoundedSemaphore
    _closed: bool = False

    def iter_chunks(self, chunk_size: int = STREAM_CHUNK_SIZE) -> Iterator[bytes]:
        """Read fixed-size chunks and release resources on every exit path."""

        try:
            self.spool.seek(0)
            while chunk := self.spool.read(max(1, int(chunk_size))):
                yield chunk
        finally:
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.spool.close()
        finally:
            self._lease.release()


def safe_download_filename(job_id: str) -> str:
    """Return an ASCII attachment name with no path or control characters."""

    safe_job = _SAFE_FILENAME_RE.sub("-", str(job_id)).strip(".-")
    safe_job = safe_job[:120]
    return f"job-{safe_job or 'log'}-system-log.jsonl"


def prepare_system_log_download(
    session: Session,
    paths: list[EnvironmentSource],
    *,
    job_id: str,
    source_id: int | None = None,
    log_session_id: str | None = None,
    log_session_scope: str = "all",
    secret_store: CredentialSecretStore | None = None,
) -> PreparedSystemLogDownload:
    """Read indexed job candidates once and prepare an original-byte stream.

    Unresolved filenames remain candidates; session and exact job identity stay
    record-scoped. Manifest pages are bounded and files are staged and consumed
    one at a time.
    """

    _validate_request(job_id, log_session_id, log_session_scope)
    enabled_sources = {
        int(source.id): source
        for source in paths
        if source.enabled and source.id is not None
    }
    if source_id is not None:
        enabled_sources = {
            candidate: source
            for candidate, source in enabled_sources.items()
            if candidate == source_id
        }

    if not _download_leases.acquire(blocking=False):
        raise DownloadBusyError(
            "Two system log downloads are already being prepared or streamed; retry shortly"
        )

    spool: BinaryIO | None = None
    try:
        spool = tempfile.SpooledTemporaryFile(
            max_size=max(1, int(RAM_SPOOL_THRESHOLD)),
            mode="w+b",
        )
        with _manifest_read_snapshot(session):
            size = _prepare_spool(
                session,
                enabled_sources,
                spool,
                job_id=job_id,
                log_session_id=log_session_id,
                log_session_scope=log_session_scope,
                secret_store=secret_store,
            )
        spool.seek(0)
        return PreparedSystemLogDownload(
            spool=spool,
            size=size,
            filename=safe_download_filename(job_id),
            _lease=_download_leases,
        )
    except Exception:
        try:
            if spool is not None:
                spool.close()
        finally:
            _download_leases.release()
        raise


@contextmanager
def _manifest_read_snapshot(session: Session) -> Iterator[None]:
    """Ensure paged manifest reads share the session's read snapshot."""

    # The route may already have queried the source list in this session.  In
    # that case the existing transaction is the snapshot owner; otherwise use
    # the repository's SQLite legacy-mode BEGIN helper before the first SELECT.
    if session.in_transaction():
        ensure_workspace_transaction(session)
        yield
        return
    with session.begin():
        ensure_workspace_transaction(session)
        yield


def _prepare_spool(
    session: Session,
    sources: dict[int, EnvironmentSource],
    spool: BinaryIO,
    *,
    job_id: str,
    log_session_id: str | None,
    log_session_scope: str,
    secret_store: CredentialSecretStore | None,
) -> int:
    if not sources:
        return 0

    source_ids = sorted(sources)
    manifests = system_log_manifests(
        session, source_ids, job_id, page_size=MANIFEST_PAGE_SIZE
    )
    adapters: dict[int, object] = {}
    staging_dirs: dict[int, Path] = {}
    total_size = 0

    with ExitStack() as scopes:
        while True:
            page = list(islice(manifests, max(1, int(MANIFEST_PAGE_SIZE))))
            if not page:
                break
            for manifest in page:
                source = sources.get(manifest.source_id)
                if source is None:
                    continue
                read_uri: str | None = None
                staged_target: Path | None = None
                local_before: StorageRevision | None = None
                budget_scope = nullcontext()
                if source.storage_provider != "local":
                    budget_scope = batching.log_byte_budget.acquire(
                        _remote_expected_revision(manifest).size
                    )
                budget_scope.__enter__()
                try:
                    if source.storage_provider == "local":
                        read_uri, local_before = _verify_local_revision(manifest)
                    else:
                        staging_dir = staging_dirs.get(source.id)
                        if staging_dir is None:
                            scopes.enter_context(log_staging.staging_scope(source.id))
                            staging_dir = log_staging.create_staging_dir(source.id)
                            staging_dirs[source.id] = staging_dir
                        adapter = adapters.get(source.id)
                        if adapter is None:
                            adapter = create_storage_adapter(
                                binding_from_source(source),
                                uri=source.uri,
                                session=session,
                                secret_store=secret_store
                                or KeyringCredentialSecretStore(),
                            )
                            adapters[source.id] = adapter
                        read_uri, staged_target = _materialize_remote(
                            manifest,
                            adapter,
                            staging_dir,
                        )

                    candidates = filename_job_candidates(manifest)
                    for item in iter_system_log_lines(
                        read_uri,
                        max_line_bytes=max(1, int(MAX_LINE_BYTES)),
                    ):
                        if item.diagnostic is not None:
                            _raise_diagnostic(item.diagnostic)
                        if item.row is not None and not record_matches_filename(
                            item.row, candidates
                        ):
                            raise DownloadAdmissionError(
                                "System log record job identity differs from its filename",
                                code="system_log_filename_identity_mismatch",
                            )
                        if item.row is None or not _download_record_matches(
                            item.row,
                            job_id=job_id,
                            log_session_id=log_session_id,
                            log_session_scope=log_session_scope,
                        ):
                            continue
                        payload = item.raw
                        if not payload.endswith((b"\n", b"\r")):
                            payload += b"\n"
                        next_size = total_size + len(payload)
                        if next_size > max(1, int(MAX_OUTPUT_BYTES)):
                            raise DownloadTooLargeError(
                                "The selected system log exceeds the 1 GiB download limit",
                                code="download_output_too_large",
                            )
                        _spool_write(spool, payload, total_size)
                        total_size = next_size

                    if source.storage_provider == "local":
                        _verify_local_revision_after(manifest, read_uri, local_before)
                except SystemLogDownloadError:
                    raise
                except StorageConflictError as exc:
                    raise DownloadRevisionConflictError(str(exc)) from exc
                except OSError as exc:
                    raise DownloadStorageError(str(exc)) from exc
                except Exception as exc:
                    raise DownloadStorageError(
                        "System log object could not be read",
                        code=getattr(exc, "code", "storage_read_failed"),
                    ) from exc
                finally:
                    try:
                        if staged_target is not None:
                            log_staging.cleanup_materialized_path(
                                staged_target.as_posix(),
                                staging_dirs[manifest.source_id],
                            )
                    finally:
                        budget_scope.__exit__(None, None, None)
            if len(page) < max(1, int(MANIFEST_PAGE_SIZE)):
                break
    return total_size


def _verify_local_revision(manifest: LogFileManifest) -> tuple[str, StorageRevision]:
    adapter = LocalStorageAdapter()
    try:
        before = adapter.stat(manifest.file_uri)
    except OSError as exc:
        raise DownloadStorageError(str(exc)) from exc
    expected = file_state.file_revision_from_json(
        manifest.file_uri,
        manifest.revision_json,
    )
    if expected is not None and not _revision_equivalent(expected, before):
        raise DownloadRevisionConflictError(
            "The indexed system log changed before it could be downloaded"
        )
    return manifest.file_uri, before


def _verify_local_revision_after(
    manifest: LogFileManifest,
    read_uri: str | None,
    before: StorageRevision | None,
) -> None:
    if read_uri is None or before is None:
        return
    adapter = LocalStorageAdapter()
    try:
        after = adapter.stat(read_uri)
    except OSError as exc:
        raise DownloadStorageError(str(exc)) from exc
    if not _revision_equivalent(before, after):
        raise DownloadRevisionConflictError(
            "The indexed system log changed while it was being downloaded"
        )


def _materialize_remote(
    manifest: LogFileManifest,
    adapter: object,
    staging_dir: Path,
) -> tuple[str, Path]:
    expected = _remote_expected_revision(manifest)
    max_remote_bytes = max(1, int(MAX_REMOTE_OBJECT_BYTES))
    if expected.size > max_remote_bytes:
        raise DownloadTooLargeError(
            "A source system log object exceeds the 1 GiB staging limit",
            code="remote_object_too_large",
        )
    digest = hashlib.sha256(manifest.file_uri.encode("utf-8")).hexdigest()[:16]
    target = staging_dir / f"{digest}-{uri_basename(manifest.file_uri)}"
    temporary = target.with_name(f".{target.name}.download.tmp")
    try:
        before = adapter.stat(manifest.file_uri)  # type: ignore[attr-defined]
        if not isinstance(before, StorageRevision) or not _remote_revision_equivalent(
            expected, before
        ):
            raise DownloadRevisionConflictError(
                "The indexed system log changed before it could be downloaded"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        bytes_written = 0
        source = _open_remote_source(adapter, manifest.file_uri, expected)
        digest = hashlib.sha256()
        with source, temporary.open("wb") as output:
            while True:
                remaining = expected.size - bytes_written
                chunk = source.read(min(1024 * 1024, remaining + 1))
                if not chunk:
                    break
                next_size = bytes_written + len(chunk)
                if next_size > max_remote_bytes:
                    raise DownloadTooLargeError(
                        "The remote system log exceeded the 1 GiB staging limit",
                        code="remote_object_too_large",
                    )
                if next_size > expected.size:
                    raise DownloadRevisionConflictError(
                        "The remote system log size did not match its indexed revision"
                    )
                digest.update(chunk)
                output.write(chunk)
                bytes_written = next_size
        if bytes_written != expected.size:
            raise DownloadRevisionConflictError(
                "The remote system log size did not match its indexed revision"
            )
        if (
            expected.content_hash
            and digest.hexdigest().lower() != expected.content_hash.lower()
        ):
            raise DownloadRevisionConflictError(
                "The remote system log content did not match its indexed revision"
            )
        after = adapter.stat(manifest.file_uri)  # type: ignore[attr-defined]
        if not isinstance(after, StorageRevision) or not _remote_revision_equivalent(
            expected, after
        ):
            raise DownloadRevisionConflictError(
                "The remote system log changed while it was being downloaded"
            )
        os.replace(temporary, target)
    except (SystemLogDownloadError, StorageConflictError):
        raise
    except OSError as exc:
        raise DownloadStorageError(
            "System log object could not be staged",
            code=getattr(exc, "code", "storage_read_failed"),
        ) from exc
    except Exception as exc:
        raise DownloadStorageError(
            "System log object could not be staged",
            code=getattr(exc, "code", "storage_read_failed"),
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)
    return target.as_posix(), target


def _remote_expected_revision(manifest: LogFileManifest) -> StorageRevision:
    try:
        payload = json.loads(manifest.revision_json or "")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DownloadTooLargeError(
            "The indexed system log has no usable remote revision",
            code="remote_revision_unknown",
        ) from exc
    if (
        not isinstance(payload, dict)
        or "size" not in payload
        or payload.get("size") is None
    ):
        raise DownloadTooLargeError(
            "The remote system log size is unknown; refresh the log source before downloading",
            code="remote_size_unknown",
        )
    try:
        expected = file_state.file_revision_from_json(
            manifest.file_uri,
            manifest.revision_json,
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise DownloadTooLargeError(
            "The indexed system log has no usable remote revision",
            code="remote_revision_unknown",
        ) from exc
    if expected is None:
        raise DownloadTooLargeError(
            "The indexed system log has no usable remote revision",
            code="remote_revision_unknown",
        )
    content_hash = payload.get("content_hash")
    if content_hash is not None:
        expected = replace(expected, content_hash=str(content_hash))
    return expected


def _open_remote_source(
    adapter: object,
    uri: str,
    expected: StorageRevision,
) -> BinaryIO:
    """Open a provider snapshot when the adapter exposes one.

    DBFS and OneLake return the revision attached to their download response.
    Comparing that revision before consuming the stream closes the stat/open
    ABA window while retaining the generic adapter protocol for fsspec-backed
    providers.
    """

    open_with_revision = getattr(adapter, "open_read_with_revision", None)
    if not callable(open_with_revision):
        return adapter.open_read(uri)  # type: ignore[attr-defined]
    source: BinaryIO | None = None
    try:
        opened = open_with_revision(uri)
        if (
            not isinstance(opened, tuple)
            or len(opened) != 2
            or not hasattr(opened[0], "read")
            or not isinstance(opened[1], StorageRevision)
        ):
            raise DownloadStorageError(
                "The remote adapter returned an invalid revision-aware stream",
                code="storage_read_failed",
            )
        source = opened[0]
        observed = opened[1]
        if not _remote_revision_equivalent(expected, observed):
            raise DownloadRevisionConflictError(
                "The indexed system log changed before it could be downloaded"
            )
        return source
    except Exception:
        if source is not None:
            try:
                source.close()
            except Exception:
                pass
        raise


def _revision_equivalent(left: StorageRevision, right: StorageRevision) -> bool:
    """Compare local revisions while tolerating equivalent URI spellings."""

    try:
        local_adapter = LocalStorageAdapter()
        left_uri = local_adapter.canonical_uri(left.canonical_uri)
        right_uri = local_adapter.canonical_uri(right.canonical_uri)
        same_uri = left_uri == right_uri
    except (OSError, RuntimeError, ValueError):
        try:
            same_uri = (
                Path(left.canonical_uri).resolve()
                == Path(right.canonical_uri).resolve()
            )
        except (OSError, RuntimeError, ValueError):
            same_uri = left.canonical_uri == right.canonical_uri
    except Exception:
        same_uri = left.canonical_uri == right.canonical_uri
    if not same_uri or left.size != right.size:
        return False
    if left.provider_revision is not None and right.provider_revision is not None:
        return left.provider_revision == right.provider_revision
    return left.last_modified == right.last_modified


def _remote_revision_equivalent(left: StorageRevision, right: StorageRevision) -> bool:
    """Compare a provider revision without depending on URI spelling."""

    if left.size != right.size:
        return False
    if left.content_hash is not None and right.content_hash is not None:
        return left.content_hash == right.content_hash
    if left.provider_revision is not None and right.provider_revision is not None:
        return left.provider_revision == right.provider_revision
    return left.last_modified == right.last_modified


def _spool_write(spool: BinaryIO, payload: bytes, current_size: int) -> None:
    threshold = max(1, int(RAM_SPOOL_THRESHOLD))
    if current_size + len(payload) > threshold:
        rollover = getattr(spool, "rollover", None)
        if callable(rollover):
            rollover()
    spool.write(payload)


def _download_record_matches(
    row: dict[str, object],
    *,
    job_id: str,
    log_session_id: str | None,
    log_session_scope: str,
) -> bool:
    if str(row.get("job_id") or "") != job_id:
        return False
    if log_session_scope == "null":
        return row.get("log_session_id") is None
    if log_session_scope == "exact":
        return row.get("log_session_id") == log_session_id
    return True


def _raise_diagnostic(diagnostic: dict[str, object]) -> None:
    code = str(diagnostic.get("code") or "system_log_admission_failed")
    message = str(diagnostic.get("message") or "System log admission failed")
    if code == "line_too_large":
        raise DownloadTooLargeError(message, code=code)
    raise DownloadAdmissionError(message, code=code)


def _validate_request(
    job_id: str,
    log_session_id: str | None,
    log_session_scope: str,
) -> None:
    if not str(job_id):
        raise SystemLogDownloadError("job_id is required", code="job_id_required")
    if log_session_scope not in {"all", "null", "exact"}:
        raise SystemLogDownloadError(
            "log_session_scope must be all, null, or exact",
            code="invalid_session_scope",
        )
    if log_session_scope == "exact" and log_session_id is None:
        raise SystemLogDownloadError(
            "Exact session scope requires log_session_id",
            code="exact_session_required",
        )
