from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class StudioSetting(Base):
    __tablename__ = "studio_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class StudioModuleState(Base):
    """Studio-level enable/disable state for a capability module.

    Rows are written only when a module is toggled away from its catalog
    default. Absent rows resolve to the module's ``default_enabled`` value.
    """

    __tablename__ = "studio_module_states"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    environments: Mapped[list[Environment]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )
    reference_mappings: Mapped[list[ProjectReferenceMapping]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
    )


class Environment(Base):
    __tablename__ = "environments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    project: Mapped[Project] = relationship(back_populates="environments")
    sources: Mapped[list[EnvironmentSource]] = relationship(
        back_populates="environment",
        cascade="all, delete-orphan",
    )
    source_registrations: Mapped[list[SourceRegistration]] = relationship(
        back_populates="environment",
        cascade="all, delete-orphan",
    )


class CredentialProfile(Base):
    __tablename__ = "credential_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    provider: Mapped[str] = mapped_column(String(20), nullable=False)
    auth_type: Mapped[str] = mapped_column(String(50), nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    secret_state: Mapped[str] = mapped_column(String(20), nullable=False)
    masked_summary_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    sources: Mapped[list[EnvironmentSource]] = relationship(
        back_populates="credential_profile"
    )


class ProjectReferenceMapping(Base):
    __tablename__ = "project_reference_mappings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    reference_type: Mapped[str] = mapped_column(String(50), nullable=False)
    reference_normalized_value: Mapped[str] = mapped_column(Text, nullable=False)
    reference_signature_json: Mapped[str] = mapped_column(Text, nullable=False)
    target_identifier_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    target_normalized_value: Mapped[str] = mapped_column(Text, nullable=False)
    target_display_value: Mapped[str] = mapped_column(Text, nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    project: Mapped[Project] = relationship(back_populates="reference_mappings")


class SourceRegistration(Base):
    __tablename__ = "source_registrations"
    __table_args__ = (
        UniqueConstraint(
            "environment_id",
            "purpose",
            "identity_key",
            name="uq_source_registration_identity",
        ),
        Index("ix_source_registrations_environment", "environment_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    environment_id: Mapped[int] = mapped_column(
        ForeignKey("environments.id", ondelete="CASCADE"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(String(50), nullable=False)
    input_uri: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_uri: Mapped[str] = mapped_column(Text, nullable=False)
    input_locations_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    canonical_locations_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    identity_key: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    environment: Mapped[Environment] = relationship(back_populates="source_registrations")
    sources: Mapped[list[EnvironmentSource]] = relationship(back_populates="registration")


class EnvironmentSource(Base):
    __tablename__ = "environment_sources"
    __table_args__ = (
        Index("ix_environment_sources_storage_provider", "storage_provider"),
        Index("ix_environment_sources_credential_profile", "credential_profile_id"),
        Index("ix_environment_sources_registration", "registration_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    environment_id: Mapped[int] = mapped_column(ForeignKey("environments.id", ondelete="CASCADE"), nullable=False)
    registration_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_registrations.id", ondelete="SET NULL"), nullable=True
    )
    source_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    source_config_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_provider: Mapped[str] = mapped_column(
        String(20), nullable=False, default="local", server_default="local"
    )
    storage_auth_mode: Mapped[str] = mapped_column(
        String(30), nullable=False, default="none", server_default="none"
    )
    credential_profile_id: Mapped[str | None] = mapped_column(
        ForeignKey("credential_profiles.id", ondelete="RESTRICT"), nullable=True
    )
    storage_config_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    read_check_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    read_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    read_check_result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    sync_schedule_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    sync_interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_scheduled_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    environment: Mapped[Environment] = relationship(back_populates="sources")
    registration: Mapped[SourceRegistration | None] = relationship(back_populates="sources")
    credential_profile: Mapped[CredentialProfile | None] = relationship(
        back_populates="sources"
    )


class MetadataMaterialization(Base):
    __tablename__ = "metadata_materializations"
    __table_args__ = (UniqueConstraint("source_id", name="uq_metadata_materialization_source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("environment_sources.id", ondelete="CASCADE"), nullable=False)
    source_revision_json: Mapped[str] = mapped_column(Text, nullable=False)
    normalizer_version: Mapped[str] = mapped_column(String(100), nullable=False)
    materialization_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    editor_document_json: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    materialized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class CodeArtifactMaterialization(Base):
    __tablename__ = "code_artifact_materializations"
    __table_args__ = (UniqueConstraint("source_id", name="uq_code_artifact_materialization_source"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("environment_sources.id", ondelete="CASCADE"), nullable=False)
    source_revision_json: Mapped[str] = mapped_column(Text, nullable=False)
    materialization_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_manifest_json: Mapped[str] = mapped_column(Text, nullable=False)
    module_index_json: Mapped[str] = mapped_column(Text, nullable=False)
    diagnostics_json: Mapped[str] = mapped_column(Text, nullable=False)
    analyzer_version: Mapped[str] = mapped_column(String(50), nullable=False)
    materialized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class SourceObservation(Base):
    __tablename__ = "source_observations"
    __table_args__ = (
        Index(
            "ix_source_observations_due",
            "next_observation_at",
            "lease_expires_at",
        ),
    )

    source_id: Mapped[int] = mapped_column(
        ForeignKey("environment_sources.id", ondelete="CASCADE"),
        primary_key=True,
    )
    last_outcome: Mapped[str] = mapped_column(
        String(30), default="never", nullable=False
    )
    pending_changes: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    observed_revision_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_succeeded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    inventory_metrics_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    unchanged_streak: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    failure_streak: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_observation_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    automatic_observation_paused_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_owner: Mapped[str | None] = mapped_column(String(100), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )


class SyncJob(Base):
    __tablename__ = "sync_jobs"
    __table_args__ = (
        Index("ix_sync_jobs_retention", "source_id", "status", "completed_at", "started_at", "id"),
        Index(
            "uq_sync_jobs_running_source",
            "source_id",
            unique=True,
            sqlite_where=text("status = 'running'"),
            postgresql_where=text("status = 'running'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    environment_id: Mapped[int] = mapped_column(ForeignKey("environments.id", ondelete="CASCADE"), nullable=False)
    source_id: Mapped[int] = mapped_column(ForeignKey("environment_sources.id", ondelete="CASCADE"), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    job_type: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LogFileManifest(Base):
    __tablename__ = "log_file_manifest"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "file_kind",
            "file_uri",
            name="uq_log_file_manifest_source_kind_uri",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("environment_sources.id", ondelete="CASCADE"), nullable=False)
    file_uri: Mapped[str] = mapped_column(Text, nullable=False)
    file_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    partition_value: Mapped[date | None] = mapped_column(Date, nullable=True)
    partition_format: Mapped[str | None] = mapped_column(String(100), nullable=True)
    revision_json: Mapped[str] = mapped_column(Text, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    log_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    run_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class LogStreamState(Base):
    __tablename__ = "log_stream_states"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "stream_kind",
            name="uq_log_stream_states_source_kind",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("environment_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    stream_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    root_uri: Mapped[str] = mapped_column(Text, nullable=False)
    partition_format: Mapped[str | None] = mapped_column(String(100), nullable=True)
    partition_granularity: Mapped[str | None] = mapped_column(String(20), nullable=True)
    checkpoint_partition_value: Mapped[date | None] = mapped_column(Date, nullable=True)
    boundary_last_modified: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_scanned_partition_value: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    layout_status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class MetadataEditorDraft(Base):
    __tablename__ = "metadata_editor_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("environment_sources.id", ondelete="CASCADE"), nullable=False)
    base_revision_json: Mapped[str] = mapped_column(Text, nullable=False)
    editor_document_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class EnvironmentMetadataEditorDraft(Base):
    __tablename__ = "environment_metadata_editor_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    environment_id: Mapped[int] = mapped_column(ForeignKey("environments.id", ondelete="CASCADE"), nullable=False, unique=True)
    base_revision_json: Mapped[str] = mapped_column(Text, nullable=False)
    editor_document_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class MetadataValidationResult(Base):
    __tablename__ = "metadata_validation_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("environment_sources.id", ondelete="CASCADE"), nullable=False)
    base_revision_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    result_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class MetadataSaveEvent(Base):
    __tablename__ = "metadata_save_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("environment_sources.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_revision_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    saved_revision_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    backup_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class MetadataBackup(Base):
    __tablename__ = "metadata_backups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, nullable=False)
    environment_id: Mapped[int] = mapped_column(ForeignKey("environments.id", ondelete="CASCADE"), nullable=False)
    source_id: Mapped[int] = mapped_column(ForeignKey("environment_sources.id", ondelete="CASCADE"), nullable=False)
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    backup_path: Mapped[str] = mapped_column(Text, nullable=False)
    source_revision_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    saved_revision_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    save_event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
