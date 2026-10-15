# Spy Clicker: архитектура для Belmondo Bot

Статус: MVP 1–3 реализованы локально; production smoke остаётся отдельным gate  
Дата обновления: 2026-08-31  
Источник требований: `Telegram Spy Clicker — Lightweight Design Document.md`

## 1. Резюме решения

Spy Clicker встраивается в текущий процесс Belmondo Bot как отдельный пакет
`spy_game/`, а не как расширение существующего `games/`.

Причина разделения:

- `games/` обслуживает короткие LLM-сессии и хранит их в `chat_data` только до
  завершения процесса;
- Spy Clicker содержит долгоживущую экономику, фоновые события и должен
  переживать рестарты;
- источником истины Spy Clicker будут SQLite и application service, а не
  Telegram handler и не process memory.

В MVP остаётся один Python-процесс и существующий `python-telegram-bot`.
Встроенный `Application.job_queue` используется для периодического tick, SQLite
— для состояния. Redis, Celery, отдельные сервисы и LLM в первом релизе не
нужны.

```text
Telegram updates                         Application.job_queue
       |                                          |
       v                                          v
handlers/spy_game.py  ---------------->  spy_game/service.py
       |                               (use cases + validation)
       |                                          |
       |                              +-----------+-----------+
       |                              |                       |
       |                              v                       v
       |                     RuleBasedDirector         RewardResolver
       |                              |                       |
       +------------------------------+-----------+-----------+
                                                  |
                                                  v
                                      Unit of Work / repositories
                                                  |
                                                  v
                                               SQLite
```

## 2. Как решение встраивается в текущий репозиторий

### 2.1. Существующие точки интеграции

| Текущий компонент | Интеграция Spy Clicker |
|---|---|
| `app.py` | Composition root: создаёт runtime, запускает миграции, кладёт service в `application.bot_data`, регистрирует команды/callback и periodic job, закрывает runtime при shutdown. |
| `handlers/` | Новый тонкий Telegram-адаптер `handlers/spy_game.py`. SQL и расчёта наград здесь нет. |
| `handlers/messages.py` | Не меняется ради activity tracking. Отдельный handler в PTB group `-1` учитывает текстовую активность до потенциально долгого текущего dispatcher. |
| `state.py` | Экономика игры сюда не добавляется. Допустима только ссылка на process-wide runtime в `bot_data`. |
| `games/` | Не меняется. Короткие групповые игры продолжают использовать существующий `GameStore`. |
| `config.py` | Остаётся конфигурацией общих клиентов. Настройки и баланс Spy Clicker живут отдельно, чтобы не увеличивать связность файла. |
| `logger.py` | Используется существующий logger; события пишутся компактно, без полных Telegram update. |

Для callback используется отдельный prefix и отдельный handler pattern:

```text
spy:<action>:<event_id>
```

Пример: `spy:claim:8f2c1a903d4e`. В callback нет награды, цены или agent type.
Общий `button_godnoscope` остаётся последним catch-all callback handler.

### 2.2. Новые файлы

```text
belmondo_bot/
├── app.py                              # wiring и lifecycle
├── handlers/
│   └── spy_game.py                     # команды, callbacks, Telegram render
├── spy_game/
│   ├── __init__.py
│   ├── models.py                       # enum/dataclass, результаты use case
│   ├── settings.py                     # runtime flags и typed balance
│   ├── service.py                      # Telegram-independent application API
│   ├── director.py                     # RuleBased/LLM Director + fallback
│   ├── narrator.py                     # LLM prose + persistent cache/fallback
│   ├── rewards.py                      # RewardResolver, injected RNG
│   ├── activity.py                     # in-memory counters + periodic flush
│   ├── scheduler.py                    # один tick, без собственного loop
│   ├── database.py                     # SQLite executor, connection pragmas
│   ├── repositories.py                 # SQLite Unit of Work
│   └── migrations/
│       ├── 001_initial.sql
│       ├── 002_economy_history.sql
│       ├── 003_items_equipment.sql
│       ├── 004_event_participants.sql
│       ├── 005_story_narrative.sql
│       └── 006_agency_history.sql
└── tests/
    └── spy_game/
        ├── test_service.py
        ├── test_repositories.py
        ├── test_scheduler.py
        ├── test_handlers.py
        └── test_migrations.py
```

На MVP repositories собраны в одном файле. Разносить каждую таблицу в отдельный
модуль преждевременно. Граница замены SQLite задаётся `UnitOfWork`/Protocol, а не
количеством файлов.

## 3. Границы ответственности

### Telegram adapter

`handlers/spy_game.py` делает только следующее:

1. извлекает `chat_id`, `user_id`, username, `event_id` и action;
2. вызывает один метод `SpyGameService`;
3. преобразует типизированный result в сообщение или alert;
4. редактирует inline keyboard после успешного разрешения события.

Handler не доверяет callback payload и не определяет награду.

### Application service / Game Engine

Публичный async API первого релиза:

```python
class SpyGameService:
    async def initialize(self) -> None: ...
    async def close(self) -> None: ...
    async def enable_chat(self, chat_id: int, requested_by: int) -> EnableResult: ...
    async def disable_chat(self, chat_id: int, requested_by: int) -> EnableResult: ...
    async def record_activity(self, chat_id: int) -> None: ...
    async def tick(self, now: datetime) -> TickResult: ...
    async def attach_message(self, event_id: str, message_id: int) -> None: ...
    async def cancel_publication(self, event_id: str, reason: str) -> None: ...
    async def claim_event(self, command: ClaimCommand) -> ClaimResult: ...
    async def get_profile(self, user_id: int) -> Profile: ...
    async def get_agents(self, user_id: int) -> AgentInventory: ...
    async def exchange_with_handler(self, command: ExchangeCommand) -> ExchangeResult: ...
    async def increase_reputation(self, command: PrestigeCommand) -> PrestigeResult: ...
```

Ожидаемые игровые исходы (`won`, `already_resolved`, `expired`, `not_allowed`,
`insufficient_resources`) возвращаются как result type, а не логируются как
исключения. Исключения оставляются для DB/invariant failures.

`tick()` не вызывает Telegram API. Он возвращает DTO со списком expired
messages и подготовленных spawn events. PTB job callback в
`handlers/spy_game.py` публикует/редактирует сообщения и затем вызывает
`attach_message()` или `cancel_publication()`. Поэтому application service
остаётся Telegram-independent и тестируется без сетевых моков.

### Director и reward resolution

`RuleBasedDirector` получает компактный snapshot и выбирает только разрешённый
тип события/config ID. `RewardResolver` использует injected RNG и typed balance.
Оба компонента — pure Python и не знают о Telegram/SQLite.

Текущий registry содержит:

- `recruitment`;
- `dead_drop`;
- `handler`;
- `intercept`;
- `cooperative_operation`;
- `chase`;
- `npc` с config ID `recruiter`; прежние активные события с
  `operations_chief` или `counterintelligence` остаются совместимыми;
- `death_operation` с weighted selection и принудительным Recruitment после
  любого редкого события.

LLM Narrator реализован как opt-in слой и использует structured JSON helpers из
`games.llm`. Модель возвращает только художественное поле `body`; заголовок,
кнопка, длительность и награда остаются server-side. Общий timeout ограничивает
весь запрос с retry, после чего используется `TemplateNarrator`. `LLMDirector`
выбирает только значения из server-side allowlist и при любой ошибке передаёт
выбор `RuleBasedDirector`. Окончательная награда всегда определяется
`RewardResolver`.

### Persistence boundary

Публичные методы service асинхронны. Blocking `sqlite3` выполняется в одном
выделенном worker thread, что даёт предсказуемое потребление памяти на машине с
1 CPU и не блокирует Telegram event loop. Каждая операция открывает connection,
а транзакционный callback получает один и тот же connection на всю Unit of Work.

Такой дизайн не требует новой runtime-зависимости. При переходе на PostgreSQL
меняются Unit of Work и startup wiring, но не handler и доменная логика.

## 4. Authoritative state и модель данных

Прогресс пользователя глобален для бота, а не отдельный для каждого чата. Это
следует из ключей `user_agents(user_id, agent_type)` исходного LDD. События,
активность и сюжет остаются chat-scoped.

Все timestamps сохраняются как UTC ISO-8601 с timezone. В domain используются
только aware `datetime`.

### MVP schema

```sql
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
);

CREATE TABLE users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    display_name TEXT,
    reputation INTEGER NOT NULL DEFAULT 0 CHECK (reputation >= 0),
    agency_level INTEGER NOT NULL DEFAULT 0 CHECK (agency_level >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE user_agents (
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    agent_type TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK (amount >= 0),
    PRIMARY KEY (user_id, agent_type)
);

CREATE TABLE chat_state (
    chat_id INTEGER PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK (enabled IN (0, 1)),
    activity_score REAL NOT NULL DEFAULT 0 CHECK (activity_score >= 0),
    activity_updated_at TEXT NOT NULL,
    last_event_at TEXT,
    next_event_at TEXT,
    story_arc TEXT,
    story_stage INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE game_events (
    id TEXT PRIMARY KEY,
    chat_id INTEGER NOT NULL REFERENCES chat_state(chat_id),
    event_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'resolved', 'expired', 'cancelled')),
    winner_user_id INTEGER REFERENCES users(user_id),
    message_id INTEGER,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    resolved_at TEXT,
    CHECK (expires_at > created_at)
);

CREATE UNIQUE INDEX one_active_spy_event_per_chat
ON game_events(chat_id) WHERE status = 'active';

CREATE INDEX game_events_due_idx ON game_events(status, expires_at);
CREATE INDEX game_events_chat_created_idx ON game_events(chat_id, created_at DESC);

CREATE TABLE event_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    event_id TEXT NOT NULL REFERENCES game_events(id),
    chat_id INTEGER NOT NULL,
    user_id INTEGER,
    event_type TEXT NOT NULL,
    outcome TEXT NOT NULL,
    reward_type TEXT,
    reward_id TEXT,
    reward_amount INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE economy_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    action TEXT NOT NULL CHECK (action IN ('exchange', 'prestige')),
    source_event_id TEXT REFERENCES game_events(id),
    recipe_id TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE user_items (
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    item_type TEXT NOT NULL,
    amount INTEGER NOT NULL DEFAULT 0 CHECK (amount >= 0),
    PRIMARY KEY (user_id, item_type)
);

CREATE TABLE equipped_items (
    user_id INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    slot INTEGER NOT NULL CHECK (slot > 0),
    item_type TEXT NOT NULL,
    PRIMARY KEY (user_id, slot),
    UNIQUE (user_id, item_type)
);
```

Миграция `005` добавляет `story_summary`, `lore`, `lore_tags` и
`event_templates`; миграция `006` — append-only audit уровней агентства.

`agent_type`, `event_type` и JSON payload валидируются typed registry до записи
и после чтения. SQLite не является хранилищем произвольных механик.

### Миграции и connections

- SQL-файлы append-only, имя начинается с возрастающей версии.
- Миграции выполняются транзакционно при startup до запуска polling/jobs.
- Повторный startup идемпотентен благодаря `schema_migrations`.
- На каждом connection устанавливаются:
  `foreign_keys=ON`, `busy_timeout=5000`, `synchronous=NORMAL`.
- `journal_mode=WAL` устанавливается и проверяется при initialize.
- DB-файл, `-wal`, `-shm` и backup directory не коммитятся.

## 5. Критический flow: spawn и конкурентный claim

### Spawn

```text
job_queue tick
  -> flush activity counters
  -> expire overdue events
  -> transaction: reserve due enabled chat + create active event
  -> render predefined template
  -> Telegram send_message(inline keyboard)
  -> attach message_id
```

Если публикация не удалась, event переводится в `cancelled` с причиной. Если
процесс упал между DB create и Telegram
send, startup reconciliation отменяет active events без `message_id`. Если он
упал после send, но до attach, кнопка всё равно безопасна: callback содержит
event ID, а DB остаётся authoritative. Такой редкий orphan message будет
отклонён после reconciliation/expiration и не выдаст награду.

Tick не создаёт событие, если:

- глобальный feature flag выключен;
- чат не включён или не входит в beta allowlist;
- у чата уже есть active event;
- ни peak, ни inertia, ни random policy не выбрали текущий tick;
- общий cooldown не разрешает spawn.

### First-click claim

Один DB transaction выполняет весь экономический переход:

```text
BEGIN IMMEDIATE
  -> load event and verify chat/action/type/status/expiry
  -> UPSERT user identity
  -> atomic UPDATE active -> resolved with winner_user_id
  -> resolve reward from server-side config
  -> UPSERT user_agents amount = amount + reward
  -> INSERT event_history with unique idempotency_key
COMMIT
```

Фактический claim использует условный `UPDATE ... WHERE id = ? AND chat_id = ?
AND status = 'active' AND expires_at > ?`. Ровно один callback получает
`rowcount == 1`; остальные получают `already_resolved`. Reward и history не
могут закоммититься отдельно от winner.

После commit Recruitment handler под lock перечитывает authoritative progress и
список username из DB, затем редактирует исходное Telegram-сообщение `1/3 →
2/3 → 3/3`. Новое сообщение на каждый claim не создаётся. Публично выводятся
только `@username`; display name и numeric user ID не используются. Ошибка
Telegram API на этом шаге не откатывает выданную награду: повторный callback
получит authoritative result из DB, а не вторую награду.

## 6. Scheduler и activity tracking

Не создаётся отдельный asyncio loop или APScheduler instance. В `app.py`
регистрируется один `job_queue.run_repeating` со стабильным именем
`spy-game-tick` и `job_kwargs={"max_instances": 1}`.

Текстовые group messages от пользователей учитываются отдельным PTB handler в
group `-1`; сообщения ботов и команды пропускаются. Handler только увеличивает
in-memory counter `ActivityTracker`, поэтому быстро освобождает update для
текущей group `0`. SQLite не трогается на каждое сообщение. Tick атомарно
забирает накопленные counters и обновляет score с decay. Потеря нескольких
activity increments при crash приемлема, потеря экономики — нет. При глобальной
паузе бота activity не накапливается.

Расчёт задаётся typed balance config, например:

```text
decayed_score = persisted_score * decay(elapsed_time)
new_score = min(max_score, decayed_score + pending_messages * points_per_message)
```

Реализованные defaults:

- одно принятое сообщение = 1 point;
- один пользователь учитывается не чаще раза в 20 секунд;
- half-life score = 30 минут, cap = 100;
- профиль `balanced` по умолчанию: threshold = 5.5;
- свежий peak = минимум 3 принятых сообщения за текущий scheduler tick;
- inertia = окно 2 минуты после активности, шанс 1/2 на tick;
- random = средний интервал 90 минут на включённый чат;
- cooldown между событиями = 10 минут;
- spawn оставляет 45% score.

Peak создаёт событие в том же tick; накопленный score может дать только короткую
двухминутную инерцию. Random добавляет редкие фоновые сигналы. Выбранный канал
сохраняется как `trigger_reason`. `next_event_at` используется только как
транзакционный кандидат текущего tick и сбрасывается перед следующим; старые
отложенные таймеры очищает migration 007. Уникальный active-event index и общий
cooldown не допускают наложения и серии событий.

`chat_state.activity_profile` хранит выбранный master профиль отдельно для каждого
чата. `/spy_admin activity` показывает параметры, а `calm`, `balanced` и
`aggressive` меняют частоту без рестарта и без сброса накопленного score. `calm`
воспроизводит прежние значения `6 / 4 / 1 из 3 / 120 минут / 12 минут`;
`aggressive` использует `4.5 / 2 / 1 из 2 / 45 минут / 8 минут` и трёхминутное
окно inertia.

## 7. Настройки, баланс и beta safety

Runtime-настройки читаются один раз при startup из environment, а баланс хранится
в typed Python config. Секретов в этих значениях нет.

```text
SPY_GAME_ENABLED=false
SPY_GAME_DB_PATH=var/spy-game-dev.sqlite3
SPY_GAME_ALLOWED_CHAT_IDS=-100111,-100222
SPY_GAME_TICK_SECONDS=30
SPY_GAME_ALLOW_MANUAL_SPAWN=true
SPY_GAME_LLM_NARRATOR_ENABLED=false
SPY_GAME_LLM_NARRATOR_TIMEOUT_SECONDS=8
SPY_GAME_LLM_DIRECTOR_ENABLED=false
SPY_GAME_LLM_DIRECTOR_TIMEOUT_SECONDS=8
```

Правила:

- `SPY_GAME_ENABLED=false` — глобальный kill switch, jobs ничего не публикуют;
- allowlist обязателен и на dev, и в prod beta;
- `/spy_admin enable` включает текущий чат только если он находится в allowlist;
- `/spy_admin enable|disable|spawn|activity|status` доступны master; перед public rollout
  можно добавить Telegram-admin policy;
- manual spawn включается отдельным flag и по умолчанию запрещён в prod;
- dev и prod всегда используют разные DB path и bot token;
- невалидная конфигурация останавливает startup с понятной ошибкой, а не
  включает игру с default для production.

Текущий аргумент `mode` из `app.main` передаётся в `SpySettings`: он задаёт
безопасные dev/prod defaults, но environment имеет последнее слово. Production
default всегда `enabled=false` и `allow_manual_spawn=false`.

Typed balance содержит registry агентов Tier 1–6, event weights/cooldowns,
expiration, activity decay, Handler/NPC recipes, reputation и agency requirements.
Ни одно из этих значений не берётся из callback или narrative text.

Зафиксированный баланс MVP:

- Recruitment, Dead Drop, Intercept, Cooperative, Chase, Handler, NPC и Death
  Operation выбираются с весами `8:3:3:2:2:2:1:1`;
- `10 Informant -> 1 random Tier 2`;
- `15 Informant -> 1 deterministic Operative/Observer/Courier`;
- `2` агента каждого типа Tier 2 дают `1 random Tier 3`;
- следующий уровень reputation требует по `reputation + 1` каждого агента Tier 2;
- Recruitment выдаёт `1 + reputation` Informant с cap бонуса `5`;
- Dead Drop выдаёт item, Informant или пустой результат по server-side pool;
- у игрока три equipment slot; Wiretap даёт 20% шанс на дополнительного
  Informant при победе в Recruitment;
- Death Operation требует двухшагового подтверждения и ставит всех агентов
  игрока, но не items/equipment: шанс успеха `35%`, успех возвращает каждый
  тип агента в размере `×2` от ставки и добавляет случайного Tier 3, провал
  обнуляет агентурную сеть.
- Recruiter меняет `20 Informant` на weighted pool `70% Tier 2 / 25% Tier 3 /
  5% Tier 4`; уровень службы переносит по 5 процентных пунктов из common в rare;
- Operations Chief создаёт Tier 4/6 из явно показанной комбинации agent + item,
  Counterintelligence Officer выполняет только добровольные deterministic recipes;
- Handler, Recruiter, Operations Chief и Counterintelligence Officer доступны
  постоянно через «Контакты Центра». Их обычные обмены не требуют события,
  выполняются атомарно и защищены idempotency key от повторной доставки одного
  действия;
- каждый из этих NPC остаётся в пуле редких событий; первая успешная сделка
  закрывает событие и выдаёт удвоенный результат;
- уровень службы требует reputation, Intelligence Director, Resident и Illegal
  Agent; списывает только requirements, сбрасывает reputation и имеет cap `5`.

## 8. Команды и UX MVP

У игрока одна публичная команда `/spy`. Она открывает Rich Message с inline
кнопками «Досье», «Агенты», «Инвентарь», «Контакты», «Рейтинг», «Состояние сети»,
«Повысить репутацию», «Своя служба» и «Обновить». Отдельные
`/profile` и `/agents` не регистрируются.

У master одна команда `/spy_admin` с подкомандами:

- `enable` — opt-in разрешённого чата;
- `disable` — прекращает spawn и отменяет active event;
- `spawn [recruitment|dead_drop|intercept|cooperative_operation|chase|handler|npc|death_operation]` — принудительное
  событие для dev/smoke test;
- `status` — состояние сети, activity score и состояние trigger.

Handler exchange показывается inline-кнопками из события, а постоянные контакты
и reputation — из оперативного центра, не увеличивая число публичных команд.
Каждая кнопка вызывает server-side recipe по короткому ID; стоимость повторно
проверяется в transaction.

Первый MVP не смешивает Spy Clicker с `/game`: `/game` уже обещает пользователю
короткие session-based игры, а clicker является фоновой системой.

## 9. Ошибки, recovery и наблюдаемость

### Startup reconciliation

До запуска periodic job:

1. применить миграции;
2. expired active events перевести в `expired`;
3. active events без подтверждённого `message_id` перевести в `cancelled`;
4. проверить registry IDs в активных payload;
5. запустить tick только после успешного initialize.

### Logging

INFO:

- `spy_event_created event_id chat_id event_type expires_at`;
- `spy_event_resolved event_id chat_id winner_id reward_id reward_amount`;
- enable/disable и migration version.

WARNING/ERROR:

- publication/edit failures;
- DB busy после retry budget;
- invalid persisted payload/invariant;
- scheduler tick failure.

Username, тексты обычных сообщений и полный Telegram update не логируются.
Один неуспешный tick логируется и завершается; следующий tick продолжает работу.

### Backup

До production beta нужен консистентный backup через SQLite backup API, а не
копирование только основного файла при активном WAL. Минимум:

- backup перед каждой миграцией/deploy;
- daily backup;
- 7 daily + 4 weekly;
- периодический restore test в отдельный path;
- rollback кода не удаляет и не downgrade-ит DB автоматически.

## 10. Тестовая стратегия

### Unit

- RuleBasedDirector соблюдает allowlist, weights и anti-repeat policy;
- RewardResolver детерминирован с seeded/fake RNG;
- LLM Narrator принимает только `body`, отклоняет механику и падает в template
  fallback при invalid JSON/timeout;
- reputation/Handler recipes проверяют достаточность ресурсов;
- render не содержит экономических значений из callback.

### SQLite integration

- миграция с нуля и повторное применение;
- foreign keys/check constraints;
- claim создаёт user, агента и history в одной transaction;
- искусственная ошибка между claim и reward откатывает весь transaction;
- 20–50 одновременных claim дают ровно трёх разных winners, три history row и
  по одной награде каждому;
- duplicate callback не изменяет inventory;
- expired/wrong-chat/unknown-action callbacks не меняют состояние;
- restart сохраняет progress и восстанавливает scheduler state.

### Telegram adapter

- callback pattern не конфликтует с duel/game/roulette/godnoscope;
- `/spy` Rich menu и `/spy_admin` permissions;
- winner/loser/expired messages;
- send/edit failure не нарушает DB state.

### Regression и dev smoke

```text
.venv/bin/pytest -q
```

В dev allowlisted чате вручную проверить:

1. запуск с чистой DB и повторный restart;
2. `/spy_admin enable`, `/spy_admin spawn`, клик с четырёх аккаунтов;
3. первые три разных аккаунта получают reward, четвёртый — нет; повторный клик
   одного аккаунта не занимает новое место;
4. досье и агенты через `/spy` до и после restart;
5. expiration без клика;
6. `/spy_admin disable` и kill switch;
7. обычные `/game`, duel, AI replies и triggers не регрессировали.

## 11. План реализации и gates

### Этап A — storage skeleton (реализован)

- package, models, typed settings;
- SQLite executor, migration runner, `001_initial.sql`;
- Unit of Work и repository integration tests.

Gate: чистая/повторная миграция и transaction rollback проходят локально.

### Этап B — минимальный вертикальный slice (реализован)

- Recruitment registry/template;
- `RuleBasedDirector`, `RewardResolver`;
- create/publish/attach;
- concurrent-safe `claim_event`;
- callback handler и история.

Gate: concurrency integration test доказывает ровно три distinct-награды.

### Этап C — работающая фоновая игра (реализован локально)

- `/spy` Rich menu с досье, агентами и состоянием сети;
- expiration/reconciliation;
- activity aggregation и scheduler tick;
- enable/disable/manual spawn, feature flag и allowlist.

Gate: полный pytest green и dev smoke после restart.

### Этап D — полный MVP из LDD (реализован локально)

- Handler upgrade event;
- reputation transaction;
- Tier 1–3 balance config;
- leaderboard только если он не задерживает beta.

Gate: economy invariants покрыты integration-тестами; после deploy требуется
ручной сценарий расхода/upgrade/prestige в dev beta.

### Этап E — production beta

- production DB path и права на directory;
- pre-deploy backup и restore check;
- миграции на копии production DB;
- deploy с `SPY_GAME_ENABLED=false`;
- smoke существующих функций;
- включение flag только для одного allowlisted beta chat;
- наблюдение логов/DB invariants, затем постепенное расширение allowlist.

### Этап F — MVP 2 (реализован локально)

- items registry и persistent inventory — реализовано;
- три equipment slot и Wiretap modifier — реализовано;
- конкурентный Dead Drop с item/agent/empty pool — реализовано;
- редкая Death Operation с persistent confirmation, all-in ставкой и
  атомарным исходом — реализовано;
- Intercept с несколькими вариантами ответа — реализовано;
- cooperative event с distinct participants — реализовано;
- двухэтапный Chase с разными победителями стадий — реализовано;
- Recruiter, Operations Chief и Counterintelligence Officer — реализованы;
- lightweight story state и сюжетная ветка из четырёх стадий — реализованы;
- leaderboard и небольшой Endgame через agency level — реализованы.

### Этап G — MVP 3 / narrative (реализован локально)

- strict `LLMDirector` с `RuleBasedDirector` fallback;
- story-aware Director snapshot и server-side allowlist;
- lore, story summary и persistent narrative template cache;
- `LLMNarrator` остаётся неавторитетным для правил и экономики.

Rollback MVP: выключить `SPY_GAME_ENABLED`, перезапустить процесс и при
необходимости откатить код. DB сохраняется; destructive down migration не
выполняется. Production deploy/rollback и backup описаны в
`docs/runbooks/spy-game-server-rollout.md`.

## 12. Acceptance criteria первой beta

1. Игра включается только глобальным flag + chat allowlist + chat opt-in.
2. Restart не теряет users, agents, events, history и next schedule.
3. Для RecruitmentEvent конкурентные callback выдают по одной награде первым
   трём разным пользователям; повторные callback не меняют inventory, а прогресс
   редактируется в одном исходном сообщении без display names.
4. Claim, reward и history атомарны.
5. Handler/event NPC/permanent contact exchange, prestige и agency progression
   атомарны и оставляют audit.
6. Handler не содержит SQL, RNG или balance logic.
7. LLM/API outage не влияет на core game: Director и Narrator имеют timeout и
   обязательные локальные fallback.
8. Полный текущий test suite и новый spy-game suite проходят.
9. Dev smoke завершён до production deploy.
10. Перед beta проверены backup и rollback kill switch.
11. Обычные функции Belmondo Bot продолжают работать без изменения UX.

## 13. Решения, которые можно отложить без блокировки production beta

- один или несколько beta chats после первой smoke-проверки;
- окончательный rebalance после telemetry beta.

Дальнейший rebalance меняет typed config и не затрагивает transaction boundary.
