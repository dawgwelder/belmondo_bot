-- Persistent user <-> chat membership so the Mini App can resolve a group
-- context without a fresh /spy link. Membership is a gate only: player state
-- stays global per user_id.
CREATE TABLE chat_members (
    chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);
CREATE INDEX chat_members_user_seen_idx ON chat_members(user_id, last_seen_at DESC);

ALTER TABLE chat_state ADD COLUMN title TEXT;

-- Backfill from every table that records both a chat and a user, so players
-- with history get the full cabinet immediately after the release.
INSERT INTO chat_members(chat_id, user_id, last_seen_at)
SELECT chat_id, user_id, MAX(created_at) FROM (
    SELECT chat_id, user_id, created_at FROM event_history WHERE user_id IS NOT NULL
    UNION ALL
    SELECT e.chat_id, p.user_id, p.updated_at
    FROM event_participants p JOIN game_events e ON e.id = p.event_id
    UNION ALL
    SELECT chat_id, user_id, created_at FROM slot_spins
    UNION ALL
    SELECT chat_id, challenger_user_id, created_at FROM spy_duel_history
    UNION ALL
    SELECT chat_id, opponent_user_id, created_at FROM spy_duel_history
    WHERE opponent_user_id IS NOT NULL
)
GROUP BY chat_id, user_id;
