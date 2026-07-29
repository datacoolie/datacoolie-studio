from __future__ import annotations


class StorageError(RuntimeError):
    code = "storage_error"

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message)
        self.provider = provider


class ProviderDependencyMissing(StorageError):
    code = "provider_dependency_missing"

    def __init__(self, provider: str, install_command: str) -> None:
        super().__init__(
            f"{provider.upper()} storage support is not installed. Run: {install_command}",
            provider=provider,
        )
        self.install_command = install_command


class StorageConfigurationError(StorageError, ValueError):
    code = "storage_configuration_invalid"


class StorageAuthenticationError(StorageError):
    code = "storage_authentication_failed"


class StorageNotFoundError(StorageError, FileNotFoundError):
    code = "storage_object_not_found"


class StorageConflictError(StorageError):
    code = "storage_revision_conflict"

    def __init__(self, uri: str, message: str = "Storage revision conflict") -> None:
        super().__init__(message)
        self.uri = uri


class StorageAccessError(StorageError):
    code = "storage_access_failed"


class StorageWriteUnsupported(StorageError):
    code = "storage_write_unsupported"
