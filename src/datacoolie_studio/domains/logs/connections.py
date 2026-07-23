from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from threading import Condition
from typing import Any, Iterator

import duckdb


class ManagedDuckDBConnection:
    def __init__(self, manager: AnalyticsConnectionManager, connection: Any) -> None:
        self._manager = manager
        self._connection = connection
        self._closed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def __enter__(self) -> ManagedDuckDBConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._connection.close()
        finally:
            self._manager.release()


class AnalyticsConnectionManager:
    """Coordinate in-process DuckDB connections with exclusive cache maintenance."""

    def __init__(self) -> None:
        self._condition = Condition()
        self._active_connections = 0
        self._maintenance_active = False

    def connect(self, path: Path, *, read_only: bool = False) -> ManagedDuckDBConnection:
        with self._condition:
            while self._maintenance_active:
                self._condition.wait()
            self._active_connections += 1
        connection = None
        try:
            connection = duckdb.connect(database=str(path), read_only=read_only)
            connection.execute("SET enable_progress_bar = false")
        except Exception:
            if connection is not None:
                connection.close()
            self.release()
            raise
        return ManagedDuckDBConnection(self, connection)

    def release(self) -> None:
        with self._condition:
            self._active_connections -= 1
            if self._active_connections < 0:
                self._active_connections = 0
                raise RuntimeError("Analytics connection manager released too many connections")
            if self._active_connections == 0:
                self._condition.notify_all()

    @contextmanager
    def exclusive_maintenance(self) -> Iterator[None]:
        with self._condition:
            while self._maintenance_active:
                self._condition.wait()
            self._maintenance_active = True
            while self._active_connections:
                self._condition.wait()
        try:
            yield
        finally:
            with self._condition:
                self._maintenance_active = False
                self._condition.notify_all()


analytics_connections = AnalyticsConnectionManager()
