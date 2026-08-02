# Деплой на VPS

Стек живёт в `/opt/finance` на том же VPS, что и TripOS (147.45.238.246),
и устроен по той же схеме: GitHub Actions собирает образы в GHCR и по SSH
обновляет контейнеры.

Роли репозиториев:

- **finance_helper_back** — владеет стеком: кладёт на сервер
  `docker-compose.yml` (из `deploy/docker-compose.prod.yml`) и `.env`
  (из GitHub Secrets), поднимает `postgres`, `api`, `bot`.
- **finance_helper_web** — обновляет только контейнер `web`; требует, чтобы
  стек хотя бы раз был задеплоен из бэкенда.

## Порты на сервере

| порт | кто |
|---|---|
| 80 | TripOS web |
| 8000 (localhost) | TripOS api |
| 5432 (localhost) | системный Postgres |
| **443** | **finance caddy** — TLS, единственный наружный порт стека |
| 8080 (localhost) | finance web, для дебага |
| 8001 (localhost) | finance api, для дебага |

Postgres финансов наружу не торчит вовсе; попасть в него:
`ssh root@147.45.238.246 'docker compose -f /opt/finance/docker-compose.yml exec postgres psql -U finance'`.

## Секреты GitHub

Завести **в обоих репозиториях** (Settings → Secrets and variables → Actions):

| секрет | значение |
|---|---|
| `SSH_HOST` | `147.45.238.246` |
| `SSH_USER` | `root` |
| `SSH_KEY` | приватный ключ деплоя; публичная часть уже в `authorized_keys` сервера — подойдёт содержимое `~/.ssh/tripos_deploy` (тот же ключ, что в секретах TripOS) |
| `GHCR_PAT` | PAT c `read:packages` — сервер логинится им в GHCR перед pull (тот же, что у TripOS) |

Только в **finance_helper_back** (уходят в `/opt/finance/.env`):

| секрет | значение |
|---|---|
| `POSTGRES_PASSWORD` | пароль БД, сгенерировать: `openssl rand -hex 24` |
| `BOT_TOKEN` | токен бота из BotFather |
| `WEBAPP_URL` | `https://147-45-238-246.sslip.io` (см. раздел HTTPS) |
| `ALLOWED_TELEGRAM_IDS` | `202441927` |
| `INTERNAL_TOKEN` | секрет бот↔API, сгенерировать: `openssl rand -hex 24` |

Workflow валидирует, что все пять заведены и не пусты, до того как трогать сервер.

## Первый запуск

1. Завести секреты (выше).
2. Запушить `finance_helper_back` в `main` — деплой создаст `/opt/finance`,
   зальёт compose и `.env`, поднимет `postgres`, применит миграции
   (это делает команда запуска `api`) и запустит `api` с ботом.
3. Запушить `finance_helper_web` в `main` — поднимется `web` на 8080.
4. Перенести данные с ноутбука (см. ниже) либо просто прислать боту полный
   CSV-дамп — импорт зальёт всё с нуля, но ручные операции (например аренда)
   и доходы по месяцам в дампе не живут, их перенос — только дампом БД.
5. В BotFather: `/setmenubutton` → указать `WEBAPP_URL`.

## HTTPS

Telegram открывает Mini App только по https. TLS терминирует Caddy — он часть
стека (сервис `caddy` в compose), слушает 443 и проксирует на web. Имя
`147-45-238-246.sslip.io` — это IP сервера, записанный через дефисы: sslip.io
резолвит такие имена в сам IP, регистрировать ничего не нужно. Сертификат
Let's Encrypt Caddy получает и продлевает сам через TLS-ALPN-челлендж
(80-й порт занят TripOS, но этому челленджу хватает 443). Выдача проверена
на сервере 2026-08-02.

Отсюда значение секрета: `WEBAPP_URL=https://147-45-238-246.sslip.io` —
и тот же адрес указывается в BotFather.

Переезд на нормальный домен потом: A-запись на сервер, заменить имя хоста
в `deploy/Caddyfile`, обновить секрет `WEBAPP_URL` и адрес в BotFather,
задеплоить бэк. Если TLS отдать Cloudflare — сервис `caddy` можно убрать
и вернуть web наружу.

## Перенос данных с ноутбука

Импортированный дамп зальётся заново ботом, но ручные операции, доходы
по месяцам и планы живут только в БД. Полный перенос:

```bash
pg_dump -d finance_helper | gzip > /tmp/finance.sql.gz
scp /tmp/finance.sql.gz root@147.45.238.246:/tmp/
ssh root@147.45.238.246 'gunzip -c /tmp/finance.sql.gz | docker compose -f /opt/finance/docker-compose.yml exec -T postgres psql -U finance finance'
```

Дамп локальной базы совместим: схема создаётся теми же миграциями.
Перед заливкой стек должен быть поднят, а таблицы пусты (сразу после
первого деплоя так и есть).

## Бэкапы

`deploy/backup.sh` кладётся в репозиторий; на сервере запускать кроном:

```
0 4 * * * /opt/finance/backup.sh >> /opt/finance/backups/backup.log 2>&1
```

Скрипт складывает `pg_dump | gzip` в `/opt/finance/backups` с ротацией
30 дней; если в `.env` добавить `S3_ENDPOINT`, `S3_BUCKET`,
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` — копия уедет и в S3.
Сам скрипт на сервер деплой не кладёт — скопировать один раз:
`scp deploy/backup.sh root@147.45.238.246:/opt/finance/ && ssh root@147.45.238.246 chmod +x /opt/finance/backup.sh`.

## Дебаг на сервере

```bash
ssh root@147.45.238.246
cd /opt/finance
docker compose ps
docker compose logs --tail=100 api bot web
curl -s http://127.0.0.1:8001/health   # api напрямую, минуя web
```

Обход авторизации (`DEV_BYPASS_AUTH`) в проде не работает по построению:
`ENV=production` зашит в compose, и флаг игнорируется.
