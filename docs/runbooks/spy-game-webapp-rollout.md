# Spy Clicker Mini App: развёртывание и обновление сервисов

Статус: production beta runbook для существующего хоста Belmondo Bot.

Mini App не создаёт второй Python-процесс и не открывает второй экземпляр
Telegram polling. HTTP API и статические файлы запускаются внутри
`belmondo.service`, используют тот же `SpyGameService` и тот же последовательный
SQLite executor. Снаружи добавляется только HTTPS reverse proxy nginx.

Развёртывание Telegram HTML5 Game `spies` для событий «Перехват» и «Тайник» описано
отдельно в [`spy-game-html5-intercept.md`](spy-game-html5-intercept.md). HTML5
Game использует тот же listener и nginx location, но отдельный BotFather
`/newgame` flow.

Пути и имена ниже соответствуют текущему окружению:

- checkout: `/home/belmondo/belmondo_bot`;
- systemd unit: `belmondo.service`;
- service account: `belmondo`;
- environment: `/etc/belmondo-bot/belmondo.env`;
- SQLite: `/var/lib/belmondo-bot/spy-game.sqlite3`.

Перед началом замените:

- `<RELEASE_REF>` — проверенный tag или commit SHA;
- `<DOMAIN>` — домен Mini App, например `spy.example.org`;
- `<BOT_USERNAME>` — username бота без `@`;
- `<WEBAPP_SHORT_NAME>` — short name, созданный в BotFather;
- `<BETA_CHAT_ID>` — числовой ID beta-группы.

## 1. Итоговая схема

```text
Telegram Mini App
       │ HTTPS :443
       ▼
nginx + TLS
       │ http://127.0.0.1:8080/spy-app/
       ▼
belmondo.service
  ├─ Telegram polling и групповые события
  ├─ aiohttp Mini App API и static
  └─ один SpyGameService → SQLite WAL
```

Не запускайте отдельный API-процесс поверх того же SQLite: это создаст второй
executor и отдельный lifecycle reconcile. Порт `8080` должен слушать только
loopback и не должен быть открыт в firewall.

## 2. Подготовить DNS и Mini App в BotFather

1. Создайте DNS `A`/`AAAA` для `<DOMAIN>` на production-сервер и дождитесь
   резолва.
2. Откройте `@BotFather`, выполните `/newapp`, выберите `<BOT_USERNAME>` и
   создайте `<WEBAPP_SHORT_NAME>`.
3. В качестве Web App URL укажите строго
   `https://<DOMAIN>/spy-app/` с завершающим `/`.
4. Включите для того же URL Main Mini App через `/mybots` → bot →
   Bot Settings → Configure Mini App и, при желании, кнопку меню бота. Запуск
   из профиля или меню даёт полноценный кабинет: групповой контекст backend
   подбирает сам по сохранённому членству пользователя в игровых чатах
   (таблица `chat_members`, см. ниже).
5. Итоговая direct link должна иметь вид
   `https://t.me/<BOT_USERNAME>/<WEBAPP_SHORT_NAME>`.

Формат direct link и правила `startapp` описаны в официальной документации
[Telegram Mini Apps](https://core.telegram.org/bots/webapps#direct-link-mini-apps),
а требования к server-side проверке `initData` — в разделе
[Validating data](https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app).

Не добавляйте `startapp` вручную в BotFather. Бот выпускает этот параметр для
каждого `/spy` как необязательную подсказку, из какого чата открыта ссылка:
токен подписан, живёт 10 минут и хранится в памяти процесса; числовой chat ID в
ссылку не помещается. Подсказка не привязана к пользователю — кнопку в чужом
`/spy` может нажимать любой участник чата. Просроченная, чужая или «пережившая
restart» подсказка молча игнорируется: контекст всё равно берётся из членства.

Членство в чатах (`chat_members`) обновляется по сообщениям в группе, нажатиям
inline-кнопок и `/spy` (последний пишет сразу, остальное — на очередном tick).
Миграция 019 заполняет таблицу из истории событий, слотов и дуэлей, поэтому
существующие игроки получают полный кабинет сразу после выкладки. Игрок без
членства ни в одном включённом чате видит read-only досье с подсказкой написать
в игровом чате или вызвать `/spy`. Если пользователь состоит в нескольких
включённых чатах, Mini App показывает переключатель; клиент передаёт выбор
заголовком `X-Spy-Chat` в виде opaque handle, числовые ID от клиента не
принимаются.

## 3. Проверить и сохранить текущую конфигурацию

```bash
sudo systemctl cat belmondo.service
sudo nginx -T
sudo install -o root -g root -m 0600 /etc/belmondo-bot/belmondo.env /root/belmondo.env.pre-webapp
sudo install -o root -g root -m 0600 /etc/systemd/system/belmondo.service /root/belmondo.service.pre-webapp
```

Если nginx уже установлен, не заменяйте его конфигурацию целиком: добавьте
только отдельный `server` block или `location` в существующий виртуальный host.

Перед обновлением снимите online backup работающей WAL-базы:

```bash
backup_path="/var/backups/belmondo-bot/spy-game-$(date -u +%Y%m%dT%H%M%SZ)-pre-webapp.sqlite3"
sudo -u belmondo sqlite3 /var/lib/belmondo-bot/spy-game.sqlite3 ".backup '$backup_path'"
sudo -u belmondo sqlite3 "$backup_path" "PRAGMA integrity_check;"
```

Продолжайте только если последняя команда вернула `ok`.

## 4. Доставить release и проверить его до restart

```bash
cd /home/belmondo/belmondo_bot
git status --short
sudo -u belmondo git fetch --prune origin
sudo -u belmondo git checkout <RELEASE_REF>
sudo -u belmondo .venv/bin/pip install -r requirements.txt
sudo -u belmondo .venv/bin/pytest -q
sudo -u belmondo .venv/bin/python -m compileall -q app.py handlers spy_game
git rev-parse HEAD
git status --short
```

Не выполняйте checkout поверх неизвестных незакоммиченных изменений. В
`requirements.txt` уже присутствует `aiohttp`; отдельный web framework не нужен.

## 5. Добавить Web App environment с выключенным listener

Откройте `/etc/belmondo-bot/belmondo.env` и сохраните существующие значения.
Добавьте:

```dotenv
SPY_GAME_WEBAPP_ENABLED=false
SPY_GAME_WEBAPP_HOST=127.0.0.1
SPY_GAME_WEBAPP_PORT=8080
SPY_GAME_WEBAPP_LAUNCH_URL=https://t.me/<BOT_USERNAME>/<WEBAPP_SHORT_NAME>
SPY_GAME_WEBAPP_INIT_DATA_MAX_AGE_SECONDS=300
SPY_GAME_WEBAPP_CONTEXT_TTL_SECONDS=600
SPY_GAME_WEBAPP_RATE_LIMIT_PER_MINUTE=60
```

Права файла должны остаться закрытыми:

```bash
sudo chown root:belmondo /etc/belmondo-bot/belmondo.env
sudo chmod 0640 /etc/belmondo-bot/belmondo.env
```

`SPY_GAME_WEBAPP_LAUNCH_URL` — Telegram direct link, а не URL домена. Публичный
URL из BotFather указывает на nginx, direct link нужен кнопке группового `/spy`.

## 6. Установить nginx и получить TLS-сертификат

Если nginx и certbot ещё не установлены:

```bash
sudo apt-get update
sudo apt-get install -y nginx certbot python3-certbot-nginx
sudo systemctl enable --now nginx
```

Создайте `/etc/nginx/sites-available/belmondo-spy-app`:

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name <DOMAIN>;

    location /spy-app/ {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 5s;
        proxy_read_timeout 30s;
        client_max_body_size 8k;
    }
}
```

Здесь `proxy_pass` намеренно указан без завершающего `/`: путь `/spy-app/...`
должен попасть в приложение без переписывания.

Включите host и проверьте конфигурацию:

```bash
sudo ln -s /etc/nginx/sites-available/belmondo-spy-app /etc/nginx/sites-enabled/belmondo-spy-app
sudo nginx -t
sudo systemctl reload nginx
sudo certbot --nginx -d <DOMAIN> --redirect
sudo nginx -t
sudo systemctl reload nginx
sudo certbot renew --dry-run
```

Если symlink уже существует, не создавайте его повторно. При включённом UFW
разрешите стандартный HTTPS-профиль nginx, не порт 8080:

```bash
sudo ufw allow 'Nginx Full'
sudo ufw status
```

До запуска listener внешний URL закономерно вернёт `502`; это ещё не ошибка
nginx или сертификата.

## 7. Запустить listener внутри `belmondo.service`

Поменяйте только один flag:

```dotenv
SPY_GAME_WEBAPP_ENABLED=true
```

Новый systemd unit не нужен. Существующий `belmondo.service` уже имеет доступ к
коду и SQLite; привязка к непривилегированному loopback-порту совместима с его
hardening. Проверьте unit и перезапустите:

```bash
sudo systemd-analyze verify /etc/systemd/system/belmondo.service
sudo systemctl restart belmondo.service
sudo systemctl status belmondo.service --no-pager
sudo journalctl -u belmondo.service -n 100 --no-pager
sudo ss -ltnp | grep '127.0.0.1:8080'
```

В journal ожидается:

```text
Spy game initialized: enabled=True ... webapp=True ...
Spy Game Web App listening on http://127.0.0.1:8080/spy-app/
```

Проверить health сначала локально, затем через TLS:

```bash
curl --fail --silent --show-error http://127.0.0.1:8080/spy-app/health
curl --fail --silent --show-error https://<DOMAIN>/spy-app/health
curl -I https://<DOMAIN>/spy-app/
```

Health должен вернуть `"game_enabled": true`; дополнительное поле
`"html5_game_enabled"` показывает, настроены ли HTML5-операции «Перехват» и
«Тайник». Если core пока
оставлен выключенным для инфраструктурного этапа, допустимо
`"game_enabled": false`; API досье в этом состоянии вернёт 503.

## 8. Beta smoke test в Telegram

Перед тестом убедитесь, что core включён и чат активирован:

```dotenv
SPY_GAME_ENABLED=true
SPY_GAME_ALLOWED_CHAT_IDS=<BETA_CHAT_ID>
```

После изменения environment выполните restart, затем master в группе вызывает:

```text
/spy_admin enable
/spy_admin status
```

Обычный пользователь выполняет `/spy` и проверяет:

1. Первая кнопка «Открыть оперативный центр» запускает приложение внутри
   Telegram, а не внешний браузер.
2. Досье, агенты, инвентарь, контакты и рейтинг совпадают со старым inline-меню.
3. В рейтинге и публичных данных показывается только `@username`; display name и
   числовые Telegram ID не появляются.
4. Экипировка предмета меняет слот, повторное открытие сохраняет результат.
5. Повышение репутации и учреждение службы требуют подтверждения и корректно
   обрабатывают недостаток ресурсов.
6. В «Контактах» доступны постоянные сделки Куратора, Рекрутера, Начальника
   операций и Контрразведки; успешная сделка списывает показанные ресурсы ровно
   один раз.
7. Каждый контакт продолжает появляться как редкое групповое событие; его
   успешный обмен выдаёт результат ×2 и закрывает событие.
8. Старые inline-кнопки `/spy` продолжают работать как fallback, включая
   «Контакты Центра».
9. Групповые Recruitment, Dead Drop и другие события продолжают появляться и
   обрабатываться в исходных Telegram-сообщениях.
10. На экране шириной 320–480 px карточки «Контактов» не создают горизонтальную
    прокрутку: стоимость и результат переносятся, кнопка занимает отдельную строку.

Проверки контекста:

1. Откройте Main Mini App из профиля или меню бота участником, который уже
   писал в beta-группе или вызывал `/spy`: кабинет полный, карточка «Контекст
   операции» показывает «Сеть активна», экипировка и обмены работают.
2. Нажмите кнопку «Открыть оперативный центр» в сообщении `/spy` другого
   участника: приложение открывается с полным доступом, ошибки 401 нет.
3. Откройте ссылку старше 10 минут или выданную до restart: приложение
   работает как при запуске из меню, подсказка чата просто игнорируется.
4. Откройте Mini App новым аккаунтом, который ещё не писал в beta-группе:
   read-only досье с текстом «Напишите в игровом чате или вызовите /spy».
   После одного сообщения в группе и следующего tick (30 с) или после `/spy`
   кабинет становится полным.
5. Если тестовый аккаунт состоит в двух включённых чатах, в карточке появляется
   переключатель; по умолчанию выбран чат с последней активностью, выбор
   сохраняется между открытиями. Числовые chat ID в ответах API отсутствуют.
6. Выполните `/spy_admin disable`: чат исчезает из списка контекстов, чтение
   остаётся доступным, записи возвращают запрет.

Проверьте журнал без вывода request headers:

```bash
sudo journalctl -u belmondo.service --since "15 minutes ago" --no-pager
sudo journalctl -u nginx --since "15 minutes ago" --no-pager
```

Одиночные `spy_webapp_auth_failed` ожидаемы в негативных тестах. Traceback,
SQLite lock errors, циклические restart и попадание init data в логи — `NO-GO`.

## 9. Наблюдение после включения

```bash
sudo systemctl show belmondo.service -p MemoryCurrent -p MemoryMax -p NRestarts
sudo systemctl show nginx.service -p ActiveState -p NRestarts
sudo journalctl -u belmondo.service -f
```

Контролируйте HTTP 5xx nginx, рост памяти, restart процесса и SQLite errors.
Static/API не используют отдельную БД и не требуют отдельного backup schedule.
Сохраняется существующая WAL-safe процедура `.backup`.

## 10. Обновление следующих release

Mini App frontend входит в Python release в каталоге
`spy_game/webapp_static/`; отдельной сборки Node.js и отдельного deploy нет.
Для последующих обновлений:

```bash
backup_path="/var/backups/belmondo-bot/spy-game-$(date -u +%Y%m%dT%H%M%SZ)-pre-release.sqlite3"
sudo -u belmondo sqlite3 /var/lib/belmondo-bot/spy-game.sqlite3 ".backup '$backup_path'"
sudo -u belmondo sqlite3 "$backup_path" "PRAGMA integrity_check;"
cd /home/belmondo/belmondo_bot
git status --short
sudo -u belmondo git fetch --prune origin
sudo -u belmondo git checkout <RELEASE_REF>
sudo -u belmondo .venv/bin/pip install -r requirements.txt
sudo -u belmondo .venv/bin/pytest -q
sudo systemctl restart belmondo.service
sudo systemctl status belmondo.service --no-pager
curl --fail --silent --show-error https://<DOMAIN>/spy-app/health
```

nginx перезагружать не нужно, если домен, путь и порт не менялись. Browser cache
для JS/CSS ограничен пятью минутами, HTML отдаётся с `no-store`.

## 11. Rollback

### Выключить только Mini App

```dotenv
SPY_GAME_WEBAPP_ENABLED=false
```

```bash
sudo systemctl restart belmondo.service
sudo systemctl status belmondo.service --no-pager
```

Кнопка Mini App исчезнет из новых ответов `/spy`, но Telegram inline fallback и
групповые события продолжат работать. Уже отправленная web-ссылка перестанет
отвечать; при длительном rollback удалите/отключите Main Mini App в BotFather.

### Откатить nginx

Сначала выключите Mini App flag. Затем:

```bash
sudo unlink /etc/nginx/sites-enabled/belmondo-spy-app
sudo nginx -t
sudo systemctl reload nginx
```

Не удаляйте TLS-сертификат в аварийном rollback и не трогайте SQLite. Если
виртуальный host был добавлен в существующий файл, восстановите сохранённую
версию именно этого файла вместо `unlink`.

### Откатить release

Переключитесь на предыдущий проверенный ref и перезапустите только
`belmondo.service`. Миграция 019 добавляет таблицу `chat_members` и колонку
`chat_state.title`; прежний код их не читает, поэтому downgrade схемы не
требуется, а при возврате на новый release данные членства сохранятся.
Состояние, изменённое через Mini App, является обычным состоянием Spy Clicker и
совместимо с прежним inline-меню.

## 12. Go / No-Go

`GO`, если одновременно выполнено всё:

- BotFather URL и direct link относятся к одному bot/app;
- публичный `/spy-app/` имеет валидный HTTPS без redirect loop;
- порт 8080 слушает только `127.0.0.1`;
- health отвечает локально и через nginx;
- один `belmondo.service` владеет polling, HTTP API и SQLite;
- чтение и три разрешённые мутации прошли beta smoke;
- запуск из профиля/меню даёт полный кабинет участнику группы; чужая и
  просроченная ссылка работают; аккаунт без членства остаётся read-only;
- Telegram fallback и групповые события не регрессировали;
- backup прошёл `PRAGMA integrity_check = ok`.

`NO-GO`, если есть хотя бы одно:

- frontend доступен только по HTTP или сертификат невалиден;
- порт 8080 открыт наружу;
- поднят второй Python API-процесс с тем же SQLite;
- backend принимает `user_id` или `chat_id` из JSON клиента;
- Mini App выдаёт ресурсы из read-only запуска;
- есть необработанные ошибки, SQLite locking или restart loop.
