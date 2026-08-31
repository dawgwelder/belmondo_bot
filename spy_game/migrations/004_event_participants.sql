CREATE TABLE event_participants (
    event_id TEXT NOT NULL REFERENCES game_events(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('pending', 'resolved')),
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (event_id, user_id)
);

CREATE INDEX event_participants_status_updated_idx
ON event_participants(status, updated_at);
