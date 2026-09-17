ALTER TABLE event_templates ADD COLUMN context_key TEXT NOT NULL DEFAULT '';
ALTER TABLE event_templates ADD COLUMN created_at TEXT;
CREATE INDEX event_templates_context_idx ON event_templates(event_type,tone,context_key,id);

CREATE TABLE event_narratives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    chat_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    body TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX event_narratives_chat_idx ON event_narratives(chat_id,id DESC);

-- A persisted global cooldown also bounds failed/duplicate LLM attempts.
CREATE TABLE narrator_generation_limit (
    id INTEGER PRIMARY KEY CHECK (id=1),
    next_attempt_at TEXT NOT NULL
);
