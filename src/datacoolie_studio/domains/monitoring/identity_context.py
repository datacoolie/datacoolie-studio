"""Request lifecycle for Assets-owned destination identity."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from datacoolie_studio.domains.assets.identity_catalog import (
    identity_version,
)
from datacoolie_studio.domains.monitoring.read_models.common import AnalyticsContext
from datacoolie_studio.domains.read_models.contracts import ResultCacheKey
from datacoolie_studio.domains.read_models.provider import result_cache_provider


class IdentityRevisionMismatch(RuntimeError):
    """A selected destination or in-flight result belongs to obsolete inputs."""


@dataclass
class DestinationIdentityRequest:
    session: Session
    environment_id: int
    analytics_context: AnalyticsContext
    version: str
    cache_generation: tuple[str, str]
    mapping: Any = None

    @classmethod
    def capture(cls, session: Session, environment_id: int, context: AnalyticsContext):
        return cls(session, environment_id, context, identity_version(session, environment_id, context), _cache_generation(environment_id))

    def prepare(self):
        if self.mapping is None:
            from datacoolie_studio.domains.assets.identity_mapping import (
                identity_map_context, IdentityMapGenerationMismatch, IdentityMapRevisionMismatch,
            )
            try:
                self.mapping = identity_map_context(self.session, self.environment_id, self.analytics_context)
            except (IdentityMapGenerationMismatch, IdentityMapRevisionMismatch) as exc:
                raise IdentityRevisionMismatch(str(exc)) from exc
            if self.mapping.version != self.version:
                raise IdentityRevisionMismatch("Destination identity changed while preparing the request")
            self.assert_current()
        return self.mapping

    def assert_current(self):
        if (_cache_generation(self.environment_id) != self.cache_generation
                or identity_version(self.session, self.environment_id, self.analytics_context) != self.version):
            raise IdentityRevisionMismatch("Published destination identity changed; refresh the selection")

    def require_version(self, expected: str | None):
        if expected and expected != self.version:
            raise IdentityRevisionMismatch("This destination selection has expired; select it from the refreshed registry")


def _cache_generation(environment_id: int) -> tuple[str, str]:
    store = result_cache_provider().store
    return tuple(store.generation(ResultCacheKey(
        environment_id=environment_id, namespace=namespace,
        parameters_fingerprint='identity-request-fence', input_fingerprint='',
        producer_version='identity-request-v1',
    )) for namespace in ('assets.identity-map', 'monitoring.page.maintenance'))
