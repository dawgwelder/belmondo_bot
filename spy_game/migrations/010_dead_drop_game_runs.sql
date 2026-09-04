CREATE TABLE dead_drop_game_runs (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES game_events(id) ON DELETE CASCADE,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    code_json TEXT NOT NULL,
    attempts_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK (
        status IN ('ready', 'won', 'failed', 'lost_race', 'expired')
    ),
    started_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    completed_at TEXT,
    reward_type TEXT,
    reward_id TEXT,
    reward_amount INTEGER,
    UNIQUE (event_id, user_id),
    CHECK (expires_at > started_at)
);

CREATE INDEX dead_drop_game_runs_event_status_idx
ON dead_drop_game_runs(event_id, status);
