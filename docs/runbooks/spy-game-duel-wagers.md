# Spy Clicker: развёртывание дуэлей со ставками

Статус: дополнение к production beta runbook для существующего
`belmondo.service`.

Механика не добавляет новый процесс, порт или environment-переменную. Она
использует существующий `SpyGameService` и тот же SQLite WAL-файл. При первом
старте release автоматически применяется append-only миграция
`011_spy_duel_wagers.sql`.

## 1. Перед развёртыванием

Локально проверьте release:

```bash
PYENV_VERSION=3.12.4 pytest -q
PYENV_VERSION=3.12.4 python -m compileall -q app.py handlers spy_game
git diff --check
git status --short
```

На сервере снимите online backup работающей WAL-базы. Обычный `cp` запущенной
базы использовать нельзя:

```bash
backup_path="/var/backups/belmondo-bot/spy-game-$(date -u +%Y%m%dT%H%M%SZ)-pre-duels.sqlite3"
sudo -u belmondo sqlite3 /var/lib/belmondo-bot/spy-game.sqlite3 ".backup '$backup_path'"
sudo -u belmondo sqlite3 "$backup_path" "PRAGMA integrity_check;"
```

Продолжайте только после ответа `ok`.

## 2. Доставить release

Используйте обычный порядок обновления одного сервиса:

```bash
cd /home/belmondo/belmondo_bot
git status --short
sudo -u belmondo git fetch --prune origin
sudo -u belmondo git checkout <RELEASE_REF>
sudo -u belmondo .venv/bin/pip install -r requirements.txt
sudo -u belmondo .venv/bin/pytest -q
sudo -u belmondo .venv/bin/python -m compileall -q app.py handlers spy_game
sudo systemctl restart belmondo.service
sudo systemctl status belmondo.service --no-pager
sudo journalctl -u belmondo.service -n 100 --no-pager
```

Не запускайте второй экземпляр бота или отдельный worker с тем же Telegram
token. Новые настройки не требуются: остаются действующими
`SPY_GAME_ENABLED=true`, allowlist beta-чата и постоянный `SPY_GAME_DB_PATH`.
В самом чате Spy Clicker должен быть включён командой `/spy_admin enable`.

## 3. Проверить миграцию

```bash
sudo -u belmondo sqlite3 /var/lib/belmondo-bot/spy-game.sqlite3 \
  "SELECT version, applied_at FROM schema_migrations WHERE version = 11;"
sudo -u belmondo sqlite3 /var/lib/belmondo-bot/spy-game.sqlite3 \
  "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('spy_duels', 'spy_duel_history') ORDER BY name;"
sudo -u belmondo sqlite3 /var/lib/belmondo-bot/spy-game.sqlite3 \
  "PRAGMA integrity_check;"
```

Ожидаются версия `11`, обе таблицы и `ok`. Миграция не меняет существующие
таблицы экономики и не списывает агентов.

## 4. Telegram smoke test

Нужны два обычных beta-аккаунта с username и как минимум пятью осведомителями у
каждого. До теста зафиксируйте их баланс через `/spy` → «Агенты».

1. Первый игрок отвечает на сообщение второго командой `/duel` или вызывает
   его через `/duel @username`.
2. В сообщении должны появиться ставка каждого `×1` и банк `×2`. Баланс
   вызывающего уже уменьшен на один.
3. Второй игрок принимает вызов. Его баланс уменьшается на один; посторонний
   пользователь принять вызов или сделать ход не может.
4. Оба игрока выбирают по одному скрытому ходу. Повторный ход не принимается.
5. Итоговое сообщение показывает только `@username`, победителя и банк `×2`.
   Суммарное количество осведомителей у двух игроков после расчёта равно сумме
   до дуэли.
6. Повторите вызов со ставкой `3` и отклоните его: баланс вызывающего должен
   полностью восстановиться.
7. Проверьте недостаточный баланс: соперник без нужного количества агентов не
   может принять ставку, но деньги вызывающего возвращаются после отмены или
   120-секундного таймаута.

Отдельно проверьте явные ставки `/duel @username 3` и `/duel @username 5`.

## 5. Проверить escrow и аудит в SQLite

После smoke test:

```bash
sudo -u belmondo sqlite3 -header -column /var/lib/belmondo-bot/spy-game.sqlite3 \
  "SELECT id, chat_id, status, stake_amount, resolution, winner_user_id, created_at, resolved_at FROM spy_duels ORDER BY created_at DESC LIMIT 10;"
sudo -u belmondo sqlite3 -header -column /var/lib/belmondo-bot/spy-game.sqlite3 \
  "SELECT duel_id, outcome, stake_amount, pot_amount, winner_user_id, created_at FROM spy_duel_history ORDER BY id DESC LIMIT 10;"
sudo -u belmondo sqlite3 /var/lib/belmondo-bot/spy-game.sqlite3 \
  "SELECT status, COUNT(*) FROM spy_duels GROUP BY status; PRAGMA integrity_check;"
```

Для завершённой дуэли ожидаются `status=resolved`, `outcome=moves` и
`pot_amount=2*stake_amount`. Для отказа или отмены — `status=refunded`,
`pot_amount=0`. На одну дуэль должна приходиться ровно одна строка истории.

После restart незавершённые ставки не теряются. Просроченный непринятый вызов
возвращает ставку, при просроченном выборе один сделанный ход даёт техническую
победу, отсутствие обоих ходов возвращает обе ставки.

## 6. Отключение и rollback

`SPY_GAME_ENABLED=false` запрещает создавать новые ставочные дуэли. Уже
зарезервированные ставки всё равно можно корректно завершить или вернуть через
таймаут; это предотвращает зависание escrow.

Перед откатом на код без этой механики убедитесь, что активных ставок нет:

```bash
sudo -u belmondo sqlite3 -header -column /var/lib/belmondo-bot/spy-game.sqlite3 \
  "SELECT id, chat_id, status, stake_amount, expires_at FROM spy_duels WHERE status IN ('pending', 'choosing');"
```

Если строки есть, дождитесь таймаута или закройте дуэль командой `/duel_cancel`
от участника или владельца. Затем можно переключить release и перезапустить
`belmondo.service`. Таблицы миграции `011` удалять не нужно: они не мешают
старому коду и сохраняют аудит.
