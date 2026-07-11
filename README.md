# Belmondo Bot

**Belmondo Bot** — Telegram-бот для развлечения и ответов в чатах. Назван в честь Жан-Поля Бельмондо: гороскопы, цитаты, кубики, триггеры из JSON и ответы нейросетью в образе Жосслена Бомона.

## Основные возможности

### Развлечения

- **Гороскопы** — выбор знака через `/horoscope` (канал Godnoscop), длинная рассылка с Mail.ru через `/horoscope_mail`
- **AI-гороскоп** — `/ai_horoscope` (генерация по эталонным текстам и промпту в `const.py`)
- **Таро** — `/tarot` (три карты + интерпретация от модели; колода подгружается один раз на старте)
- **Предсказание на день** — `/magic_prediction` (1–2 предложения с французской фразой)
- **Дуэль профессионалов** — `/duel @user` или `/duel` reply-сообщением: AI создаёт сцену и определяет победителя по тайным ходам игроков
- **Групповые LLM-игры** — `/alibi`, `/operation`, `/pitch`: лобби на 2–8 игроков, два раунда ответов reply-сообщениями и финальный вердикт модели
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
│   ├── roulette.py         # Текстовая рулетка с inline-кнопкой
│   └── messages.py         # parse_message, spam_gif_detector, delete_dice
├── games/
│   ├── engine.py           # GameState, фазы, submit/join/start/timeout lifecycle
│   ├── store.py            # GameStore: одна активная групповая игра на чат
│   ├── llm.py              # structured JSON requests, retry, untrusted JSON helpers
│   ├── scenarios.py        # alibi, operation, pitch prompts and formatters
│   └── base.py             # dict-helpers для сценарных snapshot-структур
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

## Команды бота

| Команда             | Описание                                                     |
| ------------------- | ------------------------------------------------------------ |
| `/quote`            | Случайная цитата                                             |
| `/horoscope`        | Клавиатура знаков → гороскоп через Godnoscop                 |
| `/horoscope_mail`   | Два сообщения: гороскопы всех знаков с Mail.ru               |
| `/ai_horoscope`     | Сгенерированный AI-гороскоп                                  |
| `/tarot`            | Расклад таро + текст от модели                               |
| `/magic_prediction` | Шуточное предсказание на день                                |
| `/duel @user`       | Вызвать участника на AI-дуэль; надёжнее всего reply-командой |
| `/duel_cancel`      | Отменить зависшую дуэль участником или владельцем бота       |
| `/alibi`            | Групповая игра «Алиби, месье» с LLM-гейм-мастером            |
| `/operation`        | Групповая игра «Невозможная операция»                        |
| `/pitch`            | Групповая игра «Продай это Бельмондо»                        |
| `/game_cancel`      | Отменить активную групповую LLM-игру                         |
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
ходы могут выбирать только два дуэлянта, по одному разу каждый. AI-гейм-мастер
создаёт сцену и выносит финальный вердикт; при ошибке AI используется локальный
fallback, чтобы игра не зависала. Одновременно в чате может идти одна дуэль.
Участник или владелец бота может принудительно закрыть её через `/duel_cancel`.
Если вызов не принят за 120 секунд, он автоматически закрывается.

### Групповые LLM-игры

Команды `/alibi`, `/operation` и `/pitch` создают лобби в текущем чате. Автор лобби запускает игру после набора минимум двух участников; максимум — восемь. Все игровые ответы принимаются только reply-сообщениями на текущий prompt раунда, поэтому обычные триггеры и AI-реплаи не перехватывают ход игрока.

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
- **«рулетка»** — запускает мини-игру с одной inline-кнопкой на чат
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

Текущий полный прогон: `32 passed` (остаётся предупреждение `pytz` о deprecated `utcfromtimestamp`).

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
