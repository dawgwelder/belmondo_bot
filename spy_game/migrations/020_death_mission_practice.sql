-- Practice is independent from paid events, escrow and economic achievements.
CREATE TABLE death_mission_practice (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(user_id),
    chat_id INTEGER NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    revision INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    state_json TEXT NOT NULL,
    seed TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE UNIQUE INDEX death_practice_active_user ON death_mission_practice(user_id) WHERE status='in_run';
CREATE TABLE death_mission_practice_actions (
    run_id TEXT NOT NULL REFERENCES death_mission_practice(id),
    operation_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    PRIMARY KEY(run_id, operation_id)
);
