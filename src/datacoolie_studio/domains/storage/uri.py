from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse, unquote


CLOUD_SCHEMES = {
    "abfs": "adls",
    "abfss": "adls",
    "adl": "adls",
    "dbfs": "dbfs",
    "s3": "s3",
    "s3a": "s3",
    "s3n": "s3",
    "wasb": "azure_blob",
    "wasbs": "azure_blob",
}


@dataclass(frozen=True)
class StorageUri:
    uri: str
    provider: str
    scheme: str
    local_path: Path | None = None

    @property
    def is_local(self) -> bool:
        return self.provider == "local" and self.local_path is not None


class StorageProviderNotEnabled(RuntimeError):
    def __init__(self, provider: str, uri: str) -> None:
        super().__init__(f"{provider.upper()} storage URI is recognized but not enabled yet: {uri}")
        self.provider = provider
        self.uri = uri


def parse_storage_uri(uri: str) -> StorageUri:
    text = str(uri).strip()
    parsed = urlparse(text)
    scheme = parsed.scheme.lower()
    if scheme == "file":
        local_path = Path(unquote(parsed.path))
        return StorageUri(uri=text, provider="local", scheme=scheme, local_path=local_path)
    if scheme and not _looks_like_windows_drive(text):
        provider = CLOUD_SCHEMES.get(scheme, scheme)
        return StorageUri(uri=text, provider=provider, scheme=scheme, local_path=None)
    return StorageUri(uri=text, provider="local", scheme="", local_path=Path(text).expanduser())


def require_local_path(uri: str) -> Path:
    parsed = parse_storage_uri(uri)
    if not parsed.is_local or parsed.local_path is None:
        raise StorageProviderNotEnabled(parsed.provider, parsed.uri)
    return parsed.local_path


def join_uri(base_uri: str, *parts: str) -> str:
    parsed = parse_storage_uri(base_uri)
    clean_parts = [str(part).strip().strip("/\\") for part in parts if str(part).strip().strip("/\\")]
    if not clean_parts:
        return base_uri
    if parsed.is_local and parsed.local_path is not None:
        return str(parsed.local_path.joinpath(*clean_parts))
    return "/".join([base_uri.rstrip("/"), *clean_parts])


def uri_basename(uri: str) -> str:
    parsed = parse_storage_uri(uri)
    if parsed.is_local and parsed.local_path is not None:
        return parsed.local_path.name
    text = uri.rstrip("/")
    return text.rsplit("/", 1)[-1] if "/" in text else text


def normalized_source_uri(uri: str) -> str:
    parsed = parse_storage_uri(uri)
    if parsed.is_local and parsed.local_path is not None:
        path = parsed.local_path
        try:
            return str(path.resolve())
        except OSError:
            return str(path)
    return uri.rstrip("/")


def _looks_like_windows_drive(value: str) -> bool:
    return len(value) >= 2 and value[1] == ":" and value[0].isalpha()
