from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlparse, urlsplit, urlunsplit
from uuid import UUID

from datacoolie_studio.domains.storage.errors import StorageConfigurationError


CLOUD_SCHEMES = {
    "abfs": "adls",
    "abfss": "adls",
    "adl": "adls",
    "dbfs": "dbfs",
    "gcs": "gcs",
    "gs": "gcs",
    "s3": "s3",
    "s3a": "s3",
    "s3n": "s3",
    "wasb": "azure_blob",
    "wasbs": "azure_blob",
}
ONELAKE_DFS_HOST = "onelake.dfs.fabric.microsoft.com"


@dataclass(frozen=True)
class StorageUri:
    uri: str
    provider: str
    scheme: str
    local_path: Path | None = None

    @property
    def is_local(self) -> bool:
        return self.provider == "local" and self.local_path is not None


@dataclass(frozen=True)
class OneLakeLocation:
    workspace: str
    item: str
    relative_path: str

    @property
    def sdk_path(self) -> str:
        base = f"{self.item}/Files"
        return f"{base}/{self.relative_path}" if self.relative_path else base


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
    if _is_onelake_uri(parsed):
        return StorageUri(uri=text, provider="onelake", scheme=scheme, local_path=None)
    if _is_adls_https_uri(parsed):
        return StorageUri(uri=text, provider="adls", scheme=scheme, local_path=None)
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


def validate_storage_uri(uri: str, provider: str | None = None) -> StorageUri:
    raw_uri = str(uri).strip()
    detected = parse_storage_uri(raw_uri)
    expected = provider.lower() if provider else detected.provider
    normalized_uri = _normalize_provider_alias(raw_uri, expected)
    parsed = parse_storage_uri(normalized_uri)
    if expected == "onelake":
        parse_onelake_location(normalized_uri)
        return parsed
    if expected == "minio":
        if parsed.scheme not in {"s3", "s3a", "s3n"}:
            raise StorageConfigurationError("MinIO locations must use s3:// URIs")
    elif parsed.provider != expected:
        raise StorageConfigurationError(
            f"URI provider {parsed.provider!r} does not match {expected!r}"
        )
    if parsed.is_local:
        return parsed
    split = urlsplit(normalized_uri)
    if expected == "dbfs":
        if split.scheme.lower() != "dbfs":
            raise StorageConfigurationError("DBFS locations must use dbfs:/ URIs")
        if split.netloc:
            raise StorageConfigurationError(
                "DBFS URI must not contain a workspace host; configure it in authentication"
            )
        if not split.path.startswith("/"):
            raise StorageConfigurationError("DBFS URI must use an absolute path")
        if not split.path.strip("/"):
            raise StorageConfigurationError("DBFS URI must identify a path")
        if split.query or split.fragment:
            raise StorageConfigurationError(
                "DBFS URI query strings and fragments are not allowed"
            )
        if ".." in [unquote(part) for part in split.path.split("/")]:
            raise StorageConfigurationError("DBFS URI must not contain parent traversal")
        return parsed
    adls_container_account = (
        expected == "adls"
        and split.password is None
        and split.netloc.count("@") == 1
    )
    if (split.username or split.password) and not adls_container_account:
        raise StorageConfigurationError("Storage URI must not contain userinfo")
    if split.query:
        raise StorageConfigurationError(
            "Storage URI query strings are not allowed; use typed provider options"
        )
    if split.fragment:
        raise StorageConfigurationError("Storage URI fragments are not allowed")
    if not split.netloc:
        raise StorageConfigurationError("Storage URI must identify a bucket or container")
    return parsed


def canonical_cloud_uri(uri: str, provider: str | None = None) -> str:
    raw_uri = str(uri).strip()
    detected = parse_storage_uri(raw_uri)
    expected = provider.lower() if provider else detected.provider
    normalized_uri = _normalize_provider_alias(raw_uri, expected)
    parsed = validate_storage_uri(normalized_uri, expected)
    if parsed.is_local:
        return normalized_source_uri(normalized_uri)
    split = urlsplit(normalized_uri)
    scheme = {
        "s3": "s3",
        "minio": "s3",
        "adls": "abfs",
        "onelake": "abfss",
        "gcs": "gs",
        "dbfs": "dbfs",
    }.get(expected, split.scheme.lower())
    if expected == "onelake":
        location = parse_onelake_location(normalized_uri)
        path = f"/{_encode_segment(location.item)}/Files"
        if location.relative_path:
            path += "/" + "/".join(
                _encode_segment(part) for part in location.relative_path.split("/")
            )
        return urlunsplit(
            (
                "abfss",
                f"{_encode_segment(location.workspace)}@{ONELAKE_DFS_HOST}",
                path,
                "",
                "",
            )
        )
    path = "/" + "/".join(
        quote(unquote(part), safe=":@!$&'()*+,;=-._~")
        for part in split.path.split("/")
        if part
    )
    if path == "/":
        path = ""
    if scheme == "dbfs":
        return f"dbfs:{path.rstrip('/')}"
    return urlunsplit((scheme, split.netloc.lower(), path.rstrip("/"), "", ""))


def _looks_like_windows_drive(value: str) -> bool:
    return len(value) >= 2 and value[1] == ":" and value[0].isalpha()


def _normalize_provider_alias(uri: str, provider: str) -> str:
    if provider == "onelake":
        parsed = urlsplit(uri)
        if _is_onelake_https_uri(parsed):
            if parsed.username or parsed.password or parsed.port:
                raise StorageConfigurationError(
                    "OneLake HTTPS URI must not contain userinfo or a port"
                )
            segments = _validated_raw_segments(parsed.path)
            if len(segments) < 3:
                raise StorageConfigurationError(
                    "OneLake URI must identify a workspace, Lakehouse, and Files path"
                )
            workspace, item, files, *relative = segments
            return urlunsplit(
                (
                    "abfss",
                    f"{workspace}@{ONELAKE_DFS_HOST}",
                    "/" + "/".join([item, files, *relative]),
                    "",
                    "",
                )
            )
    if provider == "adls":
        parsed = urlsplit(uri)
        if _is_adls_https_uri(parsed):
            if parsed.username or parsed.password or parsed.port:
                raise StorageConfigurationError(
                    "Azure Data Lake HTTPS URI must not contain userinfo or a port"
                )
            if parsed.query or parsed.fragment:
                raise StorageConfigurationError(
                    "Storage URI query strings and fragments are not allowed"
                )
            path = parsed.path.strip("/")
            if not path:
                raise StorageConfigurationError(
                    "Azure Data Lake HTTPS URI must identify a container"
                )
            container, _, relative_path = path.partition("/")
            return urlunsplit(
                (
                    "abfs",
                    f"{container}@{parsed.hostname}",
                    f"/{relative_path}" if relative_path else "",
                    "",
                    "",
                )
            )
    if provider == "dbfs":
        if uri.startswith("/Volumes/"):
            return f"dbfs:{uri}"
        if uri.startswith("Volumes/"):
            return f"dbfs:/{uri}"
    return uri


def _is_adls_https_uri(parsed) -> bool:
    hostname = parsed.hostname
    return (
        parsed.scheme.lower() == "https"
        and hostname is not None
        and hostname.lower().endswith(".dfs.core.windows.net")
        and hostname.lower() != "dfs.core.windows.net"
    )


def parse_onelake_location(uri: str) -> OneLakeLocation:
    """Validate and decompose a canonical or user-entered OneLake Files URI."""

    parsed = urlsplit(str(uri).strip())
    scheme = parsed.scheme.lower()
    if parsed.query or parsed.fragment:
        raise StorageConfigurationError(
            "OneLake URI query strings and fragments are not allowed"
        )
    if scheme == "https":
        if not _is_onelake_https_uri(parsed):
            raise StorageConfigurationError(
                f"OneLake HTTPS URI must use {ONELAKE_DFS_HOST}"
            )
        if parsed.username or parsed.password or parsed.port:
            raise StorageConfigurationError(
                "OneLake HTTPS URI must not contain userinfo or a port"
            )
        segments = _validated_raw_segments(parsed.path)
        if len(segments) < 3:
            raise StorageConfigurationError(
                "OneLake URI must identify a workspace, Lakehouse, and Files path"
            )
        workspace_raw, item_raw, files_raw, *relative_raw = segments
    elif scheme in {"abfs", "abfss"}:
        if parsed.password or parsed.port or parsed.netloc.count("@") != 1:
            raise StorageConfigurationError(
                "OneLake ABFS URI must use workspace@onelake.dfs.fabric.microsoft.com"
            )
        workspace_raw, _, host = parsed.netloc.partition("@")
        if host.lower() != ONELAKE_DFS_HOST or not workspace_raw:
            raise StorageConfigurationError(
                "OneLake ABFS URI must use workspace@onelake.dfs.fabric.microsoft.com"
            )
        segments = _validated_raw_segments(parsed.path)
        if len(segments) < 2:
            raise StorageConfigurationError(
                "OneLake URI must identify a Lakehouse and Files path"
            )
        item_raw, files_raw, *relative_raw = segments
    else:
        raise StorageConfigurationError(
            "OneLake locations must use abfs://, abfss://, or OneLake DFS HTTPS"
        )

    workspace = _decode_onelake_segment(workspace_raw, "workspace")
    item = _decode_onelake_segment(item_raw, "item")
    files = _decode_onelake_segment(files_raw, "root")
    relative = tuple(
        _decode_onelake_segment(value, "path") for value in relative_raw
    )
    if files != "Files":
        raise StorageConfigurationError(
            "OneLake locations must be inside a Lakehouse Files path; Tables is not supported"
        )
    workspace_id = _uuid_value(workspace)
    item_id = _uuid_value(item)
    if workspace_id or item_id:
        if workspace_id is None or item_id is None:
            raise StorageConfigurationError(
                "OneLake GUID locations must use GUIDs for both workspace and item"
            )
        workspace = workspace_id
        item = item_id
    elif not item.lower().endswith(".lakehouse"):
        raise StorageConfigurationError(
            "OneLake named items must end with .Lakehouse"
        )
    else:
        item = f"{item[: -len('.lakehouse')]}.Lakehouse"
    return OneLakeLocation(
        workspace=workspace,
        item=item,
        relative_path="/".join(relative),
    )


def _is_onelake_uri(parsed) -> bool:
    return _is_onelake_https_uri(parsed) or (
        parsed.scheme.lower() in {"abfs", "abfss"}
        and (parsed.hostname or "").lower() == ONELAKE_DFS_HOST
    )


def _is_onelake_https_uri(parsed) -> bool:
    return (
        parsed.scheme.lower() == "https"
        and (parsed.hostname or "").lower() == ONELAKE_DFS_HOST
    )


def _validated_raw_segments(path: str) -> list[str]:
    segments = [value for value in path.split("/") if value]
    for value in segments:
        lowered = value.lower()
        if "%2f" in lowered or "%5c" in lowered:
            raise StorageConfigurationError(
                "OneLake URI path segments must not contain encoded separators"
            )
    return segments


def _decode_onelake_segment(value: str, label: str) -> str:
    decoded = unquote(value)
    if (
        not decoded
        or decoded in {".", ".."}
        or "/" in decoded
        or "\\" in decoded
        or any(ord(character) < 32 for character in decoded)
    ):
        raise StorageConfigurationError(f"Invalid OneLake {label} path segment")
    return decoded


def _uuid_value(value: str) -> str | None:
    try:
        return str(UUID(value))
    except ValueError:
        return None


def _encode_segment(value: str) -> str:
    return quote(value, safe=":@!$&'()*+,;=-._~")
