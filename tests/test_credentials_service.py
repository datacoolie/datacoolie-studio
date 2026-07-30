from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from datacoolie_studio.db.models import (
    CredentialProfile,
    Environment,
    EnvironmentSource,
    Project,
    SourceObservation,
)
from datacoolie_studio.db.session import create_session, init_db
from datacoolie_studio.domains.credentials import service
from datacoolie_studio.domains.credentials.store import SecretNotFound
from datacoolie_studio.domains.storage.binding import StorageBinding
from datacoolie_studio.domains.storage.factory import create_storage_adapter


class MemorySecretStore:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.values: dict[str, dict[str, object]] = {}

    def is_available(self) -> bool:
        return self.available

    def set(self, secret_ref: str, secret: Mapping[str, object]) -> None:
        self.values[secret_ref] = dict(secret)

    def get(self, secret_ref: str) -> dict[str, object]:
        try:
            return dict(self.values[secret_ref])
        except KeyError as exc:
            raise SecretNotFound(secret_ref) from exc

    def delete(self, secret_ref: str) -> None:
        self.values.pop(secret_ref, None)


@pytest.fixture
def session(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("DATACOOLIE_STUDIO_DB", str(tmp_path / "studio.db"))
    init_db()
    value = create_session()
    try:
        yield value
    finally:
        value.close()


def test_secret_is_external_and_never_returned(session):
    store = MemorySecretStore()

    result = service.create_profile(
        session,
        name="Production S3",
        provider="s3",
        auth_type="access_key",
        config={"access_key_id": "AKIA-EXAMPLE"},
        secret={
            "secret_access_key": "super-secret-value",
            "session_token": "temporary-token",
        },
        secret_store=store,
    )

    profile = session.scalar(select(CredentialProfile))
    assert profile is not None
    persisted = " ".join(
        [
            profile.config_json,
            profile.masked_summary_json,
            profile.secret_ref or "",
        ]
    )
    response = json.dumps(result, default=str)
    assert "super-secret-value" not in persisted
    assert "temporary-token" not in persisted
    assert "super-secret-value" not in response
    assert "temporary-token" not in response
    assert store.values[profile.id]["secret_access_key"] == "super-secret-value"
    assert result["secret_state"] == "present"


def test_databricks_profiles_support_unified_auth_pat_and_oauth(session):
    store = MemorySecretStore()

    unified = service.create_profile(
        session,
        name="Databricks profile",
        provider="dbfs",
        auth_type="databricks_profile",
        config={"profile": "analytics"},
        secret=None,
        secret_store=store,
    )
    pat = service.create_profile(
        session,
        name="Databricks PAT",
        provider="dbfs",
        auth_type="pat",
        config={"host": "https://workspace.cloud.databricks.com"},
        secret={"token": "write-only-token"},
        secret_store=store,
    )
    oauth = service.create_profile(
        session,
        name="Databricks OAuth",
        provider="dbfs",
        auth_type="oauth_m2m",
        config={
            "host": "https://workspace.cloud.databricks.com",
            "client_id": "client-id",
        },
        secret={"client_secret": "write-only-client-secret"},
        secret_store=store,
    )

    assert unified["provider"] == "dbfs"
    assert pat["auth_type"] == "pat"
    assert oauth["auth_type"] == "oauth_m2m"
    assert "write-only-token" not in json.dumps(pat, default=str)
    assert "write-only-client-secret" not in json.dumps(oauth, default=str)
    assert set(service.capabilities()["dbfs"]) == {
        "databricks_profile",
        "oauth_m2m",
        "pat",
    }


def test_onelake_service_principal_is_write_only_and_provider_specific(session):
    store = MemorySecretStore()

    profile = service.create_profile(
        session,
        name="Fabric automation",
        provider="onelake",
        auth_type="service_principal",
        config={"tenant_id": "tenant-id", "client_id": "client-id"},
        secret={"client_secret": "write-only-secret"},
        secret_store=store,
    )

    assert service.capabilities()["onelake"] == ["service_principal"]
    assert profile["provider"] == "onelake"
    assert "write-only-secret" not in json.dumps(profile, default=str)
    assert store.values[str(profile["id"])] == {
        "client_secret": "write-only-secret"
    }
    with pytest.raises(service.CredentialValidationError, match="Unsupported"):
        service.create_profile(
            session,
            name="Invalid Fabric SAS",
            provider="onelake",
            auth_type="sas",
            config={},
            secret={"sas_token": "not-supported"},
            secret_store=store,
        )


def test_databricks_profile_rejects_non_https_host(session):
    with pytest.raises(service.CredentialValidationError, match="HTTPS"):
        service.create_profile(
            session,
            name="Unsafe Databricks",
            provider="dbfs",
            auth_type="pat",
            config={"host": "http://workspace.example.com"},
            secret={"token": "token"},
            secret_store=MemorySecretStore(),
        )


def test_rotation_invalidates_referencing_source_validation(session):
    store = MemorySecretStore()
    profile = service.create_profile(
        session,
        name="MinIO",
        provider="minio",
        auth_type="access_key",
        config={"access_key_id": "old-id"},
        secret={"secret_access_key": "old-secret"},
        secret_store=store,
    )
    project = Project(name="project")
    environment = Environment(name="dev", project=project)
    source = EnvironmentSource(
        environment=environment,
        source_kind="metadata",
        uri="s3://bucket/metadata.json",
        storage_provider="minio",
        storage_auth_mode="credential_profile",
        credential_profile_id=str(profile["id"]),
        read_check_status="ok",
        read_check_result_json='{"status":"ok"}',
    )
    session.add(source)
    session.commit()
    session.add(
        SourceObservation(
            source_id=source.id,
            last_outcome="error",
            error_json='{"message":"denied"}',
            failure_streak=3,
            automatic_observation_paused_at=datetime(
                2026, 7, 30, 12, 0, tzinfo=timezone.utc
            ),
            next_observation_at=None,
        )
    )
    session.commit()

    updated = service.update_profile(
        session,
        str(profile["id"]),
        name=None,
        config={"access_key_id": "new-id"},
        secret={"secret_access_key": "new-secret"},
        secret_store=store,
    )

    session.refresh(source)
    assert updated["version"] == 2
    assert source.read_check_status is None
    assert source.read_check_result_json is None
    observation = session.get(SourceObservation, source.id)
    assert observation.automatic_observation_paused_at is None
    assert observation.failure_streak == 0
    assert observation.last_outcome == "never"
    assert observation.error_json is None
    assert store.values[str(profile["id"])]["secret_access_key"] == "new-secret"


def test_edit_preserves_secret_without_reading_secret_store(session):
    store = MemorySecretStore()
    profile = service.create_profile(
        session,
        name="Production S3",
        provider="s3",
        auth_type="access_key",
        config={"access_key_id": "old-id"},
        secret={"secret_access_key": "preserved-secret"},
        secret_store=store,
    )

    class WriteOnlyDuringEditStore(MemorySecretStore):
        def get(self, secret_ref: str) -> dict[str, object]:
            raise AssertionError("Editing config must not read the existing secret")

    write_only_store = WriteOnlyDuringEditStore(available=False)
    write_only_store.values = store.values
    updated = service.update_profile(
        session,
        str(profile["id"]),
        name="Renamed S3",
        config={"access_key_id": "new-id"},
        secret=None,
        secret_store=write_only_store,
    )

    detail = service.get_profile(session, str(profile["id"]))
    assert updated["name"] == "Renamed S3"
    assert detail["config"] == {"access_key_id": "new-id"}
    assert "config" not in service.list_profiles(session)[0]
    assert store.values[str(profile["id"])]["secret_access_key"] == "preserved-secret"
    assert "preserved-secret" not in json.dumps(detail, default=str)


def test_delete_rejects_referenced_profile(session):
    store = MemorySecretStore()
    profile = service.create_profile(
        session,
        name="AWS profile",
        provider="s3",
        auth_type="aws_shared_profile",
        config={"profile_name": "analytics"},
        secret=None,
        secret_store=store,
    )
    project = Project(name="project")
    environment = Environment(name="dev", project=project)
    session.add(
        EnvironmentSource(
            environment=environment,
            source_kind="logs",
            uri="s3://bucket/logs",
            storage_provider="s3",
            storage_auth_mode="credential_profile",
            credential_profile_id=str(profile["id"]),
        )
    )
    session.commit()

    with pytest.raises(service.CredentialProfileInUse):
        service.delete_profile(
            session, str(profile["id"]), secret_store=store
        )


def test_names_are_unique_case_insensitively(session):
    store = MemorySecretStore()
    service.create_profile(
        session,
        name="Shared AWS",
        provider="s3",
        auth_type="aws_shared_profile",
        config={"profile_name": "default"},
        secret=None,
        secret_store=store,
    )

    with pytest.raises(service.CredentialProfileConflict):
        service.create_profile(
            session,
            name="shared aws",
            provider="s3",
            auth_type="aws_shared_profile",
            config={"profile_name": "other"},
            secret=None,
            secret_store=store,
        )


def test_gcs_service_account_is_validated_and_redacted(session):
    store = MemorySecretStore()
    document = {
        "type": "service_account",
        "project_id": "demo",
        "client_email": "studio@example.invalid",
        "private_key": "private-key-material",
    }

    result = service.create_profile(
        session,
        name="GCS",
        provider="gcs",
        auth_type="service_account",
        config={},
        secret={"service_account_json": document},
        secret_store=store,
    )

    assert result["masked_summary"]["service_account_email"] == "st***@example.invalid"
    assert "private-key-material" not in json.dumps(result, default=str)


def test_storage_adapter_resolution_does_not_commit_caller_session(
    session,
    monkeypatch,
):
    store = MemorySecretStore()
    profile = service.create_profile(
        session,
        name="Read-only resolution",
        provider="s3",
        auth_type="aws_shared_profile",
        config={"profile_name": "default"},
        secret=None,
        secret_store=store,
    )
    pending = Project(name="must-remain-pending")
    session.add(pending)

    class FakeFsspec:
        @staticmethod
        def filesystem(_protocol: str, **_kwargs):
            return object()

    monkeypatch.setattr(
        "datacoolie_studio.domains.storage.factory._module_available",
        lambda _name: True,
    )
    monkeypatch.setattr(
        "datacoolie_studio.domains.storage.factory.importlib.import_module",
        lambda name: FakeFsspec if name == "fsspec" else __import__(name),
    )

    create_storage_adapter(
        StorageBinding(
            provider="s3",
            auth_mode="credential_profile",
            credential_profile_id=str(profile["id"]),
        ),
        uri="s3://bucket/prefix",
        session=session,
        secret_store=store,
    )

    observer = create_session()
    try:
        assert observer.scalar(
            select(Project).where(Project.name == "must-remain-pending")
        ) is None
    finally:
        observer.close()
