-- Normalize each row together so timestamp ordering CHECKs remain valid.
UPDATE users SET created_at=moscow_timestamp(created_at), updated_at=moscow_timestamp(updated_at);
UPDATE chat_state SET activity_updated_at=moscow_timestamp(activity_updated_at),
    last_event_at=moscow_timestamp(last_event_at), next_event_at=moscow_timestamp(next_event_at),
    updated_at=moscow_timestamp(updated_at);
UPDATE game_events SET created_at=moscow_timestamp(created_at), expires_at=moscow_timestamp(expires_at),
    resolved_at=moscow_timestamp(resolved_at);
UPDATE event_history SET created_at=moscow_timestamp(created_at);
UPDATE economy_history SET created_at=moscow_timestamp(created_at);
UPDATE event_participants SET created_at=moscow_timestamp(created_at), updated_at=moscow_timestamp(updated_at);
UPDATE story_summary SET updated_at=moscow_timestamp(updated_at);
UPDATE event_templates SET last_used_at=moscow_timestamp(last_used_at), created_at=moscow_timestamp(created_at);
UPDATE agency_history SET created_at=moscow_timestamp(created_at);
UPDATE intercept_game_runs SET started_at=moscow_timestamp(started_at), expires_at=moscow_timestamp(expires_at),
    completed_at=moscow_timestamp(completed_at);
UPDATE dead_drop_game_runs SET started_at=moscow_timestamp(started_at), expires_at=moscow_timestamp(expires_at),
    completed_at=moscow_timestamp(completed_at);
UPDATE spy_duels SET created_at=moscow_timestamp(created_at), expires_at=moscow_timestamp(expires_at),
    accepted_at=moscow_timestamp(accepted_at), resolved_at=moscow_timestamp(resolved_at);
UPDATE spy_duel_history SET created_at=moscow_timestamp(created_at);
UPDATE find_mole_cases SET created_at=moscow_timestamp(created_at);
UPDATE find_mole_game_runs SET started_at=moscow_timestamp(started_at), expires_at=moscow_timestamp(expires_at),
    completed_at=moscow_timestamp(completed_at);
UPDATE death_mission_runs SET armed_at=moscow_timestamp(armed_at), committed_at=moscow_timestamp(committed_at),
    expires_at=moscow_timestamp(expires_at), completed_at=moscow_timestamp(completed_at);
UPDATE death_mission_ledger SET created_at=moscow_timestamp(created_at);
UPDATE death_mission_actions SET created_at=moscow_timestamp(created_at);
UPDATE death_mission_outbox SET next_attempt_at=moscow_timestamp(next_attempt_at), delivered_at=moscow_timestamp(delivered_at);
UPDATE achievement_queue SET created_at=moscow_timestamp(created_at);
UPDATE user_achievements SET unlocked_at=moscow_timestamp(unlocked_at);
UPDATE chase_rounds SET deadline=moscow_timestamp(deadline), notified_at=moscow_timestamp(notified_at);
UPDATE equipment_uses SET created_at=moscow_timestamp(created_at);
UPDATE event_narratives SET created_at=moscow_timestamp(created_at);
UPDATE narrator_generation_limit SET next_attempt_at=moscow_timestamp(next_attempt_at);
UPDATE slot_spins SET created_at=moscow_timestamp(created_at);
UPDATE chat_members SET last_seen_at=moscow_timestamp(last_seen_at);
UPDATE death_mission_practice SET expires_at=moscow_timestamp(expires_at);
UPDATE schema_migrations SET applied_at=moscow_timestamp(applied_at);

-- Persisted confirmations and idempotent API responses also expose deadlines.
UPDATE event_participants SET payload_json=json_set(payload_json, '$.confirmation_expires_at',
    moscow_timestamp(json_extract(payload_json, '$.confirmation_expires_at')))
WHERE json_type(payload_json, '$.confirmation_expires_at')='text';
UPDATE death_mission_actions SET response_json=json_set(response_json, '$.expires_at',
    moscow_timestamp(json_extract(response_json, '$.expires_at')))
WHERE json_type(response_json, '$.expires_at')='text';
UPDATE death_mission_practice_actions SET response_json=json_set(response_json, '$.expires_at',
    moscow_timestamp(json_extract(response_json, '$.expires_at')))
WHERE json_type(response_json, '$.expires_at')='text';

DROP TRIGGER achievements_agents_insert;
DROP TRIGGER achievements_agents_update;
DROP TRIGGER achievements_agents_delete;
DROP TRIGGER achievements_progress;

-- shortcut: SQLite uses Moscow's current +03:00 offset; update if timezone rules change.
CREATE TRIGGER achievements_agents_insert AFTER INSERT ON user_agents
BEGIN
    INSERT INTO achievement_queue(user_id, kind, payload_json, created_at)
    VALUES (NEW.user_id, 'inventory',
        (SELECT json_group_object(agent_type, amount) FROM user_agents
         WHERE user_id = NEW.user_id AND amount > 0), strftime('%Y-%m-%dT%H:%M:%f000+03:00','now','+3 hours'));
END;

CREATE TRIGGER achievements_agents_update AFTER UPDATE ON user_agents WHEN NEW.amount != OLD.amount
BEGIN
    INSERT INTO achievement_queue(user_id, kind, payload_json, created_at)
    VALUES (NEW.user_id, 'inventory',
        (SELECT json_group_object(agent_type, amount) FROM user_agents
         WHERE user_id = NEW.user_id AND amount > 0), strftime('%Y-%m-%dT%H:%M:%f000+03:00','now','+3 hours'));
END;

CREATE TRIGGER achievements_agents_delete AFTER DELETE ON user_agents
BEGIN
    INSERT INTO achievement_queue(user_id, kind, payload_json, created_at)
    VALUES (OLD.user_id, 'inventory',
        (SELECT json_group_object(agent_type, amount) FROM user_agents
         WHERE user_id = OLD.user_id AND amount > 0), strftime('%Y-%m-%dT%H:%M:%f000+03:00','now','+3 hours'));
END;

CREATE TRIGGER achievements_progress AFTER UPDATE OF reputation, agency_level ON users
WHEN NEW.reputation != OLD.reputation OR NEW.agency_level != OLD.agency_level
BEGIN
    INSERT INTO achievement_queue(user_id, kind, payload_json, created_at)
    VALUES (NEW.user_id, 'progress', json_object(
        'reputation', max(OLD.reputation, NEW.reputation),
        'agency', max(OLD.agency_level, NEW.agency_level),
        'office', NEW.agency_level > OLD.agency_level AND
          COALESCE((SELECT amount FROM user_agents WHERE user_id=NEW.user_id
                    AND agent_type='intelligence_director'), 0)=0), strftime('%Y-%m-%dT%H:%M:%f000+03:00','now','+3 hours'));
END;
