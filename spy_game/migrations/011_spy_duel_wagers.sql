CREATE TABLE spy_duels (
    id TEXT PRIMARY KEY,
    chat_id INTEGER NOT NULL REFERENCES chat_state(chat_id),
    message_id INTEGER,
    challenger_user_id INTEGER NOT NULL REFERENCES users(user_id),
    opponent_user_id INTEGER REFERENCES users(user_id),
    opponent_username TEXT,
    agent_type TEXT NOT NULL,
    stake_amount INTEGER NOT NULL CHECK (stake_amount > 0),
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'choosing', 'resolved', 'refunded')
    ),
    challenger_action TEXT,
    opponent_action TEXT,
    winner_user_id INTEGER REFERENCES users(user_id),
    resolution TEXT,
    tie_breaker_role TEXT NOT NULL CHECK (
        tie_breaker_role IN ('challenger', 'opponent')
    ),
    scenario_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    accepted_at TEXT,
    resolved_at TEXT,
    CHECK (expires_at > created_at),
    CHECK (opponent_user_id IS NOT NULL OR opponent_username IS NOT NULL)
);

CREATE UNIQUE INDEX one_active_spy_duel_per_chat
ON spy_duels(chat_id) WHERE status IN ('pending', 'choosing');

CREATE INDEX spy_duels_expiry_idx ON spy_duels(status, expires_at);

CREATE TABLE spy_duel_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    duel_id TEXT NOT NULL REFERENCES spy_duels(id),
    chat_id INTEGER NOT NULL,
    challenger_user_id INTEGER NOT NULL,
    opponent_user_id INTEGER,
    winner_user_id INTEGER,
    agent_type TEXT NOT NULL,
    stake_amount INTEGER NOT NULL,
    pot_amount INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
