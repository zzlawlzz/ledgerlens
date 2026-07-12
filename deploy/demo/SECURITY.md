# Демо: security-pass чек-лист (T-036 §3)

Проверка выполнена **2026-07-12** на живом dev-стеке (9 контейнеров, образы
`platform-app` / `platform-web`). Ниже — каждый пункт ТЗ §3, статус, команда
проверки и результат. Артефакт прикладывается к задаче T-036.

Легенда: ✅ закрыто и проверено · ⚙️ закрыто конфигом (demo-профиль/оверлей) ·
📋 организационный пункт демо-ноды (firewall/tunnel), закрывается в рантайме по
рунбуку `deploy/demo/README.md`.

---

## 1. Контейнеры non-root ✅ (app) / 📋 (nginx-master)

**app / worker / mcp-\*** — образ собирается multi-stage и запускается под
непривилегированным пользователем `app` (Dockerfile: `useradd --system app` +
`USER app`).

```
$ docker exec platform-app-1 id
uid=999(app) gid=999(app) groups=999(app)
```

**web (nginx)** — рабочие процессы идут под `nginx`, но master — под `root`
(штатное поведение официального образа: master биндит :80 и сбрасывает привилегии
воркерам).

```
$ docker exec platform-web-1 ps -o user,comm
root   nginx   (master)
nginx  nginx   (worker) ...
```

Остаточный риск низкий (master не обрабатывает запросы). Полный rootless —
переезд на `nginxinc/nginx-unprivileged` (listen 8080, USER 101). Отложено:
меняет порт образа и маппинг в базовом `docker-compose.yml` (хотспот, требует
ребилда+живой проверки UI). Зафиксировано как остаток.

Демо-оверлей дополнительно вешает `no-new-privileges:true` на все сервисы
(процесс не сможет поднять привилегии через setuid).

## 2. В образах нет секретов (docker history-скан) ✅

```
$ docker history --no-trunc platform-app | grep -iE "secret|password|token|api.?key|COPY .*\.env"
# единственное совпадение — ENV GPG_KEY=... из базового python:3.12-slim
# (это ПУБЛИЧНЫЙ ключ подписи CPython, не секрет приложения)
$ docker inspect platform-app -f '{{range .Config.Env}}{{println .}}{{end}}'
# среди baked-env нет ключей/токенов/паролей приложения
```

Секреты приходят только рантайм-путём через `env_file: .env` (не попадает в
слои образа — см. п.8). Совпадение `GPG_KEY` — апстрим-артефакт базового образа,
публичный идентификатор ключа, не секрет.

## 3. Порты наружу — только web (+Grafana view) ⚙️📋

Базовый `docker-compose.yml` публикует хост-порты многих сервисов (8000 app,
3000 web, 3001 grafana, 8081 worker, 8765-8767 mcp, 6333 qdrant, 5432 pg, 5678
n8n) — это удобно для dev/LAN. **Docker Compose конкатенирует списки `ports:`
при merge, поэтому оверлей не может «убрать» порт.** Публичная граница демо —
не «unpublish в оверлее», а два слоя на демо-ноде:

1. **Cloudflare Tunnel (Q-05, §4):** `cloudflared` держит ИСХОДЯЩЕЕ соединение,
   ingress маршрутизирует наружу ТОЛЬКО хостнейм web → `web:80` (опц. отдельный
   хостнейм на Grafana-viewer). Ни один другой сервис не имеет публичного маршрута.
2. **Host firewall (ufw):** весь inbound закрыт; опубликованные хост-порты
   доступны только с самой ноды, где и работает `cloudflared`.

Рунбук-сниппет (демо-нода):

```
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH        # только если нужен прямой SSH; иначе — через туннель
ufw enable
# cloudflared исходящий — правил на inbound не требует
```

Grafana в демо — anonymous **Viewer** (`GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer`,
`GF_USERS_ALLOW_SIGN_UP=false`), т.е. публичный доступ read-only.

## 4. CORS ограничен origin демо ✅

В `orchestrator/api.py` **CORSMiddleware не подключён** — FastAPI не добавляет
`Access-Control-Allow-Origin`, поэтому браузер по умолчанию блокирует любой
cross-origin XHR/fetch к API. В демо UI и API — один origin (nginx проксирует
`/api`, `/agui` на `app:8000`), поэтому same-origin-запросы работают, а сторонние
сайты к API из браузера обратиться не могут. Отдельный CORS-allowlist не нужен;
пункт закрыт «deny-by-default».

```
$ grep -niE "cors|allow_origin|add_middleware" orchestrator/api.py
# (пусто — middleware нет)
```

## 5. Debug-эндпоинты выключены в demo ✅⚙️

При `BUDGET_PROFILE=demo` приложение отключает интерактивную схему и
Swagger/ReDoc (`docs_url=redoc_url=openapi_url=None`). В dev остаются включены.

```
$ BUDGET_PROFILE=demo  python -c "import orchestrator.api as a; print(a.app.docs_url, a.app.openapi_url)"
None None
$ BUDGET_PROFILE=dev   python -c "import orchestrator.api as a; print(a.app.docs_url, a.app.openapi_url)"
/docs /openapi.json
```

Реализация — `61fa55e`; регресс-тест `tests/unit/test_api_demo_hardening.py`
(оба профиля). Прочих debug/trace/admin-роутов у приложения нет (роуты:
`/api/chat`, `/agui`, `/api/examples`, `/healthz`, `/api/monitor/*`).

## 6. SQL-роль app_ro перепроверена ✅

Роль `app_ro` (миграция `db/versions/001_domain.py`) — только `LOGIN` +
`CONNECT` + `USAGE ON SCHEMA public` + **`SELECT`** на доменные таблицы и
`latest_facts`. Ни INSERT/UPDATE/DELETE, ни DDL. Аналогично `grafana_ro`
(`003_grafana_ro.py`) для дашбордов.

```
GRANT CONNECT ON DATABASE ... TO app_ro
GRANT USAGE ON SCHEMA public TO app_ro
GRANT SELECT ON <domain tables>, latest_facts TO app_ro
```

`sql_query`-инструмент воркера ходит под read-only ролью → инъекция через
LLM-сгенерированный SQL не может писать/ронять данные.

## 7. Заголовки безопасности на nginx ✅

`web/nginx.conf` (демо): `server_tokens off`, `Content-Security-Policy`
(same-origin default, `connect-src 'self'` для SSE), `X-Frame-Options: DENY`,
`X-Content-Type-Options: nosniff`, `Referrer-Policy`, `Permissions-Policy`,
`client_max_body_size 64k`. Проверено живьём (временный nginx на compose-сети,
той же собранной `dist`):

```
$ curl -I http://<demo-web>/
HTTP/1.1 200 OK
Server: nginx                       # версия скрыта
Content-Security-Policy: default-src 'self'; connect-src 'self'; img-src 'self' data:; ...
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: geolocation=(), microphone=(), camera=()
```

CSP не ломает UI: собранный `index.html` подключает только same-origin
`/assets/*.js` (внешний ES-модуль) и `/assets/*.css` — ни inline-скриптов, ни
CDN. `nginx -t` на compose-сети проходит. Реализация — `ef28bbf`.

## 8. `.env` не в образах ✅

`.dockerignore` исключает `.env` и `.env.*`; секреты монтируются рантайм
(`env_file: .env`).

```
$ docker exec platform-app-1 sh -c 'ls -la /app/.env; ls -la /app | grep -i env'
ls: cannot access '/app/.env': No such file or directory
# .env-файлов в /app нет
```

---

## Итог

| # | Пункт | Статус |
|---|-------|--------|
| 1 | Контейнеры non-root | ✅ app/worker/mcp; 📋 nginx-master (штатно, остаток задокументирован) |
| 2 | Нет секретов в образах | ✅ |
| 3 | Порты наружу — только web (+Grafana view) | ⚙️ оверлей + 📋 tunnel/ufw (рунбук §4) |
| 4 | CORS ограничен | ✅ deny-by-default (middleware нет) |
| 5 | Debug-эндпоинты off в demo | ✅ docs/redoc/openapi отключены |
| 6 | app_ro перепроверена | ✅ SELECT-only |
| 7 | Security-заголовки nginx | ✅ проверено live |
| 8 | `.env` не в образах | ✅ |

**Остаётся для полного закрытия крита §3** («демо доступно по публичному URL с
TLS»): живой запуск Cloudflare Tunnel (§4) — ждёт CF-домен владельца (Q-05).
Конфиг-часть (demo-профиль, security-заголовки, docs-off, no-new-privileges,
ufw-рунбук) готова и проверена; остаётся сетевой/рантайм-шаг на демо-ноде.
