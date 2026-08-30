# Spy Clicker: пошаговое включение на сервере

Статус: runbook для первого production beta rollout.

Документ описывает безопасное включение нового функционала в уже работающем
боте. Команды адаптированы под текущий production unit `belmondo.service`,
пользователя `belmondo` и checkout `/home/belmondo/belmondo_bot`.

Перед выполнением замените:

- `<RELEASE_REF>` — проверенный tag или commit SHA;
- `<PROD_BOT_TOKEN>` — production-токен Telegram-бота;
- `<BETA_CHAT_ID>` — числовой ID одной beta-группы, обычно вида `-100...`;
- `<PATH_TO_AUTH_CONF>` — защищённый источник production `auth.conf`.

## 1. Что требуется поднять

Отдельная инфраструктура для Spy Clicker не нужна:

- бот остаётся одним Python-процессом и получает Telegram updates через polling;
- планировщик событий работает в `python-telegram-bot JobQueue` внутри процесса;
- состояние игры хранится в локальном SQLite-файле в режиме WAL;
- Rich Message / RichText формируется самим ботом и отдельного сервиса не требует;
- LLM Narrator обращается к DeepSeek по HTTPS и имеет локальный template fallback.

Не требуются Redis, Celery, RabbitMQ, PostgreSQL или отдельный worker. Нельзя
одновременно запускать два экземпляра бота с одним Telegram-токеном: они будут
конкурировать за polling updates.

Серверу нужен исходящий HTTPS-доступ как минимум к:

- `api.telegram.org`;
- `api.deepseek.com`, только когда включён LLM Narrator;
- уже используемым ботом внешним источникам.

Входящий HTTP-порт открывать не нужно.

## 2. Безопасный порядок rollout

Включение выполняется поэтапно:

1. Развернуть код с глобальным `SPY_GAME_ENABLED=false`.
2. Проверить старт процесса и автоматическую миграцию SQLite.
3. Включить игру только для одной beta-группы, Narrator оставить выключенным.
4. Провести ручной smoke test core-механики.
5. Запретить ручное создание событий и проверить естественный таймер.
6. Отдельно включить LLM Narrator и проверить fallback.

Глобальный flag и allowlist дополняют друг друга. Один только
`/spy_admin enable` не обходит `SPY_GAME_ENABLED=false` и не добавляет чат в
`SPY_GAME_ALLOWED_CHAT_IDS`.

## 3. Проверить ОС, service account и каталоги данных

Пользователь `belmondo` и checkout уже существуют, поэтому повторно создавать
их или клонировать репозиторий не нужно. Проверьте окружение:

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip git sqlite3 build-essential curl ca-certificates
python3 --version
id belmondo
sudo -u belmondo test -x /home/belmondo/belmondo_bot/.venv/bin/python
```

Требуется Python 3.10 или новее.

Создайте постоянные каталоги для SQLite и backup:

```bash
sudo install -d -o belmondo -g belmondo -m 0750 /var/lib/belmondo-bot
sudo install -d -o belmondo -g belmondo -m 0750 /var/backups/belmondo-bot
sudo install -d -o root -g belmondo -m 0750 /etc/belmondo-bot
```

SQLite-файл должен находиться в постоянном каталоге
`/var/lib/belmondo-bot`, а не внутри checkout: иначе смена release может потерять
состояние игры.

## 4. Развернуть код и зависимости

Доставьте `<RELEASE_REF>` в существующий checkout принятым способом. Перед
обновлением убедитесь, что на сервере нет незакоммиченных файлов, которые можно
потерять:

```bash
cd /home/belmondo/belmondo_bot
git status --short
sudo -u belmondo git checkout <RELEASE_REF>
sudo -u belmondo .venv/bin/python -m pip install --upgrade pip
sudo -u belmondo .venv/bin/pip install -r requirements.txt
```

До переключения процесса проверьте, что фактически развернут нужный commit:

```bash
cd /home/belmondo/belmondo_bot
git rev-parse HEAD
git status --short
```

## 5. Установить секреты

Файл `auth.conf` обязателен при старте всего приложения, даже если LLM Narrator
пока выключен. Так как production-бот уже работает, сначала проверьте
существующий файл и не перезаписывайте его без необходимости:

```bash
sudo -u belmondo test -r /home/belmondo/belmondo_bot/auth.conf
```

Если файла нет, установите его из защищённого источника, не добавляя в Git:

```bash
sudo install -o root -g belmondo -m 0640 <PATH_TO_AUTH_CONF> /home/belmondo/belmondo_bot/auth.conf
```

Минимально необходимая структура:

```ini
[auth]
openai_api_key = <DEEPSEEK_API_KEY>

[paths]
gonoscopes_path = godnoscopes.json
```

Если другие модули текущего бота используют дополнительные поля `auth.conf`,
их необходимо сохранить. Не выводите содержимое файла в journal или shell
history.

## 6. Создать environment-файл с выключенной игрой

Создайте `/etc/belmondo-bot/belmondo.env`:

```bash
sudoedit /etc/belmondo-bot/belmondo.env
sudo chown root:belmondo /etc/belmondo-bot/belmondo.env
sudo chmod 0640 /etc/belmondo-bot/belmondo.env
```

Начальная конфигурация:

```dotenv
TELEGRAM_BOT_TOKEN=<PROD_BOT_TOKEN>

SPY_GAME_ENABLED=false
SPY_GAME_ALLOWED_CHAT_IDS=<BETA_CHAT_ID>
SPY_GAME_DB_PATH=/var/lib/belmondo-bot/spy-game.sqlite3
SPY_GAME_TICK_SECONDS=30
SPY_GAME_EVENT_LIFETIME_SECONDS=180
SPY_GAME_ALLOW_MANUAL_SPAWN=false
SPY_GAME_LLM_NARRATOR_ENABLED=false
SPY_GAME_LLM_NARRATOR_TIMEOUT_SECONDS=8
```

Для нескольких beta-чатов ID перечисляются через запятую без пробелов или с
обычными пробелами: `-100111,-100222`. На первом rollout рекомендуется ровно
одна группа.

## 7. Обновить текущий `belmondo.service`

Сначала сохраните текущий unit в закрытом каталоге root и проверьте его
фактическое содержимое:

```bash
sudo install -o root -g root -m 0600 /etc/systemd/system/belmondo.service /root/belmondo.service.pre-spy
sudo systemctl cat belmondo.service
```

Приведите `/etc/systemd/system/belmondo.service` к следующему виду. Здесь
сохранены текущие пути, memory limit, journal и политика restart; добавлены
EnvironmentFile, доступ к каталогу SQLite и безопасные параметры процесса:

```bash
sudoedit /etc/systemd/system/belmondo.service
```

```ini
[Unit]
Description=Belmondo Bot
After=network-online.target

[Service]
Type=simple
User=belmondo
Group=belmondo
WorkingDirectory=/home/belmondo/belmondo_bot
EnvironmentFile=/etc/belmondo-bot/belmondo.env
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/home/belmondo/belmondo_bot/.venv/bin/python /home/belmondo/belmondo_bot/main.py --mode=prod --spam_mode=medium --token=${TELEGRAM_BOT_TOKEN}
Restart=always
RestartSec=5
TimeoutStopSec=30
MemoryMax=256M
StandardOutput=journal
StandardError=journal
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=/var/lib/belmondo-bot
UMask=0027

[Install]
WantedBy=multi-user.target
```

Текущая точка входа принимает Telegram token через CLI, поэтому unit следует
существующему контракту `main.py`. Environment-файл должен оставаться
недоступным непривилегированным пользователям. На shared-сервере дополнительно
учтите, что аргументы процесса могут быть видны другим локальным пользователям;
для текущего production-хоста предпочтителен отдельный service account без
доступа посторонних пользователей. В самом unit замените прежний буквальный
`--token=...` на показанный `--token=${TELEGRAM_BOT_TOKEN}`; значение хранится в
`/etc/belmondo-bot/belmondo.env`.

Проверить unit до запуска:

```bash
sudo systemd-analyze verify /etc/systemd/system/belmondo.service
sudo systemctl daemon-reload
sudo systemctl show belmondo.service -p FragmentPath -p User -p Group -p WorkingDirectory -p EnvironmentFiles -p MemoryMax
```

## 8. Проверить release до переключения production-процесса

```bash
cd /home/belmondo/belmondo_bot
sudo -u belmondo .venv/bin/pytest -q
sudo -u belmondo .venv/bin/python -m compileall -q app.py handlers spy_game
```

Тесты не должны использовать production SQLite. Значение
`SPY_GAME_DB_PATH` из systemd EnvironmentFile не подхватывается обычной shell
командой, пока его явно не экспортировали.

## 9. Первый старт: игра ещё выключена

Выполните контролируемый restart текущего unit:

```bash
sudo systemctl enable belmondo.service
sudo systemctl restart belmondo.service
sudo systemctl status belmondo.service --no-pager
sudo journalctl -u belmondo.service -n 100 --no-pager
```

Ожидаемая строка в journal:

```text
Spy game initialized: enabled=False narrator=False mode=prod ...
```

Даже с выключенным feature flag приложение создаёт БД и применяет миграции, но
не регистрирует фоновый job и не публикует игровые события.

Проверить БД:

```bash
sudo -u belmondo sqlite3 /var/lib/belmondo-bot/spy-game.sqlite3 "PRAGMA journal_mode; PRAGMA integrity_check; SELECT version, applied_at FROM schema_migrations ORDER BY version;"
sudo -u belmondo test -w /var/lib/belmondo-bot/spy-game.sqlite3
```

Ожидается `wal`, затем `ok`, затем список применённых миграций. Ошибка миграции,
отсутствие WAL или циклический restart — условие `NO-GO`: игру не включать.

## 10. Сделать консистентный backup SQLite

SQLite работает в WAL-режиме, поэтому не копируйте только основной файл через
`cp` во время работы. Используйте online backup API:

```bash
backup_path="/var/backups/belmondo-bot/spy-game-$(date -u +%Y%m%dT%H%M%SZ).sqlite3"
sudo -u belmondo sqlite3 /var/lib/belmondo-bot/spy-game.sqlite3 ".backup '$backup_path'"
sudo -u belmondo sqlite3 "$backup_path" "PRAGMA integrity_check;"
```

Ожидаемый результат последней команды — `ok`. Добавьте этот backup в штатную
систему резервного копирования сервера. Минимальная рекомендация: 7 ежедневных
и 4 еженедельных копии с периодической проверкой восстановления.

## 11. Включить core-механику в одном beta-чате

Измените только три флага в `/etc/belmondo-bot/belmondo.env`:

```dotenv
SPY_GAME_ENABLED=true
SPY_GAME_ALLOW_MANUAL_SPAWN=true
SPY_GAME_LLM_NARRATOR_ENABLED=false
```

Перезапустите процесс и убедитесь, что включены core и template Narrator:

```bash
sudo systemctl restart belmondo.service
sudo systemctl status belmondo.service --no-pager
sudo journalctl -u belmondo.service -n 100 --no-pager
```

В beta-группе пользователь с Telegram ID `master` из `state.py` выполняет:

```text
/spy_admin enable
/spy_admin status
/spy_admin spawn
```

Затем:

1. Убедиться, что событие пришло Rich Message с кнопкой.
2. Нажать кнопку от двух разных пользовательских аккаунтов.
3. Проверить, что событие завершилось, награды начислены один раз, а старая
   кнопка больше не принимает клик.
4. Открыть `/spy` от обычного игрока и проверить меню, профиль и статус.
5. Повторить клик после завершения и убедиться, что награда не дублируется.

Проверить журнал:

```bash
sudo journalctl -u belmondo.service --since "10 minutes ago" --no-pager
```

Ожидаемые маркеры:

- `spy_event_created` для естественно созданного события;
- `spy_narrative_selected ... source=template`;
- отсутствие `scheduler tick failed`, `event publication failed` и traceback.

Ручной `/spy_admin spawn` сейчас не пишет `spy_event_created`, поэтому его
наличие проверяется сообщением в чате и данными БД.

Проверить инвариант «не более одного активного события на чат»:

```bash
sudo -u belmondo sqlite3 /var/lib/belmondo-bot/spy-game.sqlite3 "SELECT chat_id, COUNT(*) FROM game_events WHERE status = 'active' GROUP BY chat_id HAVING COUNT(*) > 1;"
sudo -u belmondo sqlite3 /var/lib/belmondo-bot/spy-game.sqlite3 "SELECT status, COUNT(*) FROM game_events GROUP BY status ORDER BY status;"
sudo -u belmondo sqlite3 /var/lib/belmondo-bot/spy-game.sqlite3 "SELECT COUNT(*) AS agent_rows FROM user_agents;"
```

Первый запрос не должен вернуть строк.

## 12. Проверить естественное возникновение событий

После ручного smoke test выключите ручной spawn и перезапустите бот:

```dotenv
SPY_GAME_ALLOW_MANUAL_SPAWN=false
```

```bash
sudo systemctl restart belmondo.service
```

В beta-чате создайте обычную активность сообщениями нескольких участников.
Повторные сообщения одного пользователя учитываются не чаще одного раза в 20
секунд. Команда `/spy_admin status` показывает накопленную активность и время
следующего события.

Рабочие диапазоны таймера:

| Activity score | До следующего события |
|---:|---:|
| 6–14.9 | 45–75 минут |
| 15–29.9 | 25–45 минут |
| 30 и выше | 12–25 минут |

После spawn сохраняется 45% накопленной активности. До достижения score 6
событие не планируется. Для production-проверки не сокращайте интервалы в коде:
ручной spawn уже проверяет обработку события, а естественный тест должен
подтвердить реальные настройки планировщика.

## 13. Отдельно включить LLM Narrator

Перед включением убедитесь, что `auth.conf` содержит действующий ключ DeepSeek и
с сервера доступен API. Не отправляйте сам ключ в проверочной команде:

```bash
curl -I https://api.deepseek.com
```

Включите Narrator:

```dotenv
SPY_GAME_LLM_NARRATOR_ENABLED=true
SPY_GAME_LLM_NARRATOR_TIMEOUT_SECONDS=8
```

```bash
sudo systemctl restart belmondo.service
sudo journalctl -u belmondo.service -n 100 --no-pager
```

Для короткого теста можно временно вернуть
`SPY_GAME_ALLOW_MANUAL_SPAWN=true`, выполнить `/spy_admin spawn`, а затем сразу
снова выставить `false` и перезапустить процесс.

Ожидаемые варианты:

- `spy_narrative_selected ... source=llm` — модель вернула валидный текст;
- `spy_narrator: fallback ...` и затем `source=template` — timeout, ошибка API
  или невалидный ответ; событие всё равно должно быть опубликовано.

LLM управляет только художественным `body`. Тип события, награды, дедлайн,
кнопки и серверные правила модель не определяет.

## 14. Наблюдение после включения

Первые 30–60 минут держите открытым журнал:

```bash
sudo systemctl show belmondo.service -p MemoryCurrent -p MemoryMax -p NRestarts
sudo journalctl -u belmondo.service -f
```

Контролировать:

- процесс не перезапускается циклически;
- Telegram polling не сообщает о втором экземпляре бота;
- нет `scheduler tick failed` и ошибок SQLite locking;
- события появляются только в allowlist-чате;
- после истечения deadline кнопка удаляется;
- LLM timeout приводит к template fallback, а не к потере события;
- `/spy_admin status` показывает разумные activity score и `next_event_at`.

После суток beta-проверки повторите `PRAGMA integrity_check`, снимите backup и
оцените число событий и игроков:

```bash
sudo -u belmondo sqlite3 /var/lib/belmondo-bot/spy-game.sqlite3 "PRAGMA integrity_check; SELECT status, COUNT(*) FROM game_events GROUP BY status; SELECT COUNT(*) AS rewarded_players FROM users;"
```

## 15. Rollback и kill switch

### Быстро выключить только Narrator

```dotenv
SPY_GAME_LLM_NARRATOR_ENABLED=false
```

После изменения выполните `sudo systemctl restart belmondo.service`. Core-игра
продолжит работать на локальных шаблонах.

### Выключить игру целиком

Если бот отвечает, master сначала выполняет в beta-чате:

```text
/spy_admin disable
```

Это закрывает активное событие и убирает его keyboard. Затем:

```dotenv
SPY_GAME_ENABLED=false
SPY_GAME_ALLOW_MANUAL_SPAWN=false
SPY_GAME_LLM_NARRATOR_ENABLED=false
```

```bash
sudo systemctl restart belmondo.service
sudo journalctl -u belmondo.service -n 100 --no-pager
```

Если бот не отвечает, сразу используйте глобальный flag и restart. Базовые
функции бота продолжат работать, а Spy scheduler не будет зарегистрирован.

### Откатить только изменение systemd unit

Если после изменения unit не проходит verify или базовый бот не стартует,
восстановите сохранённую текущую конфигурацию:

```bash
sudo install -o root -g root -m 0644 /root/belmondo.service.pre-spy /etc/systemd/system/belmondo.service
sudo systemctl daemon-reload
sudo systemctl restart belmondo.service
sudo systemctl status belmondo.service --no-pager
```

Резервная копия содержит старый `ExecStart` с Telegram token, поэтому оставляйте
её доступной только root и удаляйте по принятой на сервере процедуре работы с
секретами после подтверждённого rollout.

Не удаляйте `/var/lib/belmondo-bot/spy-game.sqlite3` и не откатывайте миграции:
это не требуется для выключения функции и уничтожит прогресс beta-игроков. При
откате к предыдущему release сохраните БД отдельно; старый код её не использует.
Восстановление из backup выполняйте только после остановки процесса и проверки
конкретной подтверждённой порчи данных.

## 16. Go / No-Go checklist

`GO`, если выполнено всё:

- release соответствует `<RELEASE_REF>`, тесты проходят;
- процесс стабильно запускается с `SPY_GAME_ENABLED=false`;
- SQLite использует WAL, миграции применены, integrity check возвращает `ok`;
- создан и проверен backup;
- allowlist содержит только beta-чат;
- master может включить чат, обычный пользователь — нет;
- ручное событие проходит полный цикл без двойной награды;
- естественный таймер выставляет `next_event_at`;
- ручной spawn после smoke test выключен;
- при ошибке LLM срабатывает template fallback;
- rollback через flags проверен и понятен дежурному.

`NO-GO`, если есть хотя бы одно:

- неизвестен фактически развернутый commit;
- на токене уже работает второй polling-процесс;
- не создана или не мигрировалась БД;
- integrity check не равен `ok`;
- beta chat ID не подтверждён;
- события появляются вне allowlist;
- scheduler, публикация или начисление наград дают необработанные ошибки.
