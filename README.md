# Belmondo Bot

**Belmondo Bot** — Telegram-бот для развлечения и ответов в чатах. Назван в честь Жан-Поля Бельмондо: гороскопы, цитаты, кубики, триггеры из JSON и ответы нейросетью в образе Жосслена Бомона.

## Основные возможности

### Развлечения

- **Гороскопы** — выбор знака через `/horoscope` (канал Godnoscop), длинная рассылка с Mail.ru через `/horoscope_mail`
- **AI-гороскоп** — `/ai_horoscope` (генерация по эталонным текстам и промпту в `const.py`)
- **Таро** — `/tarot` (три карты + интерпретация от модели; колода подгружается один раз на старте)
- **Предсказание на день** — `/magic_prediction` (1–2 предложения с французской фразой)
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

- AI-функции (`/ai_horoscope`, `/tarot`, `/magic_prediction`, ответы в реплаях) работают только в чатах, где состоит владелец бота (`master` в `state.py`). Проверка кэшируется на 60 секунд на чат.
- Команда `/pause` доступна только владельцу; в паузе пропускаются все декорированные `@pause` хендлеры.

### AI

- Ответы в стиле персонажа из `const.py`, клиент OpenAI-совместимый API (по умолчанию — DeepSeek; ключ в `auth.conf`).

## Установка и настройка

### Требования

- Python 3.8+
- Токен Telegram-бота
- Ключ API для LLM (как в `auth.conf`)
- Доступ в интернет (гороскопы с сайтов, API)

### Клонирование и зависимости

```bash
git clone <url-репозитория>
cd belmondo_bot
pip install -r requirements.txt
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

- `**mode**`: `dev` (подставляется dev-ID бота из `const.py`) или `prod`
- `**spam_mode**`: влияет на вероятность срабатывания триггеров из `triggers.json` — `soft` / `medium` / `rare` / `chaos`

```bash
python main.py --mode=dev --spam_mode=medium --token=ВАШ_TELEGRAM_BOT_TOKEN
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
│   ├── godnoscope.py       # godnoscope, button_godnoscope, get_horoscope
│   └── messages.py         # parse_message, spam_gif_detector, delete_dice
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
├── requirements.txt
├── LICENSE
└── README.md
```

### Хранение состояния

- **Процесс-wide** (`application.bot_data`, инициализируется из `state.vars_dict`): `paused`, `spam_mode`, `master`, `self_id`, `self_id_dev`, загруженная колода `tarot_deck`.
- **Per-chat** (`context.chat_data`, лениво через `state.ensure_chat_state`): `chat_deque` (история AI-диалога), `msg_deque` (анти-спам GIF), `horoscope_history`, `spam_stopper` (анти-спам текст), `ai_lock` (сериализация AI-запросов), `dt` / `ZAVOD_CHECK` / `username` (состояние завода), `zavod_text`.

## Команды бота

| Команда             | Описание                                                     |
| ------------------- | ------------------------------------------------------------ |
| `/quote`            | Случайная цитата                                             |
| `/horoscope`        | Клавиатура знаков → гороскоп через Godnoscop                 |
| `/horoscope_mail`   | Два сообщения: гороскопы всех знаков с Mail.ru               |
| `/ai_horoscope`     | Сгенерированный AI-гороскоп                                  |
| `/tarot`            | Расклад таро + текст от модели                               |
| `/magic_prediction` | Шуточное предсказание на день                                |
| `/goblin`           | Случайный goblin-контент                                     |
| `/oxxxy`            | Случайная ссылка на плейлист                                 |
| `/day`              | Стикер дня недели (`img/eva/`)                               |
| `/holiday`          | Праздники на сегодня                                         |
| `/zavod`            | Завод/офис дня                                               |
| `/roll`             | Бросок кубика Telegram                                       |
| `/pause`            | Вкл/выкл паузу обработки (только для владельца)              |
| `/clear_context`    | Сброс `chat_deque` текущего чата                             |

## Автоматические реакции (фрагменты)

В тексте сообщений (не команды), после нормализации строки:

- **«дембель»** — таймер до даты дембеля в коде
- **«страшно жить»** — шуточный ответ
- **«кубик»** — кубик или текст с результатом
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

## Устранение неполадок

1. **Бот молчит** — проверьте токен в аргументах запуска, состояние `/pause`, логи в консоли.
2. **Нет картинок** — пути в триггерах и наличие файлов в `img/`.
3. **AI не отвечает** — ключ в `auth.conf`, сеть, лимиты API. Для реплаев: отвечать нужно именно боту, пользователь не должен быть в `excluded_uids` в `const.py`, а владелец бота (`master`) должен состоять в чате.
4. **`/holiday`** — нужен файл `holidays.json` в рабочей директории бота.
5. **`/tarot` пишет «Колода таро не загружена»** — не нашёлся `tarot_cards.json` при старте; проверьте рабочую директорию и логи старта.

Логи настраиваются в `logger.py` (по умолчанию вывод в stdout).

## Лицензия и авторы

Проект под лицензией [WTFPL](LICENSE).

**Автор:** dawgwelder

---

Belmondo Bot — развлекательный бот для чата.
