"""Durable, content-addressed storage for prepared metadata write bytes."""

from __future__ import annotations

import hashlib
import os
import json
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from datacoolie_studio.core.config import metadata_write_artifact_dir
from datacoolie_studio.domains.metadata.reader import MetadataReadError
from datacoolie_studio.core.process_lock import reentrant_process_lock
from datacoolie_studio.db.session import workspace_identity


@dataclass(frozen=True)
class StoredArtifact:
    ref: str
    sha256: str
    size: int


class MetadataArtifactStore:
    """Store bytes below the Studio data root without exposing filesystem paths."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or metadata_write_artifact_dir()).expanduser().resolve()

    @contextmanager
    def lifecycle(self, session, *, staging=False):
        """Root-wide exclusion from staging to reference commit or ref check to unlink."""
        with reentrant_process_lock(self.root / ".lifecycle.lock",
                                    timeout_seconds=5, owner_label="metadata artifacts"):
            marker = self.root / ".workspace-owner.json"
            identity = workspace_identity(session)
            if not marker.exists():
                populated = (self.root / "objects").exists() and any((self.root / "objects").rglob("*"))
                owner = None if populated else identity
                temporary = marker.with_name(f".{marker.name}.{uuid4().hex}.tmp")
                try:
                    temporary.write_text(json.dumps({"workspace": owner}), encoding="utf-8")
                    os.replace(temporary, marker)
                finally:
                    temporary.unlink(missing_ok=True)
            try:
                owner = json.loads(marker.read_text(encoding="utf-8"))["workspace"]
            except (OSError, ValueError, KeyError):
                owner = None
            if staging and owner is not None and owner != identity:
                raise MetadataReadError("Metadata artifact root belongs to another workspace database")
            yield owner == identity

    def put(self, content: bytes) -> StoredArtifact:
        digest = hashlib.sha256(content).hexdigest()
        relative_ref = Path("objects") / digest[:2] / digest
        target = self._resolve_ref(relative_ref.as_posix())
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            self._verify_file(target, digest, len(content))
            return StoredArtifact(relative_ref.as_posix(), digest, len(content))

        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.replace(temporary, target)
            except FileExistsError:
                # Another process won the content-addressed race.  Its object
                # is acceptable only after the same integrity check.
                self._verify_file(target, digest, len(content))
            _restrict_file(target)
            _fsync_directory(target.parent)
        finally:
            temporary.unlink(missing_ok=True)
        return StoredArtifact(relative_ref.as_posix(), digest, len(content))

    def read(
        self,
        ref: str,
        *,
        expected_sha256: str,
        expected_size: int,
    ) -> bytes:
        path = self._resolve_ref(ref)
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise MetadataReadError("Prepared metadata artifact is unavailable") from exc
        self._verify_content(content, expected_sha256, expected_size)
        return content

    def remove(self, ref: str) -> bool:
        path = self._resolve_ref(ref)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise MetadataReadError("Prepared metadata artifact could not be removed") from exc
        return True

    def _resolve_ref(self, ref: str) -> Path:
        candidate = Path(ref)
        if (
            not ref
            or candidate.is_absolute()
            or bool(candidate.drive)
            or any(part in {"", ".", ".."} for part in ref.replace("\\", "/").split("/"))
        ):
            raise MetadataReadError("Invalid metadata artifact reference")
        resolved = (self.root / candidate).resolve(strict=False)
        if resolved == self.root or not resolved.is_relative_to(self.root):
            raise MetadataReadError("Invalid metadata artifact reference")
        return resolved

    @staticmethod
    def _verify_file(path: Path, expected_sha256: str, expected_size: int) -> None:
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise MetadataReadError("Metadata artifact is unavailable") from exc
        MetadataArtifactStore._verify_content(content, expected_sha256, expected_size)

    @staticmethod
    def _verify_content(content: bytes, expected_sha256: str, expected_size: int) -> None:
        if len(content) != expected_size or hashlib.sha256(content).hexdigest() != expected_sha256:
            raise MetadataReadError("Metadata artifact failed integrity verification")


def _restrict_file(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        # Windows ACLs are inherited from the configured Studio data root;
        # chmod is best-effort there and must not make a valid write fail.
        pass


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
