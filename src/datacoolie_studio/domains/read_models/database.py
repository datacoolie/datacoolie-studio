from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from sqlalchemy import DateTime, Index, Integer, String, Text, UniqueConstraint, create_engine, event, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool

from datacoolie_studio.core.config import result_cache_url


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ResultCacheBase(DeclarativeBase):
    pass


class ResultCacheEntry(ResultCacheBase):
    __tablename__ = "result_cache_entries"
    __table_args__ = (
        UniqueConstraint(
            "environment_id",
            "namespace",
            "parameters_fingerprint",
            name="uq_result_cache_logical_identity",
        ),
        Index(
            "ix_result_cache_lookup",
            "environment_id",
            "namespace",
            "parameters_fingerprint",
            "input_fingerprint",
            "producer_version",
        ),
        Index(
            "ix_result_cache_family",
            "environment_id",
            "namespace_family",
        ),
        Index(
            "ix_result_cache_environment_policy",
            "environment_id",
            "computed_at",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    environment_id: Mapped[int] = mapped_column(Integer, nullable=False)
    namespace: Mapped[str] = mapped_column(String(100), nullable=False)
    namespace_family: Mapped[str] = mapped_column(String(100), nullable=False)
    parameters_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    producer_version: Mapped[str] = mapped_column(String(100), nullable=False)
    generation_token: Mapped[str] = mapped_column(String(200), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    payload_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class ResultCacheGeneration(ResultCacheBase):
    __tablename__ = "result_cache_generations"

    scope_key: Mapped[str] = mapped_column(String(200), primary_key=True)
    generation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


_lock = RLock()
_engine: Engine | None = None
_engine_url: str | None = None
_session_factory: sessionmaker[Session] | None = None


def get_result_cache_engine() -> Engine:
    global _engine, _engine_url, _session_factory
    url = result_cache_url()
    with _lock:
        if _engine is not None and _engine_url == url:
            return _engine
        if _engine is not None:
            _engine.dispose()
        database = make_url(url).database
        if database:
            Path(database).expanduser().parent.mkdir(parents=True, exist_ok=True)
        kwargs: dict = {"connect_args": {"check_same_thread": False}}
        if url == "sqlite://":
            kwargs["poolclass"] = StaticPool
        engine = create_engine(url, **kwargs)
        _configure_sqlite(engine)
        _initialize_schema(engine)
        _engine = engine
        _engine_url = url
        _session_factory = sessionmaker(bind=engine, expire_on_commit=False)
        return engine


def create_result_cache_session() -> Session:
    if _session_factory is None:
        get_result_cache_engine()
    assert _session_factory is not None
    return _session_factory()


def reset_result_cache_engine() -> None:
    global _engine, _engine_url, _session_factory
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _engine_url = None
        _session_factory = None


def _configure_sqlite(engine: Engine) -> None:
    @event.listens_for(engine, "connect")
    def set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def _initialize_schema(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text("PRAGMA auto_vacuum=INCREMENTAL"))
        if str(engine.url) != "sqlite://":
            connection.execute(text("PRAGMA journal_mode=WAL"))
    ResultCacheBase.metadata.create_all(engine)
