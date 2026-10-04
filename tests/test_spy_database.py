"""Connections must be released without waiting for garbage collection."""

import asyncio
import sqlite3
import threading

import pytest
import pytest_asyncio

from spy_game import database as database_module
from spy_game.database import SQLiteDatabase


@pytest_asyncio.fixture
async def tracked_database(tmp_path, monkeypatch):
    database = SQLiteDatabase(tmp_path / "spy.sqlite3")
    connections = []
    connect = sqlite3.connect

    def track_connection(*args, **kwargs):
        connection = connect(*args, **kwargs)
        # Strong references prevent GC from hiding a missing close().
        connections.append(connection)
        return connection

    monkeypatch.setattr(database_module.sqlite3, "connect", track_connection)
    try:
        yield database, connections
    finally:

        def cleanup():
            for connection in connections:
                connection.close()

        await database._submit(cleanup)
        await database.close()


def assert_closed(connections):
    assert connections
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed database"):
            _ = connection.in_transaction


@pytest.mark.asyncio
async def test_initialization_reads_and_writes_close_connections(tracked_database):
    database, connections = tracked_database
    await database.initialize()
    assert_closed(connections)
    await database.transaction(
        lambda c: c.execute("CREATE TABLE samples(value INTEGER)")
    )
    for value in range(25):
        await database.transaction(
            lambda c: c.execute("INSERT INTO samples VALUES (?)", (value,)),
            immediate=True,
        )
        row = await database.read(
            lambda c: c.execute("SELECT SUM(value) AS total FROM samples").fetchone()
        )
        assert row["total"] == sum(range(value + 1))
        assert_closed(connections)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["read", "operation", "before_commit"])
async def test_operation_errors_close_connections_and_roll_back(
    tracked_database, failure
):
    database, connections = tracked_database
    await database.transaction(
        lambda c: c.execute("CREATE TABLE samples(value INTEGER)")
    )

    def fail(connection):
        raise RuntimeError("operation failed")

    def write(connection):
        connection.execute("INSERT INTO samples VALUES (1)")
        if failure == "operation":
            fail(connection)

    if failure == "before_commit":
        database.before_commit = fail
    with pytest.raises(RuntimeError, match="operation failed"):
        if failure == "read":
            await database.read(fail)
        else:
            await database.transaction(write, immediate=True)
    assert_closed(connections)
    assert (
        await database.read(
            lambda c: c.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        )
        == 0
    )


@pytest.mark.asyncio
async def test_commit_failure_rolls_back_and_closes_connection(tracked_database):
    database, connections = tracked_database

    def schema(connection):
        connection.execute("CREATE TABLE parents(id INTEGER PRIMARY KEY)")
        connection.execute(
            "CREATE TABLE children(parent_id INTEGER REFERENCES parents(id) "
            "DEFERRABLE INITIALLY DEFERRED)"
        )

    await database.transaction(schema, immediate=True)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        await database.transaction(
            lambda c: c.execute("INSERT INTO children VALUES (1)"),
            immediate=True,
        )
    assert_closed(connections)
    assert (
        await database.read(
            lambda c: c.execute("SELECT COUNT(*) FROM children").fetchone()[0]
        )
        == 0
    )


@pytest.mark.asyncio
async def test_failed_migration_rolls_back_and_closes_connection(
    tracked_database, monkeypatch
):
    database, connections = tracked_database

    def broken_migration(source):
        yield "CREATE TABLE partial_migration(value INTEGER)"
        yield "INSERT INTO missing_table VALUES (1)"

    monkeypatch.setattr(database_module, "_iter_sql_statements", broken_migration)
    with pytest.raises(sqlite3.OperationalError, match="missing_table"):
        await database.initialize()
    assert_closed(connections)
    assert (
        await database.read(
            lambda c: c.execute(
                "SELECT name FROM sqlite_master WHERE name='partial_migration'"
            ).fetchone()
        )
        is None
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("pragma", ["foreign_keys", "busy_timeout", "synchronous"])
async def test_connection_setup_failure_closes_connection(
    tracked_database, monkeypatch, pragma
):
    database, connections = tracked_database
    connect = database_module.sqlite3.connect

    def deny_pragma(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_authorizer(
            lambda action, name, *_: (
                sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_PRAGMA and name == pragma
                else sqlite3.SQLITE_OK
            )
        )
        return connection

    monkeypatch.setattr(database_module.sqlite3, "connect", deny_pragma)
    with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
        await database.read(lambda c: c.execute("SELECT 1").fetchone())
    assert_closed(connections)


@pytest.mark.asyncio
async def test_cancelled_waiter_closes_connection_when_worker_finishes(
    tracked_database,
):
    database, connections = tracked_database
    started = threading.Event()
    release = threading.Event()

    def operation(connection):
        started.set()
        assert release.wait(timeout=5)
        return connection.execute("SELECT 1").fetchone()[0]

    task = asyncio.create_task(database.transaction(operation, immediate=True))
    try:
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
    # The next queued operation is a barrier for the cancelled waiter's worker.
    assert await database.read(lambda c: c.execute("SELECT 2").fetchone()[0]) == 2
    assert_closed(connections)
