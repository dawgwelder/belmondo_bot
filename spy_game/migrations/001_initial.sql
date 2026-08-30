CREATE TABLE users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    display_name TEXT,
    reputation INTEGER NOT NULL DEFAULT 0 CHECK (reputation >= 0),
    agency_level INTEGER NOT NULL DEFAULT 0 CHECK (agency_level >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE user_agents (
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    agent_type TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK (amount >= 0),
    PRIMARY KEY (user_id, agent_type)
);

CREATE TABLE chat_state (
    chat_id INTEGER PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    activity_score REAL NOT NULL DEFAULT 0 CHECK (activity_score >= 0),
    activity_updated_at TEXT NOT NULL,
    last_event_at TEXT,
    next_event_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE game_events (
    id TEXT PRIMARY KEY,
    chat_id INTEGER NOT NULL REFERENCES chat_state(chat_id),
    event_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('active', 'resolved', 'expired', 'cancelled')
    ),
    winner_user_id INTEGER REFERENCES users(user_id),
    message_id INTEGER,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    resolved_at TEXT,
    CHECK (expires_at > created_at)
);

CREATE UNIQUE INDEX one_active_spy_event_per_chat
ON game_events(chat_id) WHERE status = 'active';

CREATE INDEX game_events_due_idx ON game_events(status, expires_at);
CREATE INDEX game_events_chat_created_idx
ON game_events(chat_id, created_at DESC);

CREATE TABLE event_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    event_id TEXT NOT NULL REFERENCES game_events(id),
    chat_id INTEGER NOT NULL,
    user_id INTEGER REFERENCES users(user_id),
    event_type TEXT NOT NULL,
    outcome TEXT NOT NULL,
    reward_type TEXT,
    reward_id TEXT,
    reward_amount INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
