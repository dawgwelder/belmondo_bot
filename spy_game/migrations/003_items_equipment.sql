CREATE TABLE user_items (
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    item_type TEXT NOT NULL,
    amount INTEGER NOT NULL DEFAULT 0 CHECK (amount >= 0),
    PRIMARY KEY (user_id, item_type)
);

CREATE INDEX user_items_item_type_idx
ON user_items(item_type);

CREATE TABLE equipped_items (
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    slot INTEGER NOT NULL CHECK (slot > 0),
    item_type TEXT NOT NULL,
    PRIMARY KEY (user_id, slot),
    UNIQUE (user_id, item_type)
);
