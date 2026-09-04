# Belmondo Bot

**Belmondo Bot** — Telegram-бот для развлечения и ответов в чатах. Назван в честь Жан-Поля Бельмондо: гороскопы, цитаты, кубики, триггеры из JSON и ответы нейросетью в образе Жосслена Бомона.

## Основные возможности

### Развлечения

- **Гороскопы** — выбор знака через `/horoscope` (канал Godnoscop), длинная рассылка с Mail.ru через `/horoscope_mail`
- **AI-гороскоп** — `/ai_horoscope` (генерация по эталонным текстам и промпту в `const.py`)
- **Таро** — `/tarot` (три карты + интерпретация от модели; колода подгружается один раз на старте)
- **Предсказание на день** — `/magic_prediction` (1–2 предложения с французской фразой)
- **Дуэль профессионалов** — `/duel @user` или `/duel` reply-сообщением; ставка по умолчанию — один осведомитель, доступны также ставки `3` и `5`
- **Игровое меню** — `/game`: семь групповых LLM-игр и «Рулетка»
- **Spy Clicker** — `/spy`: фоновая шпионская сеть, события по активности чата, профиль и агенты через inline-меню
- **Цитаты** — `/quote`
- **Кубики** — `/roll` и текстовые фразы с «кубик»
- **Goblin** — `/goblin` (случайный контент из `img/goblin/`)
- **Oxxxy** — `/oxxxy` (ссылки из `oxxxy_urls.py`)

### Медиа и триггеры

- **Стикеры и картинки** — по ключевым словам и правилам в `speaking/triggers.json` (`triggers.py`)
- **Реакции в коде** — слон, колокола, нацист, джекпот, «спокойной ночи» и др. (см. пакет `processors/`)

### Прочее

- **Завод/офис дня** — `/zavod` (состояние хранится отдельно для каждого чата)
- **Стикер дня недели** — `/day`
- **Праздники** — `/holiday` (нужен `holidays.json` в корне; иначе бот вернёт сообщение об отсутствии файла)
- **Нейросеть в реплаях** — ответ на реплай сообщения бота; контекст диалога хранится отдельно для каждого чата, параллельные запросы сериализуются через `asyncio.Lock`, `/clear_context` сбрасывает контекст

### Доступ и безопасность

- AI-функции (`/ai_horoscope`, `/tarot`, `/magic_prediction`, ответы в реплаях, LLM-игры) работают только в чатах, где состоит владелец бота (`master` в `state.py`). Проверка кэшируется на 60 секунд на чат.
- Команда `/pause` доступна только владельцу; в паузе пропускаются все декорированные `@pause` хендлеры.

### AI

- Ответы в стиле персонажа из `const.py`, клиент OpenAI-совместимый API (по умолчанию — DeepSeek; ключ в `auth.conf`).

## Установка и настройка

### Требования

- Python 3.10+ (текущая `.venv` в проекте используется для тестов)
- Токен Telegram-бота
- Ключ API для LLM (как в `auth.conf`)
- Доступ в интернет (гороскопы с сайтов, API)

### Клонирование и зависимости

```bash
git clone <url-репозитория>
cd belmondo_bot
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### Конфигурация `auth.conf`

В корне проекта создайте файл (секция `auth` должна содержать как минимум ключ для модели; для модуля Godnoscop через Telethon — поля `api_id`, `api_hash`, `phone` и путь к данным — см. `godnoscop/godnoscop_tracker.py`):

```ini
[auth]
openai_api_key = ваш_ключ_API

[paths]
gonoscopes_path = godnoscopes.json
```

Токен бота при запуске передаётся аргументом (см. ниже), не обязательно дублировать его в `auth.conf`, если вы так не настраивали.

## Режимы и запуск

- `mode`: `dev` (подставляется dev-ID бота из `const.py`) или `prod`
- `spam_mode`: влияет на вероятность срабатывания триггеров из `triggers.json` — `soft` / `medium` / `rare` / `chaos`

```bash
.venv/bin/python main.py --mode=dev --spam_mode=medium --token=ВАШ_TELEGRAM_BOT_TOKEN
```

`main.py` — тонкий shim поверх `app.run_bot`; вся логика запуска живёт в `app.py`.

### Spy Clicker

По умолчанию фоновая игра выключена. Для dev/beta задайте allowlist и отдельный
путь к SQLite:

```bash
export SPY_GAME_ENABLED=true
export SPY_GAME_ALLOWED_CHAT_IDS=-1001234567890
export SPY_GAME_DB_PATH=var/spy-game-dev.sqlite3
export SPY_GAME_ALLOW_MANUAL_SPAWN=true
export SPY_GAME_LLM_NARRATOR_ENABLED=true
export SPY_GAME_LLM_NARRATOR_TIMEOUT_SECONDS=8
export SPY_GAME_LLM_DIRECTOR_ENABLED=false
export SPY_GAME_LLM_DIRECTOR_TIMEOUT_SECONDS=8

# Telegram Mini App (опционально; сначала настройте HTTPS URL в BotFather)
export SPY_GAME_WEBAPP_ENABLED=false
export SPY_GAME_WEBAPP_HOST=127.0.0.1
export SPY_GAME_WEBAPP_PORT=8080
export SPY_GAME_WEBAPP_LAUNCH_URL=https://t.me/<bot_username>/<short_name>
```

После запуска master включает сеть командой `/spy_admin enable`. В production
ручной spawn по умолчанию запрещён; глобальный `SPY_GAME_ENABLED=false` служит
kill switch. LLM Narrator и LLM Director включаются независимо; при любой
ошибке используются локальные шаблоны и RuleBasedDirector.

## Структура проекта

```
belmondo_bot/
├── main.py                 # Тонкий shim: делегирует в app.run_bot (fire)
├── app.py                  # Сборка Application, регистрация хендлеров, polling
├── config.py               # OpenAI client, logger, tz, ConfigParser
├── state.py                # vars_dict + ensure_chat_state(context) для per-chat state
├── const.py                # Константы, ID, промпты (без мутабельного состояния)
├── guards.py               # @pause + ensure_master_in_chat_for_ai (TTL-кэш)
├── telegram_utils.py       # parse_stream, send_long_message
├── triggers.py             # Загрузка speaking/triggers.json, выбор ответа, отправка медиа
├── utils.py                # Спам-задержки, td_convert, roll_custom_dice, …
├── logger.py               # Настройка логирования
├── horoscope.py            # Парсинг гороскопов, промпты для AI-гороскопа и таро
├── quotes.py               # Цитаты для /quote
├── site_parser.py          # Праздники (holidays.json)
├── oxxxy_urls.py           # Плейлист Oxxxy
├── tarot_cards.json        # Колода для /tarot
├── handlers/
│   ├── ai.py               # process_ai_response, ai_horoscope, tarot, magic_prediction, clear_context
│   ├── commands.py         # quote, roll_dice, paused
│   ├── content.py          # send_oxxxy, send_goblin, send_morning, show_day, show_holidays
│   ├── duel.py             # AI-сценарий, вызов, защищённые кнопки и вердикт дуэли
│   ├── games.py            # LLM group games: lobby, callbacks, reply moves, timeouts
│   ├── godnoscope.py       # godnoscope, button_godnoscope, get_horoscope
│   ├── roulette.py         # Рулетка с inline-кнопкой
│   ├── spy_game.py         # Rich menu, фоновые события, callbacks и master UI
│   └── messages.py         # parse_message, spam_gif_detector, delete_dice
├── games/
│   ├── engine.py           # GameState, фазы, submit/join/start/timeout lifecycle
│   ├── store.py            # GameStore: одна активная групповая игра на чат
│   ├── llm.py              # structured JSON requests, retry, untrusted JSON helpers
│   ├── scenarios.py        # alibi, operation, pitch prompts and formatters
│   └── base.py             # dict-helpers для сценарных snapshot-структур
├── spy_game/
│   ├── service.py          # Telegram-independent use cases
│   ├── director.py         # RuleBased/LLM Director + strict fallback
│   ├── narrator.py         # Structured LLM prose + persistent cache/fallback
│   ├── rewards.py          # Server-side reward resolution
│   ├── repositories.py     # Atomic SQLite operations
│   ├── scheduler.py        # Activity decay и интервалы событий
│   ├── activity.py         # In-memory aggregation с anti-spam debounce
│   ├── settings.py         # Feature flags, allowlist и typed balance
│   └── migrations/         # Append-only SQLite migrations
├── processors/
│   ├── bots.py             # process_bot_messages
│   ├── media.py            # process_media_responses, sticker/zalupa/jackpot
│   ├── spells.py           # process_diarrhea_spell, pot_drinking, special_commands
│   └── squad.py            # process_men_squad_message
├── speaking/
│   └── triggers.json       # Триггеры и ответы
├── godnoscop/
│   └── godnoscop_tracker.py
├── img/                    # Медиа для реакций и goblin
├── auth.conf               # Секреты (не коммитить)
├── pytest.ini              # pythonpath = . для локальных тестов
├── requirements.txt
├── LICENSE
└── README.md
```

### Хранение состояния

- **Process-wide** (`application.bot_data`, инициализируется из `state.vars_dict`): `paused`, `spam_mode`, `master`, `self_id`, `self_id_dev`, загруженная колода `tarot_deck`.
- **Per-chat** (`context.chat_data`, лениво через `state.ensure_chat_state`): история AI-диалога, антиспам, состояние завода, реестр известных пользователей, текущая дуэль, рулетка и активная LLM-игра.
- Для LLM-игр отдельно хранятся `llm_games` (`GameState` по chat id), `llm_game_sessions` (имена игроков, контент раундов, operation token) и `llm_game_lock` для сериализации кликов, reply-ответов, таймаутов и фоновых LLM-переходов.
- **Persistent Spy Clicker**: SQLite хранит пользователей, агентов, предметы, уровень службы, состояние чатов, события, сюжет и историю. В памяти остаются только безопасные к потере счётчики свежей активности.

## Команды бота

| Команда             | Описание                                                     |
| ------------------- | ------------------------------------------------------------ |
| `/quote`            | Случайная цитата                                             |
| `/horoscope`        | Клавиатура знаков → гороскоп через Godnoscop                 |
| `/horoscope_mail`   | Два сообщения: гороскопы всех знаков с Mail.ru               |
| `/ai_horoscope`     | Сгенерированный AI-гороскоп                                  |
| `/tarot`            | Расклад таро + текст от модели                               |
| `/magic_prediction` | Шуточное предсказание на день                                |
| `/duel @user [ставка]` | Вызвать на дуэль; ставка Spy Clicker: 1, 3 или 5 осведомителей |
| `/duel_cancel`      | Отменить зависшую дуэль участником или владельцем бота       |
| `/game`             | Меню игр: семь групповых LLM-игр и рулетка                   |
| `/game_cancel`      | Отменить активную групповую LLM-игру                         |
| `/spy`              | Rich-меню Spy Clicker: досье, агенты и состояние сети        |
| `/spy_admin …`      | Управление Spy Clicker; только master                        |
| `/goblin`           | Случайный goblin-контент                                     |
| `/oxxxy`            | Случайная ссылка на плейлист                                 |
| `/day`              | Стикер дня недели (`img/eva/`)                               |
| `/holiday`          | Праздники на сегодня                                         |
| `/zavod`            | Завод/офис дня                                               |
| `/roll`             | Бросок кубика Telegram                                       |
| `/pause`            | Вкл/выкл паузу обработки (только для владельца)              |
| `/clear_context`    | Сброс `chat_deque` текущего чата                             |

### Дуэль профессионалов

Вызвать участника можно reply-командой `/duel` на его сообщение или командой
`/duel @username`. Для обычного упоминания бот сначала проверяет точный username,
а при принятии вызова привязывает к дуэли числовой Telegram user ID.

Только вызванный пользователь может принять или отклонить вызов. После принятия
ходы могут выбирать только два дуэлянта, по одному разу каждый. Дуэли доступны
в чате с включённым Spy Clicker и всегда используют ставку: без суммы команда
ставит одного осведомителя, явно можно указать `1`, `3` или `5`. Примеры:
`/duel @username`, `/duel @username 3` и reply-команда `/duel 5`.

Ставка вызывающего резервируется при создании вызова, ставка соперника — при
принятии. Победитель получает весь банк. Исход определяет серверная таблица
взаимодействий пяти ходов; AI создаёт сцену и озвучивает результат, но не может
поменять победителя. На ход даётся 180 секунд: если успел только один, он
получает техническую победу, если не успел никто — обе ставки возвращаются.
Отказ, отмена непринятого вызова и 120-секундный таймаут принятия также
возвращают escrow. Участник или владелец бота может закрыть дуэль через
`/duel_cancel`.

### Spy Clicker

У игроков одна команда — `/spy`. При включённом Mini App первой кнопкой
открывается web-оперативный центр: досье, агенты, инвентарь, рейтинг,
экипировка, репутация и собственная служба. Прежнее Telegram inline-меню
остаётся в том же сообщении как fallback. Групповые события, их кнопки и
`/spy_admin` не переносятся из чата. Обычные
сообщения пользователей повышают активность чата; повторные сообщения одного
пользователя учитываются не чаще раза в 20 секунд. Scheduler раз в 30 секунд
проверяет три канала по приоритету:

- **peak**: score не ниже 5.5 и минимум три принятых сообщения в текущем tick;
- **inertia**: score не ниже 5.5, с последней активности прошло не больше двух
  минут, вероятность срабатывания 1/2 на tick;
- **random**: фоновый сигнал со средним интервалом около 90 минут.

Peak создаёт событие в том же tick. Инерция даёт короткий живой хвост беседы,
но не переносит гарантированный сигнал на десятки минут вперёд.

Score имеет half-life 30 минут. Все три канала используют общий 10-минутный cooldown,
а после публикации остаётся 45% накопленной активности, поэтому события не
появляются серией. Master может отдельно для каждого чата переключить профиль
частоты через `/spy_admin activity calm|balanced|aggressive`; выбранный профиль
хранится в SQLite и не сбрасывается после рестарта. Первые три разных пользователя
получают по агенту; каждый claim, награда и history фиксируются одной SQLite
transaction. Исходное сообщение Recruitment редактируется на месте: прогресс
меняется от `0/3` до `3/3`, новые сообщения на каждый claim не создаются. В
публичном списке участников используются только `@username`; имя профиля не
показывается. Повторный клик не занимает дополнительное место. Registry также включает Dead Drop, Intercept, Chase, cooperative
operation, Handler, Рекрутера и двухшаговую «Смертельную операцию».
Начальник операций и Контрразведка доступны постоянно через «Контакты Центра»
в Mini App и inline fallback. Случайным NPC-событием остаётся Рекрутер.

При `SPY_GAME_LLM_NARRATOR_ENABLED=true` художественная завязка события
генерируется через существующий structured JSON transport из `games/llm.py`.
Модель возвращает только поле `body`: заголовок, кнопка, срок и экономика
формируются сервером. Невалидный текст, API error или timeout автоматически
заменяется локальным шаблоном и не отменяет событие.

При `SPY_GAME_LLM_DIRECTOR_ENABLED=true` модель выбирает только event type,
tone, известный story hook и intensity из серверного allowlist. Невалидный
ответ или timeout автоматически передаёт выбор `RuleBasedDirector`. Экономика,
победители и SQL недоступны обоим LLM-слоям.

Master использует одну команду с подкомандами:

```text
/spy_admin status
/spy_admin enable
/spy_admin disable
/spy_admin spawn [recruitment|dead_drop|intercept|cooperative_operation|chase|handler|npc|death_operation]
/spy_admin activity [calm|balanced|aggressive]
```

`spawn` дополнительно требует `SPY_GAME_ALLOW_MANUAL_SPAWN=true`.
Без аргумента `activity` показывает текущий профиль и его фактические параметры.
`calm` сохраняет прежнюю редкую частоту, `balanced` используется по умолчанию,
а `aggressive` предназначен для тихих чатов.

Mini App обслуживается тем же Python-процессом и использует тот же
`SpyGameService` и SQLite executor. Backend проверяет подпись и возраст
`Telegram.WebApp.initData`; числовой `user_id` берётся только из проверенных
данных Telegram. Кнопка группового `/spy` содержит короткоживущий подписанный
`startapp`-контекст, привязанный к пользователю и чату. Поэтому запуск из профиля
бота доступен только для чтения, а экипировка, повышение репутации и учреждение
службы разрешены после запуска из активированного группового чата. Публичный
HTTPS обычно завершается на nginx, приложение слушает только
`127.0.0.1:8080`. Подробности — в
[`docs/runbooks/spy-game-webapp-rollout.md`](docs/runbooks/spy-game-webapp-rollout.md).

События «Перехват» и «Тайник» опционально запускаются как Telegram HTML5 Game с
short name `spies`. В Перехвате игрок настраивает пять частот; в Тайнике —
подбирает трёхзначный код по серверным подсказкам за пять минут без ограничения
числа проверок. Для каждой операции SQLite хранит одну игровую сессию на
пользователя, а награду атомарно получает первый победитель. Без
`SPY_GAME_HTML5_URL` сохраняются прежние Telegram-кнопки.
Инструкция по включению и beta smoke находится в
[`docs/runbooks/spy-game-html5-intercept.md`](docs/runbooks/spy-game-html5-intercept.md).

### Групповые LLM-игры

Команда `/game` показывает меню с семью групповыми LLM-играми: «Шпионская операция», «Создай алиби», «Продай это Бельмондо», «Погоня на чём попало», «Кастинг злодеев», «Ограбление за 12 франков» и «Объясните прессе». Последней кнопкой остаётся «Рулетка». Выбор LLM-игры создаёт лобби в текущем чате. Автор лобби запускает игру после набора минимум двух участников; максимум — восемь. Все игровые ответы принимаются только reply-сообщениями на текущий prompt раунда, поэтому обычные триггеры и AI-реплаи не перехватывают ход игрока.

Игровой lifecycle общий для всех сценариев:

1. Лобби с кнопками «Участвовать», «Начать», «Отмена».
2. Первый раунд: модель генерирует завязку и общий вопрос/задачу.
3. Второй раунд: модель формирует уточнение, осложнение или новое требование.
4. Вердикт: модель выбирает победителя и номинации.

Под капотом используется `GameState` с фазами `lobby`, `round_one`, `round_two`, `judging`, `finished`. Долгие LLM-вызовы запускаются фоновыми задачами через `application.create_task`, ограничены таймаутом и защищены `operation_token`, чтобы старые ответы модели не могли перезаписать уже изменившуюся игру. Если игроки не ответили до таймаута раунда, дальше проходят только ответившие; если пригодных ответов меньше двух, партия завершается без победителя.

Данные игроков и их ответы передаются модели как недоверенный JSON-блок (`<untrusted_json>`) с экранированием разделителей. Это защищает схему ответа и системные инструкции от prompt injection внутри игровых реплик.

## Автоматические реакции (фрагменты)

В тексте сообщений (не команды), после нормализации строки:

- **«дембель»** — таймер до даты дембеля в коде
- **«страшно жить»** — шуточный ответ
- **«кубик»** — кубик или текст с результатом
- **«рулетка»** — запускает мини-игру с одной inline-кнопкой на чат; та же игра доступна через `/game`
- **«слон»**, **«колокола»**, **«нацист»**, **«джекпот»**, пожелания спокойной ночи и др. — см. пакет `processors/`

## Система триггеров (`speaking/triggers.json`)

Формат блока:

```json
{
  "trigger_name": {
    "triggers": ["слово1", "слово2"],
    "answers": ["ответ1", "ответ2"],
    "exclude_words": [],
    "prob": -1,
    "exclude_uids": [],
    "exact": false
  }
}
```

Типы значений в `answers`:

- обычный текст;
- `**img:путь/к/файлу**` — фото (или стикер для `.webp` в смешанных сценариях, см. `process_trigger_response`);
- `**sticker:путь**` — стикер;
- несколько частей через `**|**` — смешанный ответ.

Поля: `triggers`, `answers`, `exclude_words`, `prob` (для `-1` вероятность берётся из `spam_mode`), `exclude_uids`, `exact`.

После правок JSON перезапустите бота.

## Тесты

В проекте есть `pytest.ini`, поэтому тесты можно запускать из корня без ручного `PYTHONPATH`:

```bash
.venv/bin/pytest -q
```

Игровой слой покрыт отдельно:

```bash
.venv/bin/pytest -q tests/test_games_engine.py tests/test_games_handlers.py tests/test_games_llm.py tests/test_games_scenarios.py
```

Полный прогон выполняется командой выше; остаётся предупреждение `pytz` о deprecated `utcfromtimestamp`.

## Устранение неполадок

1. **Бот молчит** — проверьте токен в аргументах запуска, состояние `/pause`, логи в консоли.
2. **Нет картинок** — пути в триггерах и наличие файлов в `img/`.
3. **AI не отвечает** — ключ в `auth.conf`, сеть, лимиты API. Для реплаев: отвечать нужно именно боту, пользователь не должен быть в `excluded_uids` в `const.py`, а владелец бота (`master`) должен состоять в чате.
4. **LLM-игра не стартует** — нужны минимум 2 участника, владелец бота должен быть в чате, а `JobQueue` должен быть доступен в `python-telegram-bot` application.
5. **Ход в игре не принимается** — ответ должен быть reply именно на сообщение текущего раунда; повторный ход в том же раунде отклоняется.
6. **`/holiday`** — нужен файл `holidays.json` в рабочей директории бота.
7. **`/tarot` пишет «Колода таро не загружена»** — не нашёлся `tarot_cards.json` при старте; проверьте рабочую директорию и логи старта.

Логи настраиваются в `logger.py` (по умолчанию вывод в stdout).

## Лицензия и авторы

Проект под лицензией [WTFPL](LICENSE).

**Автор:** dawgwelder

---

Belmondo Bot — развлекательный бот для чата.
