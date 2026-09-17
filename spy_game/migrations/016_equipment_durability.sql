-- One specimen in use per type. Unused copies stay fungible in user_items.
CREATE TABLE item_durability (
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    item_type TEXT NOT NULL,
    remaining INTEGER NOT NULL CHECK (remaining > 0),
    maximum INTEGER NOT NULL CHECK (maximum > 0 AND remaining <= maximum),
    PRIMARY KEY (user_id, item_type)
);

-- Earlier exchanges could leave an equipped reference to an exhausted stack.
DELETE FROM equipped_items WHERE NOT EXISTS (
    SELECT 1 FROM user_items i WHERE i.user_id=equipped_items.user_id
    AND i.item_type=equipped_items.item_type AND i.amount > 0
);
INSERT INTO item_durability(user_id, item_type, remaining, maximum)
SELECT user_id, item_type, CASE item_type WHEN 'wiretap' THEN 5 ELSE 3 END,
       CASE item_type WHEN 'wiretap' THEN 5 ELSE 3 END
FROM equipped_items;

CREATE TABLE equipment_uses (
    operation_key TEXT NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    item_type TEXT NOT NULL,
    remaining INTEGER NOT NULL CHECK (remaining >= 0),
    created_at TEXT NOT NULL,
    PRIMARY KEY (operation_key, user_id, item_type)
);

ALTER TABLE chase_rounds ADD COLUMN hold_seconds INTEGER NOT NULL DEFAULT 30;
UPDATE chase_rounds SET hold_seconds=35 - 5 * turn;

-- Persist actual modified rewards so a reopened HTML5 result is identical.
ALTER TABLE intercept_game_runs ADD COLUMN reward_id TEXT;
ALTER TABLE intercept_game_runs ADD COLUMN reward_amount INTEGER;
UPDATE intercept_game_runs SET
    reward_id=(SELECT h.reward_id FROM event_history h WHERE h.idempotency_key='intercept-game:' || intercept_game_runs.id),
    reward_amount=(SELECT h.reward_amount FROM event_history h WHERE h.idempotency_key='intercept-game:' || intercept_game_runs.id)
WHERE status='won';
