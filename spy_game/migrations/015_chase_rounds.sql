CREATE TABLE chase_rounds (
    event_id TEXT PRIMARY KEY REFERENCES game_events(id),
    leader_user_id INTEGER NOT NULL REFERENCES users(user_id),
    turn INTEGER NOT NULL CHECK (turn BETWEEN 1 AND 6),
    deadline TEXT NOT NULL,
    rewards_json TEXT NOT NULL,
    notified_at TEXT
);

-- Keep a pending starter from the previous two-stage chase across an upgrade.
-- The already advertised event deadline remains in force for that legacy round.
INSERT INTO chase_rounds(event_id, leader_user_id, turn, deadline, rewards_json)
SELECT e.id, p.user_id, 1, e.expires_at,
       '[{"reward_type":"agent","reward_id":"informant","amount":1}]'
FROM game_events e JOIN event_participants p ON p.event_id=e.id
WHERE e.event_type='chase' AND e.status='active' AND p.status='pending';

CREATE INDEX chase_rounds_deadline_idx ON chase_rounds(deadline);
CREATE INDEX chase_rounds_pending_idx ON chase_rounds(event_id) WHERE notified_at IS NULL;
