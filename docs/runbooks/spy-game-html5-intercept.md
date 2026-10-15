# Spy Clicker HTML5 «Перехват», «Тайник» и «Найти крота»: развёртывание и проверка

HTML5 Game использует созданную в BotFather игру с short name `spies`. Игра
обслуживается тем же `belmondo.service`, HTTP listener и SQLite, что и Mini App.
Отдельный процесс и отдельная база не нужны.

## Поведение релиза

- Когда `SPY_GAME_HTML5_URL` задан, события `intercept` и `dead_drop`
  публикуются через Telegram `sendGame`.
- Нажатие Play создаёт одну SQLite-сессию для пользователя и возвращает
  персональный URL с непрозрачным bearer-токеном во fragment.
- Игрок настраивает пять частот за 45 секунд. Максимум — 5000 очков, проходной
  результат — 3000.
- Неудачная попытка не закрывает событие. Первая успешная попытка атомарно
  закрывает событие и выдаёт предмет из исходного сценария Перехвата.
- Повторная отправка HTTP-результата не выдаёт награду второй раз.
- В «Тайнике» игрок подбирает трёхзначный код за пять минут без ограничения
  количества попыток. После каждого кода сервер возвращает число цифр на своих
  и на других местах, но никогда не отдаёт секретный код браузеру. Первый
  верный код атомарно получает обычную награду `dead_drop`.
- В «Найти крота» игрок изучает четыре досье и имеет одну финальную версию.
  Ошибка завершает только личную сессию; первый правильный ответ атомарно
  получает случайный предмет и трёх осведомителей. Решение хранится только на
  сервере и не возвращается в HTML/JSON. Естественный spawn разрешён в арке
  `mole_hunt` при `story_stage >= 3`; на stage 3 событие гарантировано, выше —
  участвует в обычном пуле.
- Если HTML5 Game выключена или `sendGame` вернул ошибку, бот публикует прежний
  текстовый Перехват или кнопку мгновенного обыска тайника.

Переход на пятиминутный таймер не требует новой миграции. Уже созданная до
restart сессия сохраняет прежний `expires_at`; новые правила времени полностью
применяются к сессиям, созданным после обновления сервиса. Игровой JavaScript
отдаётся без browser cache, чтобы интерфейс лимита попыток не переживал release.

Telegram native high scores намеренно не используются: экономика остаётся в
SQLite, а публичный вывод игры сохраняет правило «только `@username`».

## 1. BotFather и публичный URL

Для созданной игры `spies` укажите URL:

```text
https://<DOMAIN>/spy-app/game/
```

URL должен открываться по HTTPS. Существующий nginx `location /spy-app/` из
[`spy-game-webapp-rollout.md`](spy-game-webapp-rollout.md) уже проксирует и игру,
и её API; отдельный location не нужен.

`/newgame` и `/newapp` — независимые сущности. Если нужны только HTML5-операции,
Mini App direct link можно не создавать. Если нужен и оперативный
центр, сохраните также `SPY_GAME_WEBAPP_LAUNCH_URL`.

## 2. Environment

Добавьте в `/etc/belmondo-bot/belmondo.env`:

```dotenv
SPY_GAME_WEBAPP_ENABLED=true
SPY_GAME_WEBAPP_HOST=127.0.0.1
SPY_GAME_WEBAPP_PORT=8080
SPY_GAME_HTML5_SHORT_NAME=spies
SPY_GAME_HTML5_URL=https://<DOMAIN>/spy-app/game/
SPY_GAME_HTML5_MOLE_ENABLED=false
```

Для оперативного центра дополнительно оставьте:

```dotenv
SPY_GAME_WEBAPP_LAUNCH_URL=https://t.me/<BOT_USERNAME>/<WEBAPP_SHORT_NAME>
```

Core Spy Clicker должен быть включён, а beta-чат — находиться в allowlist:

```dotenv
SPY_GAME_ENABLED=true
SPY_GAME_ALLOWED_CHAT_IDS=<BETA_CHAT_ID>
SPY_GAME_ALLOW_MANUAL_SPAWN=true
```

`SPY_GAME_ALLOW_MANUAL_SPAWN` нужен только для ручного smoke. После проверки
верните его в `false`. Флаг Mole включайте сначала только для beta-чата; при
`false` событие продолжает работать через Telegram inline fallback.

## 3. Backup, доставка и локальные тесты на сервере

Перед обновлением снимите online backup WAL-базы:

```bash
backup_path="/var/backups/belmondo-bot/spy-game-$(date -u +%Y%m%dT%H%M%SZ)-pre-html5.sqlite3"
sudo -u belmondo sqlite3 /var/lib/belmondo-bot/spy-game.sqlite3 ".backup '$backup_path'"
sudo -u belmondo sqlite3 "$backup_path" "PRAGMA integrity_check;"
```

Продолжайте только после ответа `ok`. Затем в проверенном checkout:

```bash
cd /home/belmondo/belmondo_bot
git status --short
sudo -u belmondo git fetch --prune origin
sudo -u belmondo git checkout <RELEASE_REF>
sudo -u belmondo .venv/bin/pip install -r requirements.txt
sudo -u belmondo .venv/bin/pytest -q tests/test_spy_game.py tests/test_spy_webapp.py
sudo -u belmondo .venv/bin/pytest -q
sudo -u belmondo .venv/bin/python -m compileall -q app.py handlers spy_game
```

Перехват использует migration `009_intercept_game_runs.sql`, вскрытие тайника —
`010_dead_drop_game_runs.sql`, а расследование — `012_find_mole_game.sql`.
Миграции применяются при старте автоматически и не удаляют старые события или
экономику.

## 4. Restart и инфраструктурная проверка

```bash
sudo systemd-analyze verify /etc/systemd/system/belmondo.service
sudo nginx -t
sudo systemctl restart belmondo.service
sudo systemctl status belmondo.service --no-pager
sudo journalctl -u belmondo.service -n 150 --no-pager
sudo ss -ltnp | grep '127.0.0.1:8080'
curl --fail --silent --show-error http://127.0.0.1:8080/spy-app/health
curl --fail --silent --show-error https://<DOMAIN>/spy-app/health
curl -I https://<DOMAIN>/spy-app/game/
```

Health должен содержать:

```json
{"ok":true,"game_enabled":true,"html5_game_enabled":true,"html5_mole_enabled":true}
```

При обновлении с версии Перехвата в журнале ожидается migration `version=10`,
запуск Web App listener и отсутствие циклических restart, traceback и SQLite
lock errors.

## 5. Telegram beta smoke

Master в allowlisted группе выполняет:

```text
/spy_admin enable
/spy_admin spawn intercept
```

Проверить последовательно:

1. Бот отправил карточку Telegram Game с кнопкой «Настроить перехват».
2. Кнопка открывает игру внутри Telegram, а не показывает бесконечный spinner.
3. Игра показывает пять этапов настройки и убывающий таймер.
4. При результате ниже 3000 та же операция остаётся доступна другому игроку.
5. Первая попытка от 3000 закрывает канал, выдаёт предмет и публикует сообщение
   только с `@username` либо меткой «Скрытый агент».
6. Повторное открытие победителем не выдаёт второй предмет.
7. Второй успешный игрок получает сообщение «Канал уже перехвачен».
8. После `SPY_GAME_HTML5_URL` без значения и restart ручной `intercept` снова
   публикуется в старом текстовом виде.

Затем master запускает тайник:

```text
/spy_admin spawn dead_drop
```

Проверить:

1. Бот отправил карточку `spies` с кнопкой «Вскрыть тайник», а после Play открылся экран
   «Вскрытие тайника» с тремя дисками.
2. Неверный код добавился в журнал; подсказка показывает только число точных и
   переставленных цифр. В JSON `/api/game/state` нет поля с секретным кодом.
3. Повтор того же кода не добавился в журнал второй раз.
4. После семи неверных разных кодов сессия осталась активной, а пятиминутный
   таймер продолжил обратный отсчёт.
5. Первый правильный код удалил кнопку, выдал ровно одну обычную награду тайника
   и опубликовал только `@username` либо «Скрытый агент».
6. Одновременная отправка правильного кода с двух аккаунтов выдала награду
   ровно одному.
7. По окончании пяти минут новые коды перестали приниматься.
8. После отключения `SPY_GAME_HTML5_URL` ручной `dead_drop` снова показал кнопку
   «Обыскать тайник».

Затем включите `SPY_GAME_HTML5_MOLE_ENABLED=true`, перезапустите сервис и
выполните `/spy_admin spawn find_mole`. Проверьте неверный ответ первым аккаунтом,
правильный ответ вторым, единственную выдачу случайного предмета и трёх
осведомителей, отсутствие решения в `/api/game/state`, удаление клавиатуры и
публичное имя только в формате `@username`. После smoke отключение только
`SPY_GAME_HTML5_MOLE_ENABLED` должно вернуть четыре inline-кнопки.

После smoke проверить состояние без изменения базы:

```bash
sudo -u belmondo sqlite3 /var/lib/belmondo-bot/spy-game.sqlite3 \
  "SELECT status, score, COUNT(*) FROM intercept_game_runs GROUP BY status, score ORDER BY status, score;"
sudo -u belmondo sqlite3 /var/lib/belmondo-bot/spy-game.sqlite3 \
  "SELECT outcome, reward_id, reward_amount, created_at FROM event_history WHERE idempotency_key LIKE 'intercept-game:%' ORDER BY id DESC LIMIT 10;"
sudo -u belmondo sqlite3 /var/lib/belmondo-bot/spy-game.sqlite3 \
  "SELECT status, json_array_length(attempts_json) AS attempts, COUNT(*) FROM dead_drop_game_runs GROUP BY status, attempts ORDER BY status, attempts;"
sudo -u belmondo sqlite3 /var/lib/belmondo-bot/spy-game.sqlite3 \
  "SELECT outcome, reward_type, reward_id, reward_amount, created_at FROM event_history WHERE idempotency_key LIKE 'dead-drop-game:%' ORDER BY id DESC LIMIT 10;"
sudo -u belmondo sqlite3 /var/lib/belmondo-bot/spy-game.sqlite3 \
  "SELECT status, selected_suspect_id, item_reward_id, agent_reward_amount FROM find_mole_game_runs ORDER BY started_at DESC LIMIT 10;"
sudo -u belmondo sqlite3 /var/lib/belmondo-bot/spy-game.sqlite3 \
  "PRAGMA integrity_check;"
```

`integrity_check` должен вернуть `ok`. В логах не должны появляться
`X-Spy-Game-Token`, URL fragment или другие bearer-токены.

## 6. Rollback HTML5-механик

Чтобы выключить HTML5 Перехват и Тайник и вернуть текстовые механики:

```dotenv
SPY_GAME_HTML5_URL=
SPY_GAME_HTML5_MOLE_ENABLED=false
SPY_GAME_ALLOW_MANUAL_SPAWN=false
```

Если Mini App direct link не настроен, одновременно выключите listener:

```dotenv
SPY_GAME_WEBAPP_ENABLED=false
```

Затем:

```bash
sudo systemctl restart belmondo.service
sudo systemctl status belmondo.service --no-pager
sudo journalctl -u belmondo.service -n 100 --no-pager
```

Migrations 009–010 и строки попыток удалять или откатывать не нужно.
