CREATE TABLE economy_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    action TEXT NOT NULL CHECK (action IN ('exchange', 'prestige')),
    source_event_id TEXT REFERENCES game_events(id),
    recipe_id TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX economy_history_user_created_idx
ON economy_history(user_id, created_at DESC);
