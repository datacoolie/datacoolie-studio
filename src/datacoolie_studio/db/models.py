from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text
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


class EnvironmentSource(Base):
    __tablename__ = "environment_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    environment_id: Mapped[int] = mapped_column(ForeignKey("environments.id", ondelete="CASCADE"), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    source_config_json: Mapped[str | None] = mapped_column(Text, nullable=True)
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


class MetadataSourceSnapshot(Base):
    __tablename__ = "metadata_source_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("environment_sources.id", ondelete="CASCADE"), nullable=False)
    source_revision_json: Mapped[str] = mapped_column(Text, nullable=False)
    editor_document_json: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class CodeArtifactSnapshot(Base):
    __tablename__ = "code_artifact_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("environment_sources.id", ondelete="CASCADE"), nullable=False)
    source_revision_json: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_manifest_json: Mapped[str] = mapped_column(Text, nullable=False)
    module_index_json: Mapped[str] = mapped_column(Text, nullable=False)
    diagnostics_json: Mapped[str] = mapped_column(Text, nullable=False)
    analyzer_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class LineageSnapshot(Base):
    __tablename__ = "lineage_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    environment_id: Mapped[int] = mapped_column(ForeignKey("environments.id", ondelete="CASCADE"), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    graph_json: Mapped[str] = mapped_column(Text, nullable=False)
    analyzer_version: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class SourceRevision(Base):
    __tablename__ = "source_revisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("environment_sources.id", ondelete="CASCADE"), nullable=False, unique=True)
    source_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    revision_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class SyncJob(Base):
    __tablename__ = "sync_jobs"

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

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("environment_sources.id", ondelete="CASCADE"), nullable=False)
    file_uri: Mapped[str] = mapped_column(Text, nullable=False)
    file_kind: Mapped[str] = mapped_column(String(50), nullable=False)
    revision_json: Mapped[str] = mapped_column(Text, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    log_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    run_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


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
