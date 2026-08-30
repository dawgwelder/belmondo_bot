"""Small async boundary around stdlib sqlite3."""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TypeVar

T = TypeVar("T")


def _iter_sql_statements(source: str):
    buffer = ""
    for line in source.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            buffer = ""
            if statement:
                yield statement
    if buffer.strip():
        raise ValueError("incomplete SQL migration")


class SQLiteDatabase:
    """Serialize short SQLite operations in one worker thread."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="spy-sqlite",
        )
        self._closed = False

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=5.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        await self._submit(self._initialize_sync)

    def _initialize_sync(self) -> None:
        migrations_dir = Path(__file__).with_name("migrations")
        with self._connect() as connection:
            mode = connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if str(mode).lower() != "wal":
                raise RuntimeError(f"SQLite WAL mode unavailable: {mode}")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                )
                """
            )
            applied = {
                row[0]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations"
                ).fetchall()
            }
            for migration_path in sorted(migrations_dir.glob("[0-9][0-9][0-9]_*.sql")):
                version = int(migration_path.name.split("_", 1)[0])
                if version in applied:
                    continue
                source = migration_path.read_text(encoding="utf-8")
                connection.execute("BEGIN IMMEDIATE")
                try:
                    for statement in _iter_sql_statements(source):
                        connection.execute(statement)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) "
                        "VALUES (?, strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'))",
                        (version,),
                    )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise

    async def read(self, operation: Callable[[sqlite3.Connection], T]) -> T:
        return await self._submit(self._run_sync, operation, False)

    async def transaction(
        self,
        operation: Callable[[sqlite3.Connection], T],
        *,
        immediate: bool = False,
    ) -> T:
        return await self._submit(self._run_sync, operation, immediate)

    def _run_sync(
        self,
        operation: Callable[[sqlite3.Connection], T],
        immediate: bool,
    ) -> T:
        with self._connect() as connection:
            if not immediate:
                return operation(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                result = operation(connection)
                connection.commit()
                return result
            except Exception:
                connection.rollback()
                raise

    async def _submit(self, callback, *args):
        if self._closed:
            raise RuntimeError("SQLiteDatabase is closed")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, callback, *args)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=True)
