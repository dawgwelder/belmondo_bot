CREATE TABLE agency_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    from_level INTEGER NOT NULL CHECK (from_level >= 0),
    to_level INTEGER NOT NULL CHECK (to_level = from_level + 1),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX agency_history_user_created_idx
ON agency_history(user_id, created_at DESC);
