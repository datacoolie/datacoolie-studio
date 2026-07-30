from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import threading
import time
from collections import OrderedDict
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from datacoolie_studio.db.models import CredentialProfile
from datacoolie_studio.domains.credentials.store import (
    CredentialSecretStore,
    SecretNotFound,
    SecretStoreUnavailable,
)
from datacoolie_studio.domains.credentials.health import (
    record_credential_secret_state,
)
from datacoolie_studio.domains.storage.adapters import (
    LocalStorageAdapter,
    StorageAdapter,
)
from datacoolie_studio.domains.storage.binding import StorageBinding
from datacoolie_studio.domains.storage.dbfs_adapter import DbfsStorageAdapter
from datacoolie_studio.domains.storage.errors import (
    ProviderDependencyMissing,
    StorageAuthenticationError,
    StorageConfigurationError,
)
from datacoolie_studio.domains.storage.fsspec_adapter import FsspecStorageAdapter
from datacoolie_studio.domains.storage.onelake_adapter import OneLakeStorageAdapter
from datacoolie_studio.domains.storage.uri import validate_storage_uri
from datacoolie_studio.domains.storage.writers import (
    AdlsConditionalStorageWriter,
    ConditionalStorageWriter,
    DatabricksVerifiedStorageWriter,
    GcsConditionalStorageWriter,
    LocalConditionalStorageWriter,
    OneLakeConditionalStorageWriter,
    S3ConditionalStorageWriter,
)


PROVIDER_DEPENDENCIES = {
    "s3": ("s3fs", 'pip install "datacoolie-studio[s3]"'),
    "minio": ("s3fs", 'pip install "datacoolie-studio[minio]"'),
    "adls": ("adlfs", 'pip install "datacoolie-studio[adls]"'),
    "onelake": (
        "azure.storage.filedatalake",
        'pip install "datacoolie-studio[onelake]"',
    ),
    "gcs": ("gcsfs", 'pip install "datacoolie-studio[gcs]"'),
    "dbfs": (
        "databricks.sdk",
        'pip install "databricks-sdk>=0.121,<0.122"',
    ),
}

_DBFS_CLIENT_CACHE_TTL_SECONDS = 60 * 60
_DBFS_CLIENT_CACHE_MAX_SIZE = 8
_dbfs_client_cache: OrderedDict[str, tuple[float, object]] = OrderedDict()
_dbfs_client_cache_lock = threading.Lock()
_ONELAKE_CLIENT_CACHE_TTL_SECONDS = 60 * 60
_ONELAKE_CLIENT_CACHE_MAX_SIZE = 8
_onelake_client_cache: OrderedDict[
    str, tuple[float, object, object]
] = OrderedDict()
_onelake_client_cache_lock = threading.Lock()


def invalidate_storage_client_caches() -> None:
    """Drop reusable provider clients after credential configuration changes."""

    with _dbfs_client_cache_lock:
        _dbfs_client_cache.clear()
    with _onelake_client_cache_lock:
        cached = list(_onelake_client_cache.values())
        _onelake_client_cache.clear()
    for _created_at, client, credential in cached:
        _close_if_supported(client)
        _close_if_supported(credential)


def provider_capabilities() -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {
        "local": {"available": True, "install_command": None}
    }
    for provider, (module_name, install_command) in PROVIDER_DEPENDENCIES.items():
        available = _module_available(module_name)
        result[provider] = {
            "available": available,
            "install_command": install_command,
        }
    return result


def create_storage_adapter(
    binding: StorageBinding,
    *,
    uri: str | None = None,
    session: Session | None = None,
    secret_store: CredentialSecretStore | None = None,
) -> StorageAdapter:
    _validate_provider_configuration(binding, uri)
    if binding.provider == "local":
        return LocalStorageAdapter()
    module_name, install_command = PROVIDER_DEPENDENCIES[binding.provider]
    if not _module_available(module_name):
        raise ProviderDependencyMissing(binding.provider, install_command)

    auth_type, credentials = _resolve_credentials(
        binding, session=session, secret_store=secret_store
    )

    if binding.provider == "dbfs":
        try:
            client = _create_dbfs_client(
                binding,
                auth_type=auth_type,
                credentials=credentials,
            )
        finally:
            credentials.clear()
        return DbfsStorageAdapter(client)

    if binding.provider == "onelake":
        try:
            identity = importlib.import_module("azure.identity")
            data_lake = importlib.import_module("azure.storage.filedatalake")
            service_client = _cached_onelake_service_client(
                identity,
                data_lake,
                auth_type=auth_type,
                credentials=credentials,
            )
        finally:
            credentials.clear()
        return OneLakeStorageAdapter(service_client)

    try:
        filesystem = _create_filesystem(
            binding,
            uri=uri,
            auth_type=auth_type,
            credentials=credentials,
        )
    finally:
        credentials.clear()
    return FsspecStorageAdapter(filesystem, provider=binding.provider)


def create_storage_writer(
    binding: StorageBinding,
    *,
    uri: str | None = None,
    session: Session | None = None,
    secret_store: CredentialSecretStore | None = None,
) -> ConditionalStorageWriter:
    _validate_provider_configuration(binding, uri)
    if binding.provider == "local":
        return LocalConditionalStorageWriter()
    auth_type, credentials = _resolve_credentials(
        binding, session=session, secret_store=secret_store
    )
    try:
        if binding.provider == "dbfs":
            _require_module(
                "databricks.sdk",
                "dbfs",
                PROVIDER_DEPENDENCIES["dbfs"][1],
            )
            client = _create_dbfs_client(
                binding,
                auth_type=auth_type,
                credentials=credentials,
            )
            adapter = DbfsStorageAdapter(client)
            return DatabricksVerifiedStorageWriter(client, adapter.open_read)

        if binding.provider == "onelake":
            _require_module(
                "azure.storage.filedatalake",
                "onelake",
                PROVIDER_DEPENDENCIES["onelake"][1],
            )
            identity = importlib.import_module("azure.identity")
            data_lake = importlib.import_module("azure.storage.filedatalake")
            core = importlib.import_module("azure.core")
            service_client = _cached_onelake_service_client(
                identity,
                data_lake,
                auth_type=auth_type,
                credentials=credentials,
            )
            return OneLakeConditionalStorageWriter(
                service_client,
                if_not_modified=core.MatchConditions.IfNotModified,
            )

        if binding.provider in {"s3", "minio"}:
            _require_module(
                "boto3",
                binding.provider,
                PROVIDER_DEPENDENCIES[binding.provider][1],
            )
            boto3 = importlib.import_module("boto3")
            client_kwargs: dict[str, object] = {}
            if binding.options.get("region"):
                client_kwargs["region_name"] = binding.options["region"]
            if binding.provider == "minio":
                client_kwargs["endpoint_url"] = binding.options["endpoint_url"]
                client_kwargs["verify"] = binding.options.get("verify_tls", True)
            if auth_type == "aws_shared_profile":
                session_factory = boto3.Session(
                    profile_name=credentials.get("profile_name")
                )
                client = session_factory.client("s3", **client_kwargs)
            else:
                if auth_type == "access_key":
                    client_kwargs.update(
                        {
                            "aws_access_key_id": credentials.get("access_key_id"),
                            "aws_secret_access_key": credentials.get(
                                "secret_access_key"
                            ),
                            "aws_session_token": credentials.get("session_token"),
                        }
                    )
                if binding.auth_mode == "anonymous":
                    botocore = importlib.import_module("botocore")
                    botocore_config = importlib.import_module("botocore.config")
                    client_kwargs["config"] = botocore_config.Config(
                        signature_version=botocore.UNSIGNED
                    )
                client = boto3.client("s3", **client_kwargs)
            return S3ConditionalStorageWriter(client)

        if binding.provider == "adls":
            _require_module(
                "azure.storage.blob",
                "adls",
                PROVIDER_DEPENDENCIES["adls"][1],
            )
            blob_module = importlib.import_module("azure.storage.blob")
            core_module = importlib.import_module("azure.core")
            account_name = str(
                credentials.get("account_name")
                or binding.options.get("account_name")
                or _adls_account_name(uri)
                or ""
            )
            _validate_adls_account_match(uri, account_name)
            if not account_name:
                raise StorageConfigurationError(
                    "ADLS account_name is required for conditional writes"
                )
            credential: object | None
            if auth_type == "service_principal":
                identity = importlib.import_module("azure.identity")
                credential = identity.ClientSecretCredential(
                    tenant_id=credentials["tenant_id"],
                    client_id=credentials["client_id"],
                    client_secret=credentials["client_secret"],
                )
            elif auth_type == "sas":
                credential = credentials.get("sas_token")
            elif auth_type == "account_key":
                credential = credentials.get("account_key")
            else:
                identity = importlib.import_module("azure.identity")
                credential = identity.DefaultAzureCredential()
            service = blob_module.BlobServiceClient(
                account_url=f"https://{account_name}.blob.core.windows.net",
                credential=credential,
            )

            def blob_client(uri: str):
                from urllib.parse import urlsplit

                parsed = urlsplit(uri)
                container = parsed.username or parsed.netloc.split("@", 1)[0]
                return service.get_blob_client(
                    container=container, blob=parsed.path.lstrip("/")
                )

            return AdlsConditionalStorageWriter(
                blob_client,
                if_not_modified=core_module.MatchConditions.IfNotModified,
            )

        _require_module(
            "google.cloud.storage",
            "gcs",
            PROVIDER_DEPENDENCIES["gcs"][1],
        )
        google_storage = importlib.import_module("google.cloud.storage")
        client_kwargs = {}
        if binding.options.get("project_id"):
            client_kwargs["project"] = binding.options["project_id"]
        if auth_type == "service_account":
            service_account = importlib.import_module(
                "google.oauth2.service_account"
            )
            client_kwargs["credentials"] = (
                service_account.Credentials.from_service_account_info(
                    credentials["service_account_json"]
                )
            )
        elif binding.auth_mode == "anonymous":
            anonymous = importlib.import_module("google.auth.credentials")
            client_kwargs["credentials"] = anonymous.AnonymousCredentials()
        client = google_storage.Client(**client_kwargs)
        return GcsConditionalStorageWriter(client.bucket)
    finally:
        credentials.clear()


def _resolve_credentials(
    binding: StorageBinding,
    *,
    session: Session | None,
    secret_store: CredentialSecretStore | None,
) -> tuple[str | None, dict[str, object]]:
    credentials: dict[str, object] = {}
    auth_type: str | None = None
    if binding.auth_mode != "credential_profile":
        return auth_type, credentials
    if session is None or secret_store is None:
        raise StorageConfigurationError(
            "Credential profile resolution requires a database session and secret store"
        )
    profile = session.get(CredentialProfile, binding.credential_profile_id)
    if profile is None:
        raise StorageAuthenticationError(
            "Credential profile was not found", provider=binding.provider
        )
    if profile.provider != binding.provider:
        raise StorageConfigurationError(
            "Credential profile provider does not match storage provider"
        )
    auth_type = profile.auth_type
    credentials.update(_load_object(profile.config_json))
    if profile.secret_ref:
        try:
            credentials.update(secret_store.get(profile.secret_ref))
        except SecretNotFound as exc:
            record_credential_secret_state(profile.id, "missing")
            raise StorageAuthenticationError(
                "Credential secret is missing", provider=binding.provider
            ) from exc
        except SecretStoreUnavailable as exc:
            record_credential_secret_state(profile.id, "unavailable")
            raise StorageAuthenticationError(
                "Credential secret store is unavailable",
                provider=binding.provider,
            ) from exc
    record_credential_secret_state(profile.id, "present")
    return auth_type, credentials


def _require_module(module_name: str, provider: str, install_command: str) -> None:
    if not _module_available(module_name):
        raise ProviderDependencyMissing(provider, install_command)


def _module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except ModuleNotFoundError:
        return False


def _create_dbfs_client(
    binding: StorageBinding,
    *,
    auth_type: str | None,
    credentials: dict[str, object],
):
    sdk = importlib.import_module("databricks.sdk")
    client_kwargs: dict[str, object] = {}
    if binding.options.get("host"):
        client_kwargs["host"] = binding.options["host"]
    if binding.options.get("profile"):
        client_kwargs["profile"] = binding.options["profile"]
    if auth_type == "databricks_profile":
        client_kwargs["profile"] = credentials.get("profile")
        if credentials.get("host"):
            client_kwargs["host"] = credentials["host"]
    elif auth_type == "pat":
        client_kwargs.update(
            {"host": credentials.get("host"), "token": credentials.get("token")}
        )
    elif auth_type == "oauth_m2m":
        client_kwargs.update(
            {
                "host": credentials.get("host"),
                "client_id": credentials.get("client_id"),
                "client_secret": credentials.get("client_secret"),
            }
        )
    return _cached_dbfs_client(
        sdk,
        {key: value for key, value in client_kwargs.items() if value},
    )


def _cached_dbfs_client(sdk, client_kwargs: dict[str, object]):
    key_payload = json.dumps(
        {
            "factory": id(sdk.WorkspaceClient),
            "kwargs": client_kwargs,
        },
        sort_keys=True,
        default=str,
    )
    cache_key = hashlib.sha256(key_payload.encode("utf-8")).hexdigest()
    now = time.monotonic()
    with _dbfs_client_cache_lock:
        cached = _dbfs_client_cache.get(cache_key)
        if cached is not None:
            created_at, client = cached
            if now - created_at < _DBFS_CLIENT_CACHE_TTL_SECONDS:
                _dbfs_client_cache.move_to_end(cache_key)
                return client
            del _dbfs_client_cache[cache_key]

        client = sdk.WorkspaceClient(**client_kwargs)
        _dbfs_client_cache[cache_key] = (now, client)
        _dbfs_client_cache.move_to_end(cache_key)
        while len(_dbfs_client_cache) > _DBFS_CLIENT_CACHE_MAX_SIZE:
            _dbfs_client_cache.popitem(last=False)
        return client


def _cached_onelake_service_client(
    identity,
    data_lake,
    *,
    auth_type: str | None,
    credentials: dict[str, object],
):
    """Reuse thread-safe Azure clients without exposing credential material."""

    key_payload = json.dumps(
        {
            "endpoint": "https://onelake.dfs.fabric.microsoft.com",
            "auth_type": auth_type or "ambient",
            "credentials": credentials,
            "default_credential_factory": id(identity.DefaultAzureCredential),
            "service_principal_factory": id(identity.ClientSecretCredential),
            "service_client_factory": id(data_lake.DataLakeServiceClient),
        },
        sort_keys=True,
        default=str,
    )
    cache_key = hashlib.sha256(key_payload.encode("utf-8")).hexdigest()
    now = time.monotonic()
    expired: tuple[float, object, object] | None = None
    evicted: list[tuple[float, object, object]] = []
    with _onelake_client_cache_lock:
        cached = _onelake_client_cache.get(cache_key)
        if cached is not None:
            created_at, client, _credential = cached
            if now - created_at < _ONELAKE_CLIENT_CACHE_TTL_SECONDS:
                _onelake_client_cache.move_to_end(cache_key)
                return client
            expired = _onelake_client_cache.pop(cache_key)

        credential = (
            identity.ClientSecretCredential(
                tenant_id=credentials["tenant_id"],
                client_id=credentials["client_id"],
                client_secret=credentials["client_secret"],
            )
            if auth_type == "service_principal"
            else identity.DefaultAzureCredential()
        )
        client = data_lake.DataLakeServiceClient(
            account_url="https://onelake.dfs.fabric.microsoft.com",
            credential=credential,
        )
        _onelake_client_cache[cache_key] = (now, client, credential)
        _onelake_client_cache.move_to_end(cache_key)
        while len(_onelake_client_cache) > _ONELAKE_CLIENT_CACHE_MAX_SIZE:
            _key, value = _onelake_client_cache.popitem(last=False)
            evicted.append(value)

    for _created_at, stale_client, stale_credential in [
        *([expired] if expired is not None else []),
        *evicted,
    ]:
        _close_if_supported(stale_client)
        _close_if_supported(stale_credential)
    return client


def _close_if_supported(value: object) -> None:
    close = getattr(value, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            # Cache cleanup is best effort and must not mask credential updates.
            pass


def _create_filesystem(
    binding: StorageBinding,
    *,
    uri: str | None,
    auth_type: str | None,
    credentials: dict[str, object],
):
    options = binding.options
    if binding.provider in {"s3", "minio"}:
        fsspec = importlib.import_module("fsspec")
        kwargs: dict[str, object] = {}
        if binding.auth_mode == "anonymous":
            kwargs["anon"] = True
        if auth_type == "aws_shared_profile":
            kwargs["profile"] = credentials.get("profile_name")
        elif auth_type == "access_key":
            kwargs.update(
                {
                    "key": credentials.get("access_key_id"),
                    "secret": credentials.get("secret_access_key"),
                }
            )
            if credentials.get("session_token"):
                kwargs["token"] = credentials["session_token"]
        client_kwargs: dict[str, object] = {}
        if options.get("region"):
            client_kwargs["region_name"] = options["region"]
        if binding.provider == "minio":
            client_kwargs["endpoint_url"] = options["endpoint_url"]
            client_kwargs["verify"] = options.get("verify_tls", True)
            kwargs["config_kwargs"] = {
                "s3": {
                    "addressing_style": options.get("addressing_style", "path")
                }
            }
        if client_kwargs:
            kwargs["client_kwargs"] = client_kwargs
        if options.get("requester_pays"):
            kwargs["requester_pays"] = True
        return fsspec.filesystem("s3", **kwargs)

    if binding.provider == "adls":
        if binding.auth_mode == "anonymous":
            raise StorageConfigurationError("ADLS anonymous mode is not supported")
        kwargs = {}
        account_name = str(
            options.get("account_name") or _adls_account_name(uri) or ""
        )
        if account_name:
            kwargs["account_name"] = account_name
        if auth_type == "service_principal":
            kwargs.update(
                {
                    "tenant_id": credentials.get("tenant_id"),
                    "client_id": credentials.get("client_id"),
                    "client_secret": credentials.get("client_secret"),
                    "account_name": credentials.get("account_name")
                    or kwargs.get("account_name"),
                }
            )
        elif auth_type == "sas":
            kwargs.update(
                {
                    "sas_token": credentials.get("sas_token"),
                    "account_name": credentials.get("account_name")
                    or kwargs.get("account_name"),
                }
            )
        elif auth_type == "account_key":
            kwargs.update(
                {
                    "account_key": credentials.get("account_key"),
                    "account_name": credentials.get("account_name")
                    or kwargs.get("account_name"),
                }
            )
        _validate_adls_account_match(uri, str(kwargs.get("account_name") or ""))
        fsspec = importlib.import_module("fsspec")
        return fsspec.filesystem("abfs", **kwargs)

    fsspec = importlib.import_module("fsspec")
    kwargs = {}
    if binding.auth_mode == "anonymous":
        kwargs["token"] = "anon"
    elif auth_type == "service_account":
        kwargs["token"] = credentials.get("service_account_json")
    if options.get("project_id"):
        kwargs["project"] = options["project_id"]
    if options.get("billing_project"):
        kwargs["requester_pays"] = options["billing_project"]
    return fsspec.filesystem("gcs", **kwargs)


def _adls_account_name(uri: str | None) -> str | None:
    if not uri:
        return None
    hostname = urlsplit(uri).hostname
    suffix = ".dfs.core.windows.net"
    if not hostname or not hostname.lower().endswith(suffix):
        return None
    account_name = hostname[: -len(suffix)].strip()
    return account_name or None


def _validate_adls_account_match(
    uri: str | None, configured_account_name: str
) -> None:
    uri_account_name = _adls_account_name(uri)
    if (
        uri_account_name
        and configured_account_name
        and uri_account_name.lower() != configured_account_name.lower()
    ):
        raise StorageConfigurationError(
            "ADLS account_name does not match the account encoded in the URI"
        )


def _validate_provider_configuration(
    binding: StorageBinding,
    uri: str | None,
) -> None:
    if binding.provider == "onelake":
        if uri:
            validate_storage_uri(uri, "onelake")
        return
    if binding.provider != "adls":
        return
    _validate_adls_account_match(
        uri,
        str(binding.options.get("account_name") or ""),
    )


def _load_object(value: str | None) -> dict[str, object]:
    try:
        payload = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
