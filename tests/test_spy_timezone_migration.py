"""Existing UTC deadlines keep their instants when storage switches to Moscow."""

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from spy_game.database import SQLiteDatabase
from time_utils import MOSCOW, moscow_iso, moscow_now

CREATED = "2026-10-15T20:59:59.123456+00:00"
EXPIRES = "2026-10-15T21:00:59.123457+00:00"


def legacy_database(path):
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        migrations = Path(__file__).parents[1] / "spy_game" / "migrations"
        for migration in sorted(migrations.glob("[0-9][0-9][0-9]_*.sql")):
            version = int(migration.name.split("_", 1)[0])
            if version > 20:
                break
            connection.executescript(migration.read_text())
            connection.execute(
                "INSERT INTO schema_migrations VALUES (?,?)", (version, CREATED)
            )
        connection.execute(
            "INSERT INTO users(user_id,created_at,updated_at) VALUES (1,?,?)",
            (CREATED, CREATED),
        )
        connection.execute(
            "INSERT INTO chat_state(chat_id,enabled,activity_updated_at,next_event_at,updated_at) "
            "VALUES (1,1,?,?,?)",
            (CREATED, EXPIRES, CREATED),
        )
        connection.execute(
            "INSERT INTO game_events(id,chat_id,event_type,status,payload_json,created_at,expires_at) "
            "VALUES ('event',1,'death_operation','active','{}',?,?)",
            (CREATED, EXPIRES),
        )
        connection.execute(
            "INSERT INTO spy_duels(id,chat_id,challenger_user_id,opponent_username,agent_type,"
            "stake_amount,status,tie_breaker_role,scenario_json,created_at,expires_at) "
            "VALUES ('duel',1,1,'opponent','informant',1,'pending','challenger','{}',?,?)",
            (CREATED, EXPIRES),
        )


@pytest.mark.asyncio
async def test_migration_preserves_deadlines_precision_nulls_and_restarts(tmp_path):
    path = tmp_path / "spy.sqlite3"
    legacy_database(path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "INSERT INTO event_templates(event_type,tone,text) VALUES ('chase','neutral','legacy')"
        )
        connection.execute(
            "INSERT INTO chase_rounds(event_id,leader_user_id,turn,deadline,rewards_json) "
            "VALUES ('event',1,1,?,'[]')",
            (EXPIRES,),
        )
        connection.execute(
            "INSERT INTO narrator_generation_limit VALUES (1,?)", (EXPIRES,)
        )
        connection.execute(
            "INSERT INTO chat_members VALUES (1,1,?), (2,1,?)",
            ("2026-10-15T22:30:00.654321+03:00", "2026-10-15T20:00:00Z"),
        )
        connection.execute(
            "UPDATE users SET updated_at=? WHERE user_id=1",
            ("2026-10-16T04:59:59.654321+08:00",),
        )

    database = SQLiteDatabase(path)
    try:
        await database.initialize()
        with closing(sqlite3.connect(path)) as connection:
            connection.row_factory = sqlite3.Row
            event = connection.execute("SELECT * FROM game_events").fetchone()
            assert event["created_at"] == "2026-10-15T23:59:59.123456+03:00"
            assert event["expires_at"] == "2026-10-16T00:00:59.123457+03:00"
            assert event["resolved_at"] is None
            assert datetime.fromisoformat(
                event["expires_at"]
            ) == datetime.fromisoformat(EXPIRES)
            assert connection.execute("SELECT updated_at FROM users").fetchone()[0] == (
                "2026-10-15T23:59:59.654321+03:00"
            )
            assert tuple(
                connection.execute(
                    "SELECT last_used_at,created_at FROM event_templates"
                ).fetchone()
            ) == (None, None)
            assert [
                row[0]
                for row in connection.execute(
                    "SELECT chat_id FROM chat_members ORDER BY last_seen_at DESC"
                )
            ] == [2, 1]
            deadline = datetime.fromisoformat(EXPIRES)
            for table, column in (
                ("game_events", "expires_at"),
                ("spy_duels", "expires_at"),
                ("chase_rounds", "deadline"),
                ("chat_state", "next_event_at"),
                ("narrator_generation_limit", "next_attempt_at"),
            ):
                query = f"SELECT COUNT(*) FROM {table} WHERE {column} <= ?"
                assert (
                    connection.execute(
                        query, (moscow_iso(deadline - timedelta(microseconds=1)),)
                    ).fetchone()[0]
                    == 0
                )
                assert (
                    connection.execute(query, (moscow_iso(deadline),)).fetchone()[0]
                    == 1
                )
            assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert not connection.execute("PRAGMA foreign_key_check").fetchall()
            before_restart = "\n".join(connection.iterdump())

        await database.initialize()
        with closing(sqlite3.connect(path)) as connection:
            assert "\n".join(connection.iterdump()) == before_restart
            assert (
                connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[
                    0
                ]
                == 22
            )
            assert all(
                row[0].endswith("+03:00")
                for row in connection.execute(
                    "SELECT applied_at FROM schema_migrations"
                )
            )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_migration_converts_persisted_json_deadlines(tmp_path):
    path = tmp_path / "spy.sqlite3"
    legacy_database(path)
    confirmation = {"confirmation_expires_at": EXPIRES, "stake": {"informant": 2}}
    response = {"expires_at": EXPIRES, "status": "armed", "revision": 1}
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "INSERT INTO event_participants VALUES ('event',1,'pending',?,?,?)",
            (json.dumps(confirmation), CREATED, CREATED),
        )
        connection.execute(
            "INSERT INTO death_mission_runs(id,event_id,user_id,token_hash,status,stake_json,rules_json,expires_at) "
            "VALUES ('run','event',1,'token','armed','{}','{}',?)",
            (EXPIRES,),
        )
        connection.execute(
            "INSERT INTO death_mission_actions VALUES ('run','operation','hash',?,?)",
            (json.dumps(response), CREATED),
        )
        connection.execute(
            "INSERT INTO death_mission_outbox VALUES ('run',0,?,NULL)", (EXPIRES,)
        )
        connection.execute(
            "INSERT INTO death_mission_practice(id,user_id,chat_id,token_hash,status,state_json,seed,expires_at) "
            "VALUES ('practice',1,1,'practice-token','in_run','{}','seed',?)",
            (EXPIRES,),
        )
        connection.execute(
            "INSERT INTO death_mission_practice_actions VALUES ('practice','operation','hash',?)",
            (json.dumps(response),),
        )
    database = SQLiteDatabase(path)
    try:
        await database.initialize()
        expected_expiry = moscow_iso(datetime.fromisoformat(EXPIRES))
        with closing(sqlite3.connect(path)) as connection:
            confirmation["confirmation_expires_at"] = expected_expiry
            assert (
                json.loads(
                    connection.execute(
                        "SELECT payload_json FROM event_participants"
                    ).fetchone()[0]
                )
                == confirmation
            )
            response["expires_at"] = expected_expiry
            for table in ("death_mission_actions", "death_mission_practice_actions"):
                assert (
                    json.loads(
                        connection.execute(
                            f"SELECT response_json FROM {table}"
                        ).fetchone()[0]
                    )
                    == response
                )
            assert connection.execute(
                "SELECT next_attempt_at,delivered_at FROM death_mission_outbox"
            ).fetchone() == (expected_expiry, None)
            assert (
                connection.execute(
                    "SELECT expires_at FROM death_mission_practice"
                ).fetchone()[0]
                == expected_expiry
            )
    finally:
        await database.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["not-a-timestamp", "2026-10-15T20:00:00"])
async def test_bad_legacy_timestamp_rolls_back_whole_migration(tmp_path, invalid):
    path = tmp_path / "spy.sqlite3"
    legacy_database(path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            "INSERT INTO user_achievements VALUES (1,'legacy',?,0)", (invalid,)
        )
    with closing(sqlite3.connect(path)) as connection:
        before = "\n".join(connection.iterdump())
    database = SQLiteDatabase(path)
    try:
        with pytest.raises(sqlite3.OperationalError, match="user-defined function"):
            await database.initialize()
        with closing(sqlite3.connect(path)) as connection:
            assert "\n".join(connection.iterdump()) == before
            assert (
                connection.execute(
                    "SELECT COUNT(*) FROM schema_migrations WHERE version=21"
                ).fetchone()[0]
                == 0
            )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_achievement_triggers_use_moscow_without_migration_udf(tmp_path):
    path = tmp_path / "spy.sqlite3"
    database = SQLiteDatabase(path)
    try:
        await database.initialize()
        before = moscow_now() - timedelta(milliseconds=1)
        # A separate connection has no custom functions, like maintenance tools.
        with closing(sqlite3.connect(path)) as connection, connection:
            stamp = moscow_iso(moscow_now())
            connection.execute(
                "INSERT INTO users(user_id,created_at,updated_at) VALUES (1,?,?)",
                (stamp, stamp),
            )
            connection.execute("INSERT INTO user_agents VALUES (1,'informant',1)")
            connection.execute("UPDATE user_agents SET amount=2 WHERE user_id=1")
            connection.execute("DELETE FROM user_agents WHERE user_id=1")
            connection.execute("UPDATE users SET reputation=1 WHERE user_id=1")
            timestamps = [
                row[0]
                for row in connection.execute(
                    "SELECT created_at FROM achievement_queue"
                )
            ]
        after = moscow_now()
        assert len(timestamps) == 4
        for value in timestamps:
            parsed = datetime.fromisoformat(value)
            assert value == moscow_iso(parsed)
            assert parsed.utcoffset() == after.astimezone(MOSCOW).utcoffset()
            assert before <= parsed <= after
    finally:
        await database.close()
