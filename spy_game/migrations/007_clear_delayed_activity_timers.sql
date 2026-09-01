-- Delayed activity timers could fire after the conversation had already ended.
-- Composite scheduling does not carry trigger candidates across ticks.
UPDATE chat_state SET next_event_at = NULL WHERE next_event_at IS NOT NULL;
