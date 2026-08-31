ALTER TABLE chat_state ADD COLUMN story_arc TEXT;
ALTER TABLE chat_state ADD COLUMN story_stage INTEGER NOT NULL DEFAULT 0
    CHECK (story_stage >= 0);

CREATE TABLE story_summary (
    chat_id INTEGER PRIMARY KEY REFERENCES chat_state(chat_id) ON DELETE CASCADE,
    summary TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE lore (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL CHECK (
        category IN ('npc', 'organization', 'story', 'location', 'world')
    ),
    name TEXT NOT NULL,
    text TEXT NOT NULL
);

CREATE TABLE lore_tags (
    lore_id TEXT NOT NULL REFERENCES lore(id) ON DELETE CASCADE,
    tag TEXT NOT NULL,
    PRIMARY KEY (lore_id, tag)
);

CREATE TABLE event_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    tone TEXT NOT NULL,
    text TEXT NOT NULL,
    usage_count INTEGER NOT NULL DEFAULT 0 CHECK (usage_count >= 0),
    last_used_at TEXT
);

CREATE INDEX event_templates_lookup_idx
ON event_templates(event_type, tone, usage_count, id);

INSERT INTO lore(id, category, name, text) VALUES
    ('section_7', 'organization', 'Секция 7',
     'Закрытое подразделение, внедряющее двойных агентов в независимые сети.'),
    ('colonel_vyazemsky', 'npc', 'Полковник Вяземский',
     'Исчезнувший куратор, чьи старые шифры снова появились в эфире.'),
    ('mole_hunt', 'story', 'Поиск крота',
     'Перехваченные сигналы указывают на внедрение Секции 7.');

INSERT INTO lore_tags(lore_id, tag) VALUES
    ('section_7', 'mole_hunt'),
    ('colonel_vyazemsky', 'mole_hunt'),
    ('mole_hunt', 'mole_hunt');
