CREATE TABLE slot_spins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(user_id),
    chat_id INTEGER NOT NULL,
    operation_id TEXT NOT NULL,
    stake INTEGER NOT NULL CHECK (stake IN (1,3,5)),
    symbols_json TEXT NOT NULL,
    multiplier INTEGER NOT NULL CHECK (multiplier >= 0),
    payout INTEGER NOT NULL CHECK (payout >= 0 AND payout = stake * multiplier),
    balance_after INTEGER NOT NULL CHECK (balance_after >= 0),
    rules_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (user_id, operation_id)
);
CREATE INDEX slot_spins_user_idx ON slot_spins(user_id, id DESC);
