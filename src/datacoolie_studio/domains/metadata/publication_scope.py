"""Frozen metadata storage identity and guarded client construction."""

import json
from contextlib import contextmanager

from datacoolie_studio.db.models import EnvironmentSource

from datacoolie_studio.domains.credentials.store import credential_profile_guards, KeyringCredentialSecretStore
from datacoolie_studio.domains.metadata.errors import MetadataConflictError
from datacoolie_studio.domains.sources.storage_binding import binding_from_source, binding_to_dict
from datacoolie_studio.domains.storage.factory import effective_storage_scope
from datacoolie_studio.domains.storage.identity import configured_storage_scope
from datacoolie_studio.domains.storage.errors import StorageConfigurationError, StorageAuthenticationError


def publication_scope(session, source, secret_store=None):
    try:
        return effective_storage_scope(binding_from_source(source), uri=source.uri,
            session=session, secret_store=secret_store or KeyringCredentialSecretStore())
    except (StorageConfigurationError, StorageAuthenticationError) as exc:
        raise MetadataConflictError("Storage scope cannot be proven; configure an explicit host/account") from exc


@contextmanager
def source_resolution(session, source):
    with credential_profile_guards(session, [source.credential_profile_id]):
        yield


def metadata_descriptor(session, source, secret_store=None, *, descriptor=None):
    result = dict(descriptor or {})
    owner = getattr(source, "_publication_storage_owner", source)
    result.update({"resolution_version": 1, "storage_source_id": owner.id if owner.id > 0 else None,
        "storage_source_uri": owner.uri, "file_uri": source.uri,
        "effective_scope": publication_scope(session, source, secret_store)})
    if owner.id > 0:
        result["storage_source_binding"] = binding_to_dict(owner)
        result["storage_source_config"] = json.loads(owner.source_config_json or "{}")
    return result


def validate_metadata_scope(session, source, item, secret_store=None, *, finalizing=False):
    descriptor = json.loads(item.target_descriptor_json or "{}")
    binding = json.loads(item.binding_snapshot_json or "{}")
    if binding_to_dict(source) != binding or source.uri != item.target_uri or not source.enabled:
        raise MetadataConflictError("Metadata source or binding changed; abandon and reprepare")
    owner_id = descriptor.get("storage_source_id")
    if owner_id:
        owner = session.get(EnvironmentSource, owner_id, populate_existing=True)
        if owner is None or owner.environment_id != source.environment_id or not owner.enabled or owner.uri != descriptor.get("storage_source_uri"):
            raise MetadataConflictError("Metadata storage owner changed; abandon and reprepare")
        if "storage_source_binding" in descriptor and (
            binding_to_dict(owner) != descriptor["storage_source_binding"]
            or json.loads(owner.source_config_json or "{}") != descriptor.get("storage_source_config")
        ):
            raise MetadataConflictError("Metadata storage owner configuration changed; abandon and reprepare")
    if finalizing:
        try:
            current = configured_storage_scope(session, binding_from_source(source), uri=source.uri)
        except StorageConfigurationError as exc:
            raise MetadataConflictError("Metadata provider scope cannot be proven during finalization") from exc
    else:
        current = publication_scope(session, source, secret_store)
    frozen = descriptor.get("effective_scope")
    if frozen is None:
        if binding.get("provider") in {"dbfs", "adls"}:
            raise MetadataConflictError("Legacy operation has no proven provider scope; abandon and reprepare")
        return
    if current != frozen or descriptor.get("file_uri") != item.target_uri:
        raise MetadataConflictError("Metadata provider scope changed; abandon and reprepare")
