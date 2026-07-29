from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
import threading
from typing import TypeVar

_Input = TypeVar("_Input")
_Output = TypeVar("_Output")


_PROVIDER_IO_LIMITS = {
    "local": 1,
    "dbfs": 8,
    "s3": 8,
    "minio": 8,
    "adls": 8,
    "onelake": 8,
    "gcs": 8,
}
_provider_semaphores = {
    provider: threading.BoundedSemaphore(limit)
    for provider, limit in _PROVIDER_IO_LIMITS.items()
}
_storage_io_context = threading.local()


def storage_io_limit(adapter: object) -> int:
    """Return a conservative per-sync cap for remote storage requests."""

    provider = str(getattr(adapter, "provider", "local")).lower()
    return _PROVIDER_IO_LIMITS.get(provider, 4)


def map_storage_io(
    adapter: object,
    function: Callable[[_Input], _Output],
    items: Sequence[_Input],
) -> list[_Output]:
    """Run independent storage requests concurrently while preserving order."""

    if not items:
        return []
    workers = min(storage_io_limit(adapter), len(items))
    provider = str(getattr(adapter, "provider", "local")).lower()
    semaphore = _provider_semaphores.setdefault(
        provider,
        threading.BoundedSemaphore(_PROVIDER_IO_LIMITS.get(provider, 4)),
    )

    def governed(item: _Input) -> _Output:
        with semaphore:
            active = getattr(_storage_io_context, "providers", set())
            _storage_io_context.providers = {*active, provider}
            try:
                return function(item)
            finally:
                _storage_io_context.providers = active

    if workers <= 1:
        return [governed(item) for item in items]
    with ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix=f"{getattr(adapter, 'provider', 'storage')}-io",
    ) as executor:
        return list(executor.map(governed, items))


def storage_io_context_active(adapter: object) -> bool:
    """Whether this thread already owns the provider's global I/O slot."""

    provider = str(getattr(adapter, "provider", "local")).lower()
    return provider in getattr(_storage_io_context, "providers", set())
