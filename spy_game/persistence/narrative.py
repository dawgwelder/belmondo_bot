"""Narration cache, per-chat repetition history and persisted LLM rate limit."""
from datetime import timedelta

from .base import _iso

POOL_SIZE = 8
RECENT_WINDOW = 3


def body_key(body):
    return " ".join(body.casefold().split())


class NarrativeRepository:
    def __init__(self, cooldown_seconds=300, refresh_seconds=6 * 60 * 60):
        self.cooldown_seconds = cooldown_seconds
        self.refresh_seconds = refresh_seconds

    @staticmethod
    def existing(connection, event):
        row = connection.execute(
            "SELECT chat_id, body FROM event_narratives WHERE event_id=?",
            (event.event_id,),
        ).fetchone()
        if row and row["chat_id"] != event.chat_id:
            raise ValueError("narrative event belongs to another chat")
        return row["body"] if row else None

    @staticmethod
    def recent(connection, chat_id):
        return tuple(
            row["body"]
            for row in connection.execute(
                "SELECT body FROM event_narratives WHERE chat_id=? ORDER BY id DESC LIMIT ?",
                (chat_id, RECENT_WINDOW),
            )
        )

    @staticmethod
    def cached(connection, event, context_key):
        return connection.execute(
            """SELECT id, text, created_at FROM event_templates
               WHERE event_type=? AND tone=? AND context_key=? ORDER BY usage_count,id""",
            (event.event_type, event.tone, context_key),
        ).fetchall()

    def prepare(self, connection, event, context_key, now, can_generate):
        existing = self.existing(connection, event)
        if existing is not None:
            return existing, (), False
        recent = self.recent(connection, event.chat_id)
        pool = self.cached(connection, event, context_key)
        newest = max((row["created_at"] or "" for row in pool), default="")
        needs_variant = len(pool) < POOL_SIZE or newest <= _iso(
            now - timedelta(seconds=self.refresh_seconds)
        )
        generate = False
        if can_generate and needs_variant:
            # Claim before any network call, including unsuccessful generations.
            claim = connection.execute(
                """INSERT INTO narrator_generation_limit(id,next_attempt_at) VALUES (1,?)
                   ON CONFLICT(id) DO UPDATE SET next_attempt_at=excluded.next_attempt_at
                   WHERE narrator_generation_limit.next_attempt_at <= ?""",
                (_iso(now + timedelta(seconds=self.cooldown_seconds)), _iso(now)),
            )
            generate = claim.rowcount == 1
        return None, recent, generate

    def finish(self, connection, event, context_key, now, generated_body, templates):
        existing = self.existing(connection, event)
        if existing is not None:
            return existing, "cache"
        recent = {body_key(body) for body in self.recent(connection, event.chat_id)}
        pool = self.cached(connection, event, context_key)
        row_id = None
        if generated_body and body_key(generated_body) not in recent | {
            body_key(row["text"]) for row in pool
        }:
            row_id = connection.execute(
                "INSERT INTO event_templates(event_type,tone,context_key,text,created_at) VALUES (?,?,?,?,?)",
                (event.event_type, event.tone, context_key, generated_body, _iso(now)),
            ).lastrowid
            body, source = generated_body, "llm"
            connection.execute(
                """DELETE FROM event_templates WHERE id IN (
                    SELECT id FROM event_templates WHERE event_type=? AND tone=? AND context_key=?
                    ORDER BY id DESC LIMIT -1 OFFSET ?
                )""",
                (event.event_type, event.tone, context_key, POOL_SIZE),
            )
        else:
            candidate = next(
                (row for row in pool if body_key(row["text"]) not in recent), None
            )
            if candidate:
                row_id, body, source = candidate["id"], candidate["text"], "cache"
            else:
                body = next(
                    (text for text in templates if body_key(text) not in recent),
                    templates[0],
                )
                source = "template"
        if row_id is not None:
            connection.execute(
                "UPDATE event_templates SET usage_count=usage_count+1,last_used_at=? WHERE id=?",
                (_iso(now), row_id),
            )
        connection.execute(
            "INSERT INTO event_narratives(event_id,chat_id,event_type,body,source,created_at) VALUES (?,?,?,?,?,?)",
            (event.event_id, event.chat_id, event.event_type, body, source, _iso(now)),
        )
        return body, source
