CREATE TABLE find_mole_cases (
    event_id TEXT PRIMARY KEY REFERENCES game_events(id) ON DELETE CASCADE,
    public_case_json TEXT NOT NULL,
    solution_suspect_id TEXT NOT NULL,
    template_id TEXT NOT NULL,
    template_version INTEGER NOT NULL CHECK (template_version > 0),
    created_at TEXT NOT NULL
);

CREATE TABLE find_mole_game_runs (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES game_events(id) ON DELETE CASCADE,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (
        status IN ('ready', 'won', 'failed', 'lost_race', 'expired')
    ),
    revision INTEGER NOT NULL DEFAULT 0 CHECK (revision >= 0),
    selected_suspect_id TEXT,
    accusation_key TEXT,
    item_reward_id TEXT,
    item_reward_amount INTEGER,
    agent_reward_type TEXT,
    agent_reward_amount INTEGER,
    started_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE (event_id, user_id),
    CHECK (expires_at > started_at),
    CHECK (item_reward_amount IS NULL OR item_reward_amount > 0),
    CHECK (agent_reward_amount IS NULL OR agent_reward_amount > 0)
);

CREATE INDEX find_mole_game_runs_event_status_idx
ON find_mole_game_runs(event_id, status);

CREATE INDEX find_mole_game_runs_expiry_idx
ON find_mole_game_runs(status, expires_at);
