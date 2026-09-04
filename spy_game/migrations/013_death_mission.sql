CREATE TABLE death_mission_runs (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES game_events(id),
    user_id INTEGER NOT NULL REFERENCES users(user_id),
    token_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK(status IN (
        'preview', 'armed', 'in_run', 'won', 'lost', 'extracted',
        'timed_out', 'cancelled_refunded', 'expired', 'lost_race'
    )),
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0),
    mode TEXT CHECK(mode IN ('all_in', 'mission')),
    tactic TEXT NOT NULL DEFAULT 'balanced',
    bonus TEXT NOT NULL DEFAULT 'tier3',
    stake_json TEXT NOT NULL,
    rules_json TEXT NOT NULL,
    state_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    seed TEXT,
    armed_at TEXT,
    committed_at TEXT,
    expires_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(event_id, user_id)
);
CREATE UNIQUE INDEX death_mission_event_owner
ON death_mission_runs(event_id) WHERE committed_at IS NOT NULL;
CREATE UNIQUE INDEX death_mission_active_user
ON death_mission_runs(user_id) WHERE status = 'in_run';
CREATE INDEX death_mission_expiry ON death_mission_runs(status, expires_at);

CREATE TABLE death_mission_stakes (
    run_id TEXT NOT NULL REFERENCES death_mission_runs(id),
    agent_type TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK(amount > 0),
    PRIMARY KEY(run_id, agent_type)
);
CREATE TABLE death_mission_ledger (
    run_id TEXT NOT NULL REFERENCES death_mission_runs(id),
    action TEXT NOT NULL CHECK(action IN ('reserve', 'settle')),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(run_id, action)
);
CREATE TABLE death_mission_actions (
    run_id TEXT NOT NULL REFERENCES death_mission_runs(id),
    operation_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(run_id, operation_id)
);
CREATE TABLE death_mission_achievements (
    user_id INTEGER NOT NULL REFERENCES users(user_id),
    run_id TEXT NOT NULL REFERENCES death_mission_runs(id),
    achievement TEXT NOT NULL CHECK(achievement IN ('checkpoint', 'raid', 'won')),
    PRIMARY KEY(user_id, run_id, achievement)
);
CREATE TABLE death_mission_outbox (
    run_id TEXT PRIMARY KEY REFERENCES death_mission_runs(id),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    next_attempt_at TEXT,
    delivered_at TEXT
);
