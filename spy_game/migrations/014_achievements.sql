-- Facts are queued in the same transaction as balances/history. The service
-- drains this queue before commit; rolled-back rewards cannot unlock medals.
CREATE INDEX achievement_event_user_idx ON event_history(event_id, user_id);
CREATE TABLE achievement_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    historical INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE achievement_metrics (
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    metric TEXT NOT NULL,
    value INTEGER NOT NULL CHECK(value >= 0),
    PRIMARY KEY(user_id, metric)
);
CREATE TABLE achievement_members (
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    metric TEXT NOT NULL,
    member TEXT NOT NULL,
    PRIMARY KEY(user_id, metric, member)
);
CREATE TABLE user_achievements (
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    achievement_id TEXT NOT NULL,
    unlocked_at TEXT NOT NULL,
    seen INTEGER NOT NULL DEFAULT 0 CHECK(seen IN (0, 1)),
    PRIMARY KEY(user_id, achievement_id)
);
CREATE TABLE achievement_titles (
    user_id INTEGER PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    achievement_id TEXT NOT NULL,
    FOREIGN KEY(user_id, achievement_id)
        REFERENCES user_achievements(user_id, achievement_id)
);

CREATE TRIGGER achievements_agents_insert AFTER INSERT ON user_agents
BEGIN
    INSERT INTO achievement_queue(user_id, kind, payload_json, created_at)
    VALUES (NEW.user_id, 'inventory',
        (SELECT json_group_object(agent_type, amount) FROM user_agents
         WHERE user_id = NEW.user_id AND amount > 0), strftime('%Y-%m-%dT%H:%M:%f+00:00','now'));
END;

CREATE TRIGGER achievements_agents_update AFTER UPDATE ON user_agents WHEN NEW.amount != OLD.amount
BEGIN
    INSERT INTO achievement_queue(user_id, kind, payload_json, created_at)
    VALUES (NEW.user_id, 'inventory',
        (SELECT json_group_object(agent_type, amount) FROM user_agents
         WHERE user_id = NEW.user_id AND amount > 0), strftime('%Y-%m-%dT%H:%M:%f+00:00','now'));
END;

CREATE TRIGGER achievements_agents_delete AFTER DELETE ON user_agents
BEGIN
    INSERT INTO achievement_queue(user_id, kind, payload_json, created_at)
    VALUES (OLD.user_id, 'inventory',
        (SELECT json_group_object(agent_type, amount) FROM user_agents
         WHERE user_id = OLD.user_id AND amount > 0), strftime('%Y-%m-%dT%H:%M:%f+00:00','now'));
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
                    AND agent_type='intelligence_director'), 0)=0), strftime('%Y-%m-%dT%H:%M:%f+00:00','now'));
END;

CREATE TRIGGER achievements_event AFTER INSERT ON event_history
WHEN NEW.user_id IS NOT NULL
BEGIN
    INSERT INTO achievement_queue(user_id, kind, payload_json, created_at)
    VALUES (NEW.user_id, 'event', json_object('id', NEW.id, 'balance',
        COALESCE((SELECT SUM(amount) FROM user_agents WHERE user_id=NEW.user_id), 0)),
        NEW.created_at);
END;

CREATE TRIGGER achievements_economy AFTER INSERT ON economy_history
WHEN NEW.user_id IS NOT NULL
BEGIN
    INSERT INTO achievement_queue(user_id, kind, payload_json, created_at)
    VALUES (NEW.user_id, 'economy', json_object('id', NEW.id, 'balance',
        COALESCE((SELECT SUM(amount) FROM user_agents WHERE user_id=NEW.user_id), 0)),
        NEW.created_at);
END;

CREATE TRIGGER achievements_agency AFTER INSERT ON agency_history
WHEN NEW.user_id IS NOT NULL
BEGIN
    INSERT INTO achievement_queue(user_id, kind, payload_json, created_at)
    VALUES (NEW.user_id, 'agency', json_object('id', NEW.id, 'balance',
        COALESCE((SELECT SUM(amount) FROM user_agents WHERE user_id=NEW.user_id), 0)),
        NEW.created_at);
END;

CREATE TRIGGER achievements_duel AFTER INSERT ON spy_duel_history
WHEN NEW.winner_user_id IS NOT NULL
BEGIN
    INSERT INTO achievement_queue(user_id, kind, payload_json, created_at)
    VALUES (NEW.winner_user_id, 'duel', json_object('id', NEW.id, 'balance',
        COALESCE((SELECT SUM(amount) FROM user_agents WHERE user_id=NEW.winner_user_id), 0)),
        NEW.created_at);
END;

INSERT INTO achievement_queue(user_id,kind,payload_json,created_at,historical)
SELECT user_id,kind,payload_json,created_at,1 FROM (
SELECT user_id AS user_id, 'event' AS kind, json_object('id',id) AS payload_json, created_at FROM event_history WHERE user_id IS NOT NULL
UNION ALL
SELECT user_id AS user_id, 'economy' AS kind, json_object('id',id) AS payload_json, created_at FROM economy_history WHERE user_id IS NOT NULL
UNION ALL
SELECT user_id AS user_id, 'agency' AS kind, json_object('id',id) AS payload_json, created_at FROM agency_history WHERE user_id IS NOT NULL
UNION ALL
SELECT winner_user_id AS user_id, 'duel' AS kind, json_object('id',id) AS payload_json, created_at FROM spy_duel_history WHERE winner_user_id IS NOT NULL
) ORDER BY created_at;

INSERT INTO achievement_queue(user_id,kind,payload_json,created_at,historical)
SELECT user_id, 'inventory',
    (SELECT json_group_object(agent_type,amount) FROM user_agents a
     WHERE a.user_id=u.user_id AND amount>0), strftime('%Y-%m-%dT%H:%M:%f+00:00','now'), 1 FROM users u;
INSERT INTO achievement_queue(user_id,kind,payload_json,created_at,historical)
SELECT user_id, 'progress', json_object('reputation',reputation,'agency',agency_level),
    strftime('%Y-%m-%dT%H:%M:%f+00:00','now'), 1 FROM users;
INSERT INTO achievement_queue(user_id,kind,payload_json,created_at,historical)
SELECT user_id, 'inventory', stake_json, strftime('%Y-%m-%dT%H:%M:%f+00:00','now'), 1
FROM death_mission_runs WHERE status='in_run';
