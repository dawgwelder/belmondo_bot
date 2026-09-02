ALTER TABLE chat_state ADD COLUMN activity_profile TEXT NOT NULL DEFAULT 'balanced'
CHECK (activity_profile IN ('calm', 'balanced', 'aggressive'));
