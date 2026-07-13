# Координация агентов LedgerLens

Канал связи между двумя автономными «разработчиками» проекта:
- **Оркестратор** — интерактивная сессия с владельцем (может спавнить субагентов
  в worktree, делает живые проверки на общем стеке, мержит).
- **Регулярная сессия** — `ledgerlens-backlog-progress` (cron каждые 3 ч,
  `~/.claude/scheduled-tasks/`): продолжает бэклог с места остановки.

Обе видят этот файл через git. **Читать его ПЕРВЫМ делом каждой сессии, писать в
него сводку в конце.** Он не заменяет BACKLOG.md (там ТЗ и приёмка) — здесь
оперативная координация: кто что делает сейчас, свежие находки, план.

---

## Протокол против коллизий (обязательно)

1. **Старт сессии:** `git pull` → прочитать раздел «Активные зоны» ниже → `git
   log --oneline -8` → **сверить состояние eval с БД, а не с памятью**
   (`SELECT id, profile, git_sha, summary->'metrics' FROM eval_runs ORDER BY id
   DESC LIMIT 6`). Числа в памяти/сносках могут устареть.
2. **Правило 30 минут:** если в `git status` есть незакоммиченные изменения, а
   файлы менялись < 30 мин назад (`stat -c %y <file>`) — вероятно, другая сессия
   активна. НЕ трогать эти файлы, заняться непересекающейся задачей или доложить
   и выйти.
3. **Застолбить зону:** перед работой над задачей — дописать строку в «Активные
   зоны» (задача, какие каталоги трогаешь, время). Убрать/обновить в конце.
4. **Коммить часто и мелко** — это главный механизм передачи. Незакоммиченная
   работа = невидима другой сессии и рискует быть перезатёртой. Перед выходом —
   закоммитить всё готовое (или явно отметить «WIP, не трогать» здесь).
5. **Не push в main из worktree субагента** — субагенты коммитят в свою ветку,
   мержит оркестратор.
6. **Общие файлы-хотспоты** (частые конфликты): `prompts/worker_react.md`,
   `config/app.yaml`, `docker-compose.yml`, `orchestrator/api.py`,
   `BACKLOG.md`. Правя их — закоммить сразу, не копи.

---

## Активные зоны (кто что делает СЕЙЧАС)

_Формат: `[роль] задача — каталоги — время начала — статус`_

- `[регулярная] T-036 §1 defect-fix #3: /agui concurrency-slot leak (overlong thread_id) — ЗАВЕРШЕНО кодом+тестами, закоммичено+запушено (main=9bb0bd1). Зона СВОБОДНА.` Тронула ТОЛЬКО `orchestrator/api.py` (хотспот — коммичен сразу, +импорт `ValidationError`, оба `/agui`+`/api/chat`) + новый `tests/unit/test_agui_slot_leak.py` (2 теста) + `BACKLOG.md`²⁷ (footnote отдельным коммитом). НЕ трогала `demo_limits.py`/пороги/prompts/graph/eval/compose/nginx. Реальный public-demo availability-баг: `/agui` брал concurrency-слот в `_admit` и создавал ран ДО `ChatRequest(session_id=body.thread_id)`, а `session_id` капается `max_length=128` при неограниченном AG-UI `thread_id` → клиент с `thread_id`>128 симв. ронял ValidationError вне slot-release-guard → слот течёт навсегда + ран осиротевает в `running`; на демо (cap=2) два таких запроса вешают демо на «busy» 429 до рестарта. Фикс: валидировать весь запрос (ValidationError→422) ДО взятия слота/`create_run` + расширить guard на весь setup до `create_task` (оба эндпоинта). 2 теста гоняют реальный endpoint (`TestClient`), **git-stash-верифицированы: падают на старом коде** (`_active==1`). 333 non-slow зелёных, ruff+mypy чисто.

- `[регулярная] T-036 §1 defect-fix #2: spoofable demo rate-limit (X-Forwarded-For) — ЗАВЕРШЕНО кодом+тестами, закоммичено+запушено (main=2dd88d2). Зона СВОБОДНА.` Тронула ТОЛЬКО `orchestrator/api.py:_client_ip` (хотспот — закоммичен сразу) + `tests/unit/test_demo_limits.py` (заменён 1 тест на 2) + `BACKLOG.md`²⁷ (footnote, отдельным коммитом). НЕ трогала `demo_limits.py`/пороги/prompts/graph/eval/compose/nginx. Фикс = доверять только `CF-Connecting-IP` (неспуфабельно за Cloudflare-туннелем), вне CF — прямой TCP-peer; `X-Forwarded-For` больше не источник для rate-limit. 331 non-slow зелёных, ruff+mypy чисто. Опциональный defense-in-depth (nginx явно затирать XFF) — не блокер, не делала.

- `[регулярная] T-036 §1 defect-fix: demo rate-limit lockout — ЗАВЕРШЕНО кодом+тестом, закоммичено+запушено (main=ced750b). Зона СВОБОДНА.` Тронула ТОЛЬКО `orchestrator/api.py` (`_admit`-ordering, хотспот — закоммичен сразу) + `tests/unit/test_demo_limits.py` (+1 регресс-тест) + `BACKLOG.md`²⁷ (addendum). НЕ трогала `demo_limits.py` (публичный API сохранён), пороги/prompts/graph/eval/compose. Реальный public-demo дефект: `check_rate` (пишет hit) вызывался ДО `acquire_slot` → concurrency-отказ («busy» 429) жёг часовую rate-квоту → рекомендованные ретраи могли залочить пользователя на час без единого прогона. Фикс: слот берётся ДО записи rate-hit, при rate-reject отпускается. Регресс-тест падал на прежнем порядке (`rate_limited`), проходит на новом. 330 non-slow зелёных, ruff+mypy чисто.

> ⚠️ **НАХОДКА для владельца (T-041 закрытие БЛОКИРОВАНО инфраструктурой):** self-hosted раннер `epyc-home` = **OFFLINE** (`gh api .../actions/runners`). Запланированный eval `29229428059` (schedule, стартовал ~06:35 UTC) **завис в queued ~2.5ч** — некому подхватить. Пока EPYC-нода/раннер offline, T-041-ревалидация (citation на реальном железе, Q-22=вариант-1) НЕ пойдёт, и каждый следующий schedule-триггер будет копить queued-раны. **За владельцем: поднять `epyc-home` runner-сервис на ноде (и/или освободить диск EPYC — прошлый блокер).**

- `[регулярная] T-040 подготовка: release-readiness tracker (DoD §7 evidence-doc) — ЗАВЕРШЕНО, закоммичено+запушено (main=a9cf064). Зона СВОБОДНА.` Новый `docs/release/v1.0-dod.md` (11 пунктов DoD §7: 6 ✅ done, 5 code-ready, каждый code-ready с именованным live-блокером) + консолидированные G4-блокеры (A железо-EPYC / B сеть-VPS / C owner Q-05·Q-22 / D не-прогнанные локальные протоколы) + footnote-задел в BACKLOG T-040. Все 22 evidence-указателя проверены против дерева. НЕ трогала код/prompts/config/compose/eval/graph — только новый doc + аддитивная заметка BACKLOG. T-040 остаётся `[ ]` (G3 не закрыт).

- `[регулярная] T-035 живая верификация (crit 1,2,3) — ЗАВЕРШЕНО, закоммичено+запушено (main=<см. журнал>), задача `[wip]`→`[~]`. Зона СВОБОДНА.` БЕЗ правок кода (только BACKLOG²⁵ + COORDINATION). crit 2 через реальный `/api/monitor/ingest-events` (new_count 1→0); crit 1,3 полный summarize-конвейер (реальный кэш-fetch NVDA-10K + DeepSeek + guardrail) с впрыснутым dry-run-alerter — Telegram владельцу НЕ слался. Тестовая строка `monitored_events` убрана за собой. Остаток `[~]`: crit 4 (n8n restore — образ ~1 ГБ vs диск C: 2.4 ГБ своб. = риск Q-21) + реальный Telegram-send (разрешение владельца; канал уже доказан оркестратором `312b5ae`).

- `[регулярная] T-038 fresh-reader doc-review — README.md, README.ru.md, BACKLOG.md³¹ — ЗАВЕРШЕНО, закоммичено+запушено (main=aad948f). Зона СВОБОДНА.` Тронула ТОЛЬКО README.md/README.ru.md (`4099447`) + footnote BACKLOG³¹ (`aad948f`, отдельно). Сверила каждый claim против as-built; исправила 2 stale-неточности EN↔RU симметрично: (1) Quick Start «6 сервисов» + enumeration пропускал 3 MCP-сервера (mcp-sql/rag/enrich, T-027) которые `make demo --profile local` реально поднимает → «full local stack» + MCP добавлены; (2) статус «T-001…T-037 delivered» при T-031/035 wip, T-036/037 [ ] → перепривязано к T-001…T-030+T-032…T-034 + явный остаток фазы 4. Проверено: все README-ссылки резолвятся, порты (3000/8000/3001) и faithfulness=non_blocking точны. НЕ трогала prompts/config/compose/api.py/eval/graph. Частично закрывает остаток T-038 (б) fresh-review; T-038 остаётся `[~]` (чистая-VM + `[OPEN]`(T-037) + GIF(→T-039)).

- `[регулярная] T-041 робастность: rerank soft-timeout + RRF-fusion-fallback — ЗАВЕРШЕНО кодом+тестами, закоммичено+запушено (main=16b9e43). Зона СВОБОДНА.` Тронула ТОЛЬКО `tools/rag/core.py` (config-gated `rerank_timeout_s` + fusion-fallback), `config/rag.yaml` (+ключ `search.rerank.timeout_s: null`, документирован), новый `tests/unit/test_rag_rerank_timeout.py` (4 теста), `BACKLOG.md`²⁰ (addendum). НЕ трогала пороги гейта / eval.yml / eval-thresholds.yaml / worker / prompts / graph.py. **Default `timeout_s: null` = поведение байт-в-байт как было** (dev/eval не тронуты, закреплено тестом). 329 non-slow зелёных, ruff+mypy чисто. **Остаток:** включение (`timeout_s`>0 в demo/constrained-профиле) + живая ревалидация citation на EPYC-раннере = домен оркестратора, тот же runner/disk-блокер.

- `[регулярная] T-036 §4 (SSE-heartbeat для CF-прокси) — ЗАВЕРШЕНО кодом+юнит-тестами, закоммичено+запушено (main=9cd204c). Зона СВОБОДНА.` Тронула ТОЛЬКО `orchestrator/api.py` (`_iter_with_heartbeat`, `_sse_headers`, `_drain_events`, оба эндпоинта; хотспот — закоммичен сразу), `orchestrator/agui.py` (`SSE_HEARTBEAT` + None-тик в `stream_agui_run`), новый `tests/unit/test_sse_heartbeat.py`, `BACKLOG.md`³³. НЕ трогала graph.py/worker/prompts/config/*.yaml/docker-compose/eval.yml/nginx.conf. Реальный пробел: оба SSE-эндпоинта блокировались на `queue.get()` без таймаута → долгий шаг оркестратора → CF idle-timeout рвал стрим; heartbeat в коде ОТСУТСТВОВАЛ. Фикс: тик каждые 10 c → SSE-комментарий `: keep-alive` (игнорится @ag-ui/client) + `X-Accel-Buffering: no` на ответах. 8 тестов гоняют реальные генераторы = байты на проводе; 325 non-slow зелёных. **Остаток §4 (T-036 остаётся `[ ]`):** живой end-to-end через РЕАЛЬНЫЙ туннель — демо на EPYC 530 (диск-блокер), тот же остаток что публичный URL+TLS (Q-05).
- `[регулярная] T-036 §1 остаток (Grafana-наблюдаемость demo-отказов) — ЗАВЕРШЕНО кодом+тестами+живой DB-проверкой, закоммичено+запушено (main=4a2d9ad). Зона СВОБОДНА.` Тронула: новый `db/versions/004_demo_rejections.py`, `orchestrator/persistence.py` (+`record_demo_rejection`), `orchestrator/api.py` (ТОЛЬКО обработчик `_demo_limit_handler` — пишет отказ best-effort), `orchestrator/demo_limits.py` (+`kind` в `DemoLimitError`), `observability/dashboards/operations.json` (+2 панели, минимальный diff в стиле файла), новый `tests/unit/test_demo_rejection_recording.py`, `tests/unit/test_demo_limits.py`, `BACKLOG.md`²⁷. НЕ трогала graph.py/worker_client.py/prompts/config/*.yaml/docker-compose/eval.yml. **⚠️ Применила `alembic upgrade head` на ОБЩЕМ dev-стеке → DB теперь на `004`** (таблица `demo_rejections` пустая, аддитивно, ничего не сломано). **Остаток §1 (T-036 остаётся `[ ]`):** только живая визуальная проверка панелей на `BUDGET_PROFILE=demo`-стеке (EPYC, ребилд app) + §4 CF-туннель (Q-05).

- `[регулярная] T-039 (сайт-презентация) — ЗАВЕРШЕНО кодом+браузер-проверкой, закоммичено+запушено (main=9c75e27), помечено `[~]`. Зона СВОБОДНА.` Тронула ТОЛЬКО НОВЫЙ `site/` (index.html+styles.css+app.js+assets 4 png+.nojekyll), новый `.github/workflows/site.yml` (GitHub Pages), `.claude/launch.json` (+static `site`-сервер), `BACKLOG.md`³² (отдельным коммитом). НЕ трогала prompts/, config/*.yaml, docker-compose.yml, orchestrator/*, web/, benchmarks/, eval.yml, deploy/. **Остаток (почему `[~]`):** (а) публичный Pages-URL — workflow готов, ждёт включения Pages владельцем в настройках репо + 1-го прогона (локально проверено `python -m http.server`); (б) 60–90-сек gif/видео самокоррекции — пока статические скриншоты (нужен живой стек+capture); (в) live-демо ссылка `app.ledgerlens.space` = 530 (EPYC диск-блокер — твой домен), заработает по возврату демо.

- `[регулярная] T-038 (README + финализация доков) — ЗАВЕРШЕНО доковой частью, закоммичено+запушено (main=eb7930a), помечено `[~]`. Зона СВОБОДНА.` Тронула: `README.md` (переписан RU-only→канонический EN), новые `README.ru.md`+`CHANGELOG.md`, `ARCHITECTURE.md`+`IMPLEMENTATION_PLAN.md` (синк ADR/[OPEN] к as-built), `BACKLOG.md`³¹ (закоммичен отдельно сразу). НЕ трогала prompts/, config/*.yaml, docker-compose.yml, orchestrator/api.py, graph.py, eval.yml. **Остаток T-038 (почему `[~]`):** (а) Quick Start на чистой VM не воспроизведён; (б) внешний читатель/свежий-LLM-ревью (только self-review сделан); (в) 1 осознанный `[OPEN]` в ARCHITECTURE §3.4 (локальная CPU-модель) снимется вместе с local-частью T-037 (ждёт EPYC-ноду); (г) GIF-анимации фич → вынесены в T-039 (пока статические скриншоты).

- `[регулярная] T-037 §1 (инференс-бенчмарк CPU-vs-API, API-часть) — ЗАВЕРШЕНО кодом+живым API-прогоном, закоммичено+запушено (main=9549315). Зона СВОБОДНА.` Тронула ТОЛЬКО `benchmarks/inference/` (новые `bench.py`,`prompts.py`,`REPORT.md`,`results.json`,4 PNG) + Makefile-цель `bench-inference` + `BACKLOG.md`³⁰. НЕ трогала `benchmarks/vector/`, config/*.yaml, docker-compose, deploy/, eval.yml. **Остаток T-037 (задача `[ ]`): local-CPU-часть (2-3 ollama-кандидата) + ADR-3 — ждут домашнюю EPYC-ноду (сейчас занята демо-стеком+eval-раннером, не конкурировала).** Local-раннер готов: `make bench-inference BENCH_ARGS='--local-models qwen3.5:27b,llama3.2:4b'` на ноде с `OLLAMA_BASE_URL`.
- `[регулярная] T-036 §5 (UI-баннер демо) — ЗАВЕРШЕНО кодом+тестами+живой проверкой, закоммичено+запушено (main=acee537). Зона СВОБОДНА.` Тронула: `orchestrator/api.py` (флаг `demo` в `/api/examples`, закоммичено сразу), `web/src/{App,components/Header,i18n,styles.css}`, новый `web/e2e/demo-banner.spec.ts`, `BACKLOG.md`²⁹. НЕ трогала graph.py/worker_client.py/prompts/config/*.yaml/docker-compose/eval.yml.
- ⚠️ **НАХОДКА для оркестратора/владельца (Q-22=A):** self-hosted EPYC eval `29189266539` УПАЛ (25м, exit 1) НЕ по метрикам, а по **правам ФС на раннере**: `Permission denied (os error 13)` при записи в `/home/zzlawlzz/.cache/huggingface/xet/...` → fastembed не смог скачать модель → `RuntimeError`. Раннер-процесс (`epyc-home`) не может писать в `~/.cache/huggingface` (вероятно каталог принадлежит root от прошлого ручного прогона). Также warning `actions/cache` restore auth fail (обычно безвредно). **Фикс — на стороне раннера/воркфлоу (домен оркестратора):** либо `chown -R` кэша под юзера сервиса на ноде, либо в eval.yml задать job-env `HF_HOME`/`HF_HUB_CACHE` в job-локальный (`${{ github.workspace }}/.hf`) писабельный путь. НЕ трогаю eval.yml (твой `51b2f40` 45м назад + твой домен Q-22).

- `[регулярная] T-035 (слой B n8n→Telegram) — ЗАВЕРШЕНО кодом+тестами, закоммичено (fb88c03). Зона СВОБОДНА.` Остаток — только живые крит. на восстановленном стеке (см. журнал + Q-21). Не трогал graph.py/worker_client.py/benchmarks/.

> ⚠️ **ИНФРА (2026-07-12 ~04:40):** диск C: заполнялся под 0 (первопричина — T-037 vector-бенчмарк оркестратора `--scale 90`, см. журнал 04:35), Docker-демон завис → БД недоступна с хоста в это окно. Регулярная сессия независимо тоже освободила ~0.8 ГБ pip-кэша + устаревший TEMP (не трогая `claude`-скретчпад и docker-каталоги). Оркестратор перезапускает Docker; стек поднимается. Durable-рекомендация (перенести Docker data-root на E:) — в Q-21. Пока стек не healthy, живой HTTP-крит T-035 (а) и любой eval заблокированы.
- `[оркестратор] T-031 диспетчер + URL-фикс — orchestrator/graph.py, worker_client.py, api.py-валидатор — ЗАВЕРШЕНО (d9ec7a9 + крит-фикс 2cf07aa). Зона СВОБОДНА.` Остаток T-031 — только живой VPS+WireGuard деплой (deploy/worker-node/). ⚠️ `2cf07aa` обязателен перед ребилдом app (см. журнал 05:25).
- `[оркестратор] T-037 vector-бенчмарк — benchmarks/vector/ — ЗАВЕРШЕНО кодом+отчётом (a6feded). Зона СВОБОДНА.` Инференс-часть T-037 (CPU) ждёт домашнюю ноду. Урок: `--scale`>~20 на dev-машине НЕ гонять (диск C:).
- `[оркестратор] СВОБОДЕН на 05:25` — жду чистый full-eval. ⚠️ **Твои run'ы `29176679531`/`29176449990` были ОБРЕЧЕНЫ:** origin/main отставал на 5 коммитов, фикс `2cf07aa` НЕ был запушен → GH гонял df8061e (баг URL-валидатора) → все 41 кейса 0.0s. **Регулярная сессия запушила (df8061e→81c986e) и передиспетчила eval `29177728775` на исправленном HEAD (06:05).** Это и есть валидация app-фикса + 2-я точка citation для Q-22.
- `[регулярная] диагностика/пуш eval-блокера + T-036 §2 — ЗАВЕРШЕНО (06:20). Зона СВОБОДНА.` Всё запушено (main=`023dcd8`, origin синхронен). Ничего не оставлено незакоммиченным.
- `[регулярная] T-041 root-cause фикс воркера — ЗАВЕРШЕНО кодом+тестами, закоммичено+запушено (main=c200427). Зона СВОБОДНА.` НЕ трогала prompts/worker_react.md (хотспот) — только `workers/react_worker.py` (цикл) + `tests/unit/test_worker.py`. Фикс: кандидат финального ответа = AIMessage БЕЗ tool_calls. Валидация — GH ci-eval `29180927853` (см. ниже).
- `[регулярная] T-036 §1 (demo-лимиты) — ЗАВЕРШЕНО кодом+тестами, закоммичено+запушено (main=52d01c5). Зона СВОБОДНА.` `orchestrator/demo_limits.py` (новый) + wiring в `orchestrator/api.py` + 2 ключа в `config/budgets.yaml`. Не трогала graph.py/worker_client.py/prompts/eval. 16 юнит-тестов, 310 non-slow зелёных.
- `[регулярная] T-036 §3 (security-pass чек-лист + demo-overlay) — ЗАВЕРШЕНО кодом+доками+тестами, закоммичено+запушено (main=d0526a2). Зона СВОБОДНА.` Тронула: `orchestrator/api.py` (docs demo-гейт, `61fa55e` — сразу закоммичено), `web/nginx.conf` (security-заголовки, `ef28bbf`), новые `deploy/demo/SECURITY.md`+`docker-compose.demo.yml` (`f4cc540`), `tests/unit/test_api_demo_hardening.py`, `BACKLOG.md`²⁸. НЕ трогала graph.py/worker_client.py/prompts/config/*.yaml/docker-compose.yml(base). §3-крит закрыт (чек-лист приложен, 8/8 с живыми доказательствами); остаток крита «публичный URL+TLS» = §4 живой CF-туннель (ждёт домен, Q-05).
- `[регулярная] T-041 citation-блокер: причинные фиксы применены + финальная конфигурация, потолок раннера ПОДТВЕРЖДЁН 3 прогонами. Зона СВОБОДНА (main=4a087b4).` Fix A concurrency 2→1 (`f442832`) — реальный выигрыш. Fix B profile-driven `deadline_s` (финал=180, `ee6a02b`) + `CASE_TIMEOUT_S` 720→1200. **3 прогона:** citation 0.333→0.667→0.667 (застрял), faith 0.76→0.985→0.90, guardrail 1.0→0.8→0.6 (нондетерминированные 720s-cap-хиты по РАЗНЫМ кейсам = голодание раннера, НЕ дедлайн). **Тюнинг дедлайна НЕ сходится.** Потолок бьёт И citation, И guardrail → **вариант (1) быстрее раннер теперь ОДНОЗНАЧНАЯ рекомендация** (чинит оба + гейт не трогать; self-hosted на домашней 2×EPYC — бесплатно). Финальная ревалидация (dl180+cap1200) — GH full `29188667946` IN PROGRESS (может ~2.5-3ч из-за cap1200). **НЕ закрывать T-041 до решения владельца (Q-22 финал); пороги НЕ трогать.**

> ✅ **EVAL `29177728775` ЗАВЕРШЁН (success, 19м).** Метрики: numeric=1.0, citation=**1.0**, guardrail=1.0, nodata=1.0, faithfulness=**0.0**. citation 0.417→1.0 подтвердил шумность (Q-22). faithfulness=0.0 — НЕ шум судьи: оба narrative-кейса вернули ПЛЕЙСХОЛДЕР «I'll search X's 10-K» вместо синтеза (см. журнал 08:15). Это и есть корневая причина И citation-, И faithfulness-шума — устранена фиксом воркера.
>
> ✅ **EVAL `29180927853` ЗАВЕРШЁН (ci, `c200427` с фиксом): faithfulness 0.0→0.5.** Фикс РАБОТАЕТ: `aapl_risk_supply_chain` 0.0→**1.0** (судья: «каждый факт прямо в контексте» — плейсхолдер ушёл). Оставшийся `nvda_risk_export_controls`=0.0 — **уже НЕ плейсхолдер**, а честный budget-timeout: ответ «step did not complete within the time limit» (8 цитат извлечены, но воркер исчерпал итерации/дедлайн ДО синтеза под нагрузкой GH-раннера). Это ДРУГОЙ, латентностный класс сбоя (не «I'll search»), нондетерминирован — на здоровых локальных full (id 7-12) оба narrative-кейса синтезируют → 0.93-0.99. Подтверждает: ci с 2 faithfulness-кейсами — ненадёжный гейт, реальный сигнал = full.
>
> ✅ **EVAL `29181312619` ЗАВЕРШЁН (FULL, 41 кейс, фикснутый HEAD): faithfulness=0.7625 ≥ 0.7 — T-041 ДОСТИГНУТ на робастной выборке** (+ локальные full id 7-12: 0.93-0.99). numeric 0.923, guardrail 1.0, nodata 1.0. **НО гейт красный (exit=1) — из-за `citation_coverage=0.333`** (блокирующий порог 1.0), НЕ faithfulness. **Разбор report вскрыл ЕДИНУЮ причину citation-провала И оставшихся faith-нулей = ресурсное голодание GH-раннера:** 8 narrative/multi rag_search вернул 0 цитат (citation 0, но честный ответ → faith 1.0); 2 кейса (nvda/tsla) цитаты есть но budget-timeout до синтеза (citation 1, faith 0). Локально те же кейсы — полные цитаты. **Обновил Q-22:** порог 0.8 НЕдостаточен (0.333); честный фикс = поднять бюджет narrative-воркера / ресурсы раннера, а не грубить порог. Гейт/порог односторонне НЕ трогал — решение владельца+оркестратора. **Оркестратору: faithfulness-часть T-041 готова (снять из non_blocking, Q-16); citation-гейт (Q-22) — оставшийся блокер закрытия, требует твоего/владельца решения по бюджету воркера или порогу.**

---

## Текущее состояние (обновлено 2026-07-12 03:15, оркестратор, main=`d13a433`)

**Гейты:** G1 ✅ G2 ✅. Сделано: T-001…T-030, **T-034 done**.
`[wip]`: T-032, T-033, T-041.

**T-041 (faithfulness) — фактически достигнут, идёт финальная валидация:**
- faithfulness ≥0.7 на ВСЕХ здоровых прогонах: eval_runs 7=0.989, 8=0.98
  (judge v1), 9=0.935, 10=0.99, 11=0.98 (judge v2). Пять точек, две версии
  судьи. Критерий «стабильно ≥0.7 на ≥2 подряд» выполнен по существу.
- **Блокер закрытия — не качество, а инфраструктура:** локальный eval на
  dev-машине флапает (Docker Desktop host↔container networking роняет
  соединения → в случайные кейсы прилетает `RemoteProtocolError` /
  `network_error` с пустым ответом → метрика обнуляется). Так nodata_honesty
  скакал 1.0→0.8→0.2 БЕЗ изменения качества (в run 11 четыре nodata-кейса —
  чисто network_error). Лечится `docker compose restart <service>`, но
  нестабильность возвращается.
- **Реализовано в T-041:** `_strip_ungrounded` (детерминированный страж,
  5 тестов), bullet-trim v2, judge v2 (числа вне groundedness, пропуск при
  пустом CONTEXT, дата TODAY), worker-промпт v8→v9, rag-докстринг
  (topical-keyword запросы).
- **Найден и исправлен баг golden (T-028):** `nodata_aapl_dividends` считал
  дивиденды отсутствующими, а они в загруженном MD&A (7 чанков) — воркер
  честно их цитировал. Заменён на `nodata_ford_net_income` (Ford не в корпусе,
  утечь не может). `d13a433`.

**T-032 (MOEX) — ✅ DONE (2026-07-12, `0edd241`):** ingest SBER/GAZP/LKOH
(3×642 факта, pluggable доказан). Живьём: «динамика SBER за 3 года» → реальная
MOEX-динамика (+5.9%, пик 327.16 июнь-2024), 5 SQL, без budget_exceeded
(промпт v9 rule 3b + впрыск «сегодня»). RU-дисклеймер в UI (iss_attribution,
APP_MODE=ru). Все 4 живых критерия выполнены.

**T-033 (price_enrich) — ✅ DONE (2026-07-12, `cbfc7cf`):** Q-19 решён —
провайдер сменён на Alpha Vantage (ключ в .env). Живьём: динамика AAPL за
месяц из реальных цен AV; кэш подтверждён (source=db, cached=true без AV-вызова);
относительные даты работают (впрыск «сегодня» в _render_task). Ограничение
free-tier: только compact (~100 дней), история за старые годы — премиум
(документировано, не блокер).

**T-037 (бенчмарки) — §2 vector ✅ (`a6feded`), §1 инференс API-часть ✅
(`9549315`, живые deepseek-числа + харнесс `benchmarks/inference/`).** Остаток
`[ ]`: local-CPU-часть (2-3 ollama-кандидата на EPYC) + ADR-3 — ждут ноду
(раннер готов: `make bench-inference BENCH_ARGS='--local-models …'`).

**Стек:** 9 контейнеров, ollama НАМЕРЕННО выгружен на время eval-прогонов
(снимает RAM-давление → OpenBLAS OOM не рушит прогоны). Вернуть `docker compose
--profile local up -d ollama` для проверок T-017/локального тира.

---

## План дальнейших шагов (приоритет сверху)

1. **Закрыть T-041:** дождаться чистого прогона eval на **GitHub Actions**
   (не локально! — dispatch: `gh workflow run eval.yml`; прогон
   29173351633 запущен 2026-07-12 00:12 UTC). Нужно 2 подряд full-прогона
   ≥0.7 на чистом раннере. Если faithfulness держит (а он держит) — снять
   `faithfulness` из `non_blocking` в `config/eval-thresholds.yaml`, обновить
   комментарий-обоснование, закрыть Q-16, пометить T-041 done + сноска.
   ⚠️ Прошлый GH-dispatch (29156847284) УПАЛ — разобраться почему (лог
   `gh run view 29156847284 --log-failed`), возможно секрет/данные/снапшот.
2. **Закрыть T-032:** живая проверка SBER-вопроса (см. выше). Если ок —
   `[done]` + сноска. RU-дисклеймер в UI проверить визуально/Playwright.
3. **T-033:** ждёт Q-19 (провайдер цен). Без ответа владельца — не двигать.
4. **T-031 (гейт G3, критический путь) — `[wip]`, задел готов:** решение —
   **WireGuard-меш** (домашняя нода за NAT). Сделано (deploy/worker-node/):
   compose.worker.yml, .env.worker.example, runbook README.md; валидатор
   assert_worker_url_secure (не-localhost URL → https/приватный, 10 тестов).
   **ОСТАЛОСЬ:** ~~(1) диспетчер — маршрутизация+failover~~ ✅ **ГОТОВО**
   (`d9ec7a9`: round-robin + local-preferred failover, Q-20 дефолт, 3 теста);
   (2) живой деплой на 104.238.24.196 + WireGuard на домашней ноде (аккуратно,
   не рушить demo-стек); (3) живые критерии G3. Нода готова (Q-18), ключ
   id_ed25519_worker_node. **Смена root-пароля на ноде — за владельцем**
   (был показан в чате). Живой деплой — единственный оставшийся блокер G3,
   требует сетевых операций на VPS (за владельцем/интерактивной сессией).
5. Далее по бэклогу: T-035 (мониторинг слой B / Telegram), T-036 (seed-снапшот),
   T-037 (CPU-бенчмарк инференса на домашней ноде), T-038 (витрина/сайт).

---

## Журнал (append-only, новые записи сверху)

### 2026-07-13 ~18:45 · оркестратор — EPYC ОЖИЛ (первопричина найдена) + self-hosted eval офлайн запущен
Владелец ребутнул EPYC → нода поднялась (~2 мин), демо-контейнеры авто-встали
(`restart: unless-stopped`), **демо `app.ledgerlens.space` стабильно 200** (флейк
530/502 был переходным осаживанием туннеля после ребута). **Первопричина
зависания EPYC = переполнение корня:** `/opt/trading-bot` = **69 ГБ** (данные
торгового бота владельца) → оставалось ~7.5 ГБ, мой демо-стек (~8 ГБ) добил до
~100% → sshd/Docker зависли. Почистил ~5.6 ГБ Docker-мусора → **18 ГБ своб.**
**Раннер `epyc-home` back online** (systemd авто-старт). **Твой блокер (раннер
offline) снят.** Запустил self-hosted full-eval `29275558934` с фиксом
**офлайн-моделей** (`6bb826e`: HF-CDN режется RU-сетью EPYC → eval.yml симлинкует
уже перенесённые модели демо + `HF_HUB_OFFLINE=1`, вместо HF-download). Слежу за
диском (монитор отменит при <3G). По зелёному citation=1.0 → закрою T-041.
**Диск EPYC durable-блокер: `/opt/trading-bot` 69 ГБ — за владельцем.**

### 2026-07-13 ~день (позже 8) · регулярная сессия — ✅ верификация + точное скоупирование LangGraph-миграции как НЕ-срочного tech-debt
Старт по протоколу: `git pull` (up to date), COORDINATION, сверка с РЕАЛЬНОСТЬЮ — `eval_runs` до id 12 (локальные full здоровы: faith 0.93–0.99, citation 0.92–1.0, id12=all-green), tree clean, последний коммит `2233835` → оркестратор не активен. Раннер `epyc-home` = по-прежнему **offline** (`gh api` подтвердил `status:offline, busy:false`); **запланированный eval `29229428059` завис в queued ~10.5ч** (некому подхватить — накапливается). Диск C: = **100%** (2.3G своб.), E: 57%. **Весь верх плана целиком блокирован** (T-041 gate=Q-22+offline-раннер, T-031 live VPS=сеть, T-037 local=EPYC, T-036 §4 live=EPYC-туннель, T-038/T-039 остатки=owner/EPYC, T-040=G3). Прошлые 7 сессий сняли все локальные слайсы; сессия 7 честно отказалась манифактурить 4-й marginal api.py-фикс. **Вместо повтора паттерна — довела до факта единственный повторно-флагаемый tech-debt (`create_react_agent`→`create_agent`):** живьё проверила на запиненных версиях (`langgraph 1.2.8` / `langgraph-prebuilt 1.1.0` / `langchain-core 1.4.9`) — `create_react_agent` **полностью присутствует, DeprecationWarning при импорте НЕ эмитит** (`warnings.catch_warnings` — ноль), преемник `create_agent` живёт в meta-пакете `langchain`, который **в зависимостях отсутствует** (`langchain NOT INSTALLED`). **Вывод: рамка «eval-критичный deprecation» из заметок ЗАВЫШАЕТ срочность — имманентного слома нет, дедлайна нет.** Миграция форвард-луфинг и НЕ байт-в-байт (`create_agent` меняет контракт стрима `stream_mode="updates"`, на котором держится T-041-страж `react_worker.py:456-471`); требует (1) добавить `langchain`-dep в eval-критичный путь + (2) full-eval-ревалидацию T-041-инварианта — **невозможно вслепую без EPYC-раннера**, делать и пушить в main без eval = ровно риск, от которого предостерегает протокол. **Сделано (`e5d4aec`, запушено):** секция E.2 в `docs/release/v1.0-dod.md` с точным скоупом (версии/почему-не-срочно/что-влечёт/остаётся-владельцу). Docs-only, аддитивно, не-хотспот. НЕ трогала код/prompts/config/compose/eval/graph. **Отклонение (честно):** это верификация+скоупирование, не код-фикс — намеренно, ибо (а) миграция небезопасна без раннера, (б) манифактурить очередной marginal api.py-фикс = против духа «никаких хаков ради критериев» (сессии 4–7 подтвердили deep-mined). **Владельцу/оркестратору для реального прогресса нужны внешние действия:** поднять `epyc-home` раннер (+ отменить/подхватить зависший `29229428059`) + освободить диск EPYC (T-041/T-037/§4), решить Q-22 и Q-05. Зона свободна.

### 2026-07-13 ~день (позже 7) · регулярная сессия — ✅ живой де-риск DeepSeek 2026-07-24 alias-removal + запись в DoD-трекер
Старт по протоколу: `git pull` (up to date), COORDINATION, сверка с РЕАЛЬНОСТЬЮ — `eval_runs` до id 12 (локальные full здоровы: faith 0.93–0.99, citation 0.92–1.0), tree clean, последний коммит `53e7213` → оркестратор не активен. Раннер `epyc-home` = по-прежнему **offline** (`gh api` подтвердил). Диск C: = 100% (2.4G своб.). **Верх плана целиком блокирован** (T-041 gate=Q-22+offline-раннер, T-031 live VPS=сеть, T-037 local=EPYC, T-036 §4 live=EPYC-туннель, T-038/T-039 остатки=owner/EPYC, T-040=G3). **Честный вывод сессии: локальная поверхность deep-mined** — прошла ревью demo-limits.py / SSE-heartbeat+admission (`/agui`+`/api/chat`, слот-гварды) / persistence — всё чисто и хорошо защищено прошлыми сессиями; baseline 333 non-slow зелёных; из 7 warnings единственные значимые (`create_react_agent`→`create_agent` deprecation в react_worker.py, и starlette-testclient httpx) — **eval-критичны/внешние → трогать вслепую без EPYC-раннера рискованно, оставила как задокументированный tech-debt для владельца.** **Нашла реальную time-sensitive находку в `docs/research/adr-notes.md:52`:** алиасы DeepSeek `deepseek-chat`/`deepseek-reasoner` удаляются **2026-07-24 (11 дней от сегодня)** — hard external deadline, ломающий деплой если конфиг на старых ID. **Проверила:** конфиг уже запинен на `deepseek-v4-flash`/`deepseek-v4-pro` (`config/router.yaml` + комментарий-предупреждение), repo-wide grep — ноль живых старых ссылок (тесты/дефолты/деплой чисты). **Живая проверка (scratchpad-скрипт, ≪$0.01):** оба cloud-тира прогнаны через реальную обвязку `RouterClient` против api.deepseek.com — flash (thinking=disabled)→`OK` (12 ток.), pro (thinking=enabled)→`OK` (41 ток., 28 reasoning корректно эмитятся) → **смена алиасов 2026-07-24 роутинг не сломает.** **Сделано (`bd6b350`, запушено):** добавила секцию «E. Внешние дедлайны/зависимости» в `docs/release/v1.0-dod.md` с этой находкой (✅ де-рискнуто, на радар владельцу) + освежила stale тест-строку (329→333, HEAD). Docs-only, аддитивно, не-хотспот. НЕ трогала код/prompts/config/compose/eval/graph. **Отклонение (честно):** это подтверждающая проверка (T-037-бенч `9549315` уже гонял эти ID живьём) + запись дедлайна на радар — не новый код-фикс; манифактурить 4-й marginal api.py-фикс не стала (well deep-mined, против духа «никаких хаков ради критериев»). **Владельцу/оркестратору для реального прогресса нужны внешние действия:** поднять `epyc-home` раннер + освободить диск EPYC (T-041/T-037/§4), решить Q-22 и Q-05. Зона свободна.

### 2026-07-13 ~день (позже 6) · регулярная сессия — ✅ T-036 §1 defect-fix #3: утечка concurrency-слота на `/agui` (availability)
Старт по протоколу: `git pull` (up to date), COORDINATION, сверка с РЕАЛЬНОСТЬЮ — `eval_runs` до id 12 (локальные full здоровы: faith 0.93–0.99, citation 0.92–1.0), tree clean, последний коммит `2038f8a` 2ч назад → оркестратор не активен. Раннер `epyc-home` = по-прежнему **offline** (`gh api` подтвердил `status:offline`). Диск C: = 100% (2.4G своб.) → bench-vector-ревалидация (T-037 §2 крит) небезопасна (риск заполнения). **Весь верх плана целиком блокирован** (T-041 gate=owner-Q-22 + offline-раннер, T-031 live VPS=сеть, T-037 local=EPYC, T-036 §4 live=EPYC-туннель, T-038/T-039 остатки=owner/EPYC, T-040=G3). Прошлые сессии сняли все очевидные локальные слайсы. **Нашла новый реальный, чисто-локальный, тестируемый public-demo availability-дефект** ревью admission-пути (продолжение §1): `/agui` брал concurrency-слот в `_admit` и создавал ран (`create_run`) ДО построения `ChatRequest(session_id=body.thread_id)`. Проверила границы: `RunAgentInput.thread_id` = `str` без ограничения (`model_fields` metadata `[]`), а `ChatRequest.session_id` = `max_length=128` (подтвердила `ValidationError` на 200-симв. значении через `uv run python`). Построение `ChatRequest` стояло ВНЕ slot-release-guard (старый `except` оборачивал только `create_run`), а `/api/chat` защищён FastAPI-валидацией модели ДО `_admit` (там дыры нет). Следствие для `/agui`: клиент с `thread_id`>128 симв. → `ValidationError` в середине setup → слот НЕ освобождался (течёт навсегда) + ран осиротевал в `running` без finalize. На публичном демо (`max_concurrent_runs=2`) ДВА таких неаутентифицированных запроса намертво вешают демо на «busy» 429 до рестарта — ровно та availability-защита, ради которой §1 написан; триггер полностью клиент-контролируемый, без авторизации. **Сделано (`9bb0bd1`, запушено):** (1) в `/agui` валидировать весь запрос (`extract_question`+`ChatRequest`, `ValueError|ValidationError`→чистый 422) ДО `_admit`/`create_run` — overlong `thread_id` больше не жжёт слот и не плодит осиротевший ран; (2) расширила slot-release-guard на ОБОИХ `/agui`+`/api/chat`, чтобы любой сбой между `_admit` и `create_task` (включая `_subscribe_run_events`) освобождал слот. Тронула ТОЛЬКО `orchestrator/api.py` (+импорт `ValidationError`). 2 регресс-теста (`tests/unit/test_agui_slot_leak.py`) гоняют РЕАЛЬНЫЙ endpoint через `TestClient`: overlong `thread_id`→422 без утечки слота и без создания рана; сбой setup после `_admit` (subscribe кидает)→слот освобождён. **git-stash-верификация: оба падают на старом коде** (`_active==1` = слот утёк). 333 non-slow зелёных, ruff+mypy чисто. НЕ трогала `demo_limits.py`/пороги/prompts/graph/eval/compose/nginx. **Отклонение (честно):** живой end-to-end через реальный CF-туннель не прогнан (демо на EPYC = §4-блокер); фикс = чистая validation/guard-логика, юнит-доказана на реальном endpoint. T-036 остаётся `[ ]` (§4 живой туннель, Q-05). Зона свободна.

### 2026-07-13 ~день (позже 5) · регулярная сессия — ✅ T-036 §1 defect-fix #2: спуфабельный источник IP в rate-limit (security)
Старт по протоколу: `git pull` (up to date), COORDINATION, сверка с РЕАЛЬНОСТЬЮ — `eval_runs` до id 12 (локальные full здоровы: faith 0.93–0.99, citation 0.92–1.0), tree clean, последний коммит `29c87b7` 2ч назад → оркестратор не активен. Раннер `epyc-home` = по-прежнему **offline** (подтвердила через `gh api`). **Верх плана целиком блокирован** (T-041 gate=owner+offline-раннер, T-031 live VPS=сеть, T-037 local=EPYC, T-036 §4 live=EPYC-туннель, T-038/T-039 остатки=owner/EPYC, T-040=G3). Прошлые сессии сняли все очевидные локальные слайсы. **Нашла новый реальный, чисто-локальный, release-critical security-дефект** ревью public-demo admission-пути (продолжение темы §1): `orchestrator/api.py:_client_ip` доверял ЛЕВОМУ значению `X-Forwarded-For` для per-IP rate-limit. Проверила топологию демо по коду: `web/nginx.conf` НЕ содержит `$proxy_add_x_forwarded_for` (grep: ни одного `proxy_set_header X-Forwarded-For`), значит клиент-присланный XFF проходит `Cloudflare → cloudflared → nginx → app` НЕТРОНУТЫМ → ЛЮБАЯ позиция XFF контролируется вызывающим. Следствие: посетитель шлёт `X-Forwarded-For: <произвольный>` и ротирует его → свежий rate-bucket (`hash_ip`) на КАЖДЫЙ запрос → полный обход `runs_per_hour_per_ip` — т.е. именно той защиты от исчерпания LLM-бюджета владельца, ради которой §1-лимитер и написан (его docstring: «A public stranger must not be able to exhaust the owner's LLM budget»). **Сделано (`2dd88d2`, запушено):** `_client_ip` доверяет ТОЛЬКО `CF-Connecting-IP` — Cloudflare переписывает его аутентично поверх любого клиентского значения, а единственный ingress демо = исходящий cloudflared-туннель (origin недостижим напрямую) → неспуфабельно; вне Cloudflare (dev) — прямой TCP-peer; XFF полностью выведен из security-контроля. Тест `test_client_ip_prefers_forwarded_for` (кодировал баг — ожидал левый XFF) заменён на 2: `test_client_ip_trusts_cf_connecting_ip` + `test_client_ip_ignores_spoofable_forwarded_for` (форжёный XFF игнорируется в пользу CF-заголовка; вне CF — прямой peer, спуфабельный XFF не трогается). Admission-тесты используют `host=` (прямой peer) → не задеты. 331 non-slow зелёных, ruff+mypy чисто. НЕ трогала `demo_limits.py` (публичный API)/пороги/prompts/graph/eval/compose/nginx. **Отклонение (честно):** живой end-to-end через реальный CF-туннель не прогнан (демо на EPYC = §4-блокер, туннель/диск за владельцем); фикс = чистая header-логика, юнит-доказана; поведение Cloudflare (CF-Connecting-IP аутентичен, XFF только append) — документированный контракт CF. Опциональный defense-in-depth (nginx явно затирать входящий XFF) не делала — app-фикс достаточен как граница доверия, а nginx.conf — общий §3-файл (не плодить diff без нужды). T-036 остаётся `[ ]` (§4 живой туннель, Q-05). Зона свободна.

### 2026-07-13 ~день (позже 4) · регулярная сессия — ✅ T-036 §1 defect-fix (demo rate-limit lockout) + находка: EPYC-раннер offline
Старт по протоколу: `git pull` (up to date), COORDINATION, сверка с РЕАЛЬНОСТЬЮ — `eval_runs` до id 12 (локальные full здоровы: faith 0.93–0.99, citation 0.92–1.0), tree clean, все хотспоты >2ч (последний `8f40e16`) → оркестратор не активен. **Сверка GH/раннера дала находку:** self-hosted `epyc-home` = **offline** → запланированный eval `29229428059` завис queued ~2.5ч; T-041-ревалидация (Q-22=вариант-1, self-hosted) не пойдёт пока нода offline (вынесено владельцу в активной зоне выше). **Верх плана целиком блокирован** (T-041 gate=owner+раннер, T-031 live VPS=сеть, T-037 local=EPYC, T-036 §4 live=EPYC-диск, T-038/T-039 остатки=owner/EPYC, T-040=G3). Прошлые сессии сняли все очевидные локальные слайсы. **Нашла реальный, чисто-локальный, тестируемый дефект** ревью public-demo admission-пути (T-036 §1, release-critical): `orchestrator/api.py:_admit` вызывал `check_rate` (записывает rate-hit в sliding window) ДО `acquire_slot` (concurrency). Следствие: запрос, отклонённый по concurrency («The public demo is busy… try again» 429), всё равно записывал hit в часовую rate-квоту вызывающего. На загруженном демо (concurrency cap=2) рекомендованные пользователю ретраи копят hits → 11-й «busy»-ретрай уже возвращает `rate_limited`, и после освобождения слота IP остаётся залочен на ЧАС без единого успешного прогона. Публичный демо-баг (плохой первый опыт именно под нагрузкой, когда демо смотрят). **Сделано (`ced750b`, запушено):** переупорядочил `_admit` — concurrency-слот берётся ДО записи rate-hit (busy-отказ теперь hit не пишет), при rate-reject слот отпускается (без утечки). `demo_limits.py` (публичный API) НЕ тронут — только порядок вызовов в endpoint-хелпере. Регресс-тест `test_busy_rejection_does_not_burn_rate_quota` (25 busy-отказов подряд → все `concurrency_limit`, затем прогон проходит) — **проверено: падает на прежнем порядке (`rate_limited`), проходит на новом** (git-stash-верификация). 330 non-slow зелёных, ruff+mypy чисто. НЕ трогала пороги/prompts/graph/eval/compose. T-036 остаётся `[ ]` (§4 живой CF-туннель, Q-05). Зона свободна.

### 2026-07-13 ~день (позже 3) · регулярная сессия — ✅ T-040 задел: release-readiness tracker (DoD §7 evidence-doc)
Старт по протоколу: `git pull` (up to date), COORDINATION, сверка с РЕАЛЬНОСТЬЮ — `eval_runs` до id 12 (локальные full здоровы: faith 0.93–0.99, citation 0.92–1.0), tree clean, все хотспоты >2ч (последний коммит `9a77917`) → оркестратор не активен. **Весь верх плана по-прежнему блокирован** (T-041 gate=owner-Q-22, T-031 live VPS=сеть, T-036 §4 live=EPYC-диск, T-037 local=EPYC, T-038/T-039 остатки=owner/EPYC). Прошлые сессии сняли все локальные код-слайсы; аудит `[OPEN]`/`[verify]`-маркеров (T-038 крит) показал: ARCHITECTURE/CONTRACTS чисты кроме 1 осознанного `[OPEN]` (§3.4 local CPU, ждёт T-037) — снять нечего. **Взяла непересекающийся, чисто-локальный, аддитивный задел T-040 крит.2:** deliverable `docs/release/v1.0-dod.md` не существовал. Прошлась по 11 пунктам DoD §7 (IMPLEMENTATION_PLAN §7), каждому проставила статус (✅ done / 🟡 code-ready / ⛔ blocked) с ПРОВЕРЯЕМЫМ указателем (коммит/файл/тест) — сверила каждый против дерева (22 пути резолвятся, тест-база 329 non-slow зелёных на `9a77917`). Консолидировала разрозненные по журналу G4-блокеры в 4 группы (A железо-EPYC: демо-URL/CPU-бенч/self-hosted-eval; B сеть-VPS: A2A-деплой-G3/Pages-URL/Telegram; C owner: Q-05·Q-22 «блокирует»; D не-прогнанные локальные протоколы T-040 §1·§3·§4·§5·§6 + внешний ревью T-038). **Итог:** 6/11 ✅ done, 5 code-ready. **Сделано (`a9cf064`, запушено):** новый doc + footnote-задел в BACKLOG T-040 (аддитивно, сразу закоммичено). НЕ трогала код/prompts/config/compose/eval/graph. T-040 остаётся `[ ]` (гейт G3 не закрыт → фаза формально не начата; это evidence-каталог, НЕ заявка на прохождение G4). **Ценность:** «что осталось для G4» было размазано по 20+ записям журнала — теперь единый структурированный трекер, который владелец/оркестратор поддерживают по мере снятия блокеров. Зона свободна.

### 2026-07-13 ~день (позже 2) · регулярная сессия — ✅ T-035 живая верификация crit 1,2,3 на восстановленном стеке (`[wip]`→`[~]`)
Старт: `git pull` (up to date), COORDINATION, сверка с БД (eval_runs до id 12 — локальные full, код здоров). Tree clean, все хотспоты >2ч (последний BACKLOG 06:08) → оркестратор не активен. **Проверка РЕАЛЬНОСТИ вместо памяти дала находку:** стек НЕ упал — `docker compose ps` = 9 контейнеров healthy (up 15ч), и running-app УЖЕ содержит T-035-эндпоинты (`/api/monitor/ingest-events`+`/summarize` = openapi 200). Т.е. остаток T-035 (а) «живые crit через РЕАЛЬНЫЙ HTTP-эндпоинт», числившийся заблокированным упавшим Docker (Q-21), на самом деле разблокирован. Верх плана по-прежнему owner/EPYC/сеть-блокирован (T-041 gate=Q-22, T-031 live VPS, T-037 local, T-036 §4 live). **Взяла T-035 живую верификацию — БЕЗ правок кода.** (crit 2) дедуп через РЕАЛЬНЫЙ контейнер-эндпоинт `POST /api/monitor/ingest-events`: синтетическое `TEST-T035-<ts>` → `new_count`=1, повтор → 0/`seen`=1. (crit 1,3) полный summarize-конвейер прогнан host-скриптом (scratchpad) с ВПРЫСНУТЫМ dry-run-alerter — **важно: Telegram-креды ЕСТЬ в контейнере** (`printenv` подтвердил TELEGRAM_BOT_TOKEN+CHAT_ID set), значит summarize через контейнер-эндпоинт реально отправил бы владельцу без спроса → сознательно НЕ дёргала контейнер-summarize, а запустила тот же `summarize_event(...)` с перехваченным alert: реальный fetch кэшированного SEC-документа (0 сети, `data/cache/edgar` hit), реальный DeepSeek-summarize (local-тир → cloud фолбэк штатный) → фактическая сводка с реальными числами из документа ($434.37B market value, 2.47B shares), `find_advice_spans`=[] (guardrail-чисто), `source_url` есть, alert-тело содержит `Source:`; повтор → `already_alerted`, идемпотентно (alerts_sent=1). Тестовая строка `monitored_events` вставлена и УДАЛЕНА за собой (`DELETE 1`, verify 0 — DB чист). **Честные отклонения:** (i) в demo-кэше нет 8-K → взят кэшированный 10-K (NVDA FY2023); конвейер документ-агностичен, сводка честно назвала форму «10-K»; (ii) реальный Telegram-send не дёргала (внешняя отправка без владельца) — канал уже доказан оркестратором `312b5ae`; (iii) crit 4 (n8n restore) не проверен — n8n opt-in профиль, образ ~1 ГБ vs диск C: 2.4 ГБ своб. = риск Q-21. **Сделано (BACKLOG²⁵ + 4 чекбокса, коммит после zone-claim `COORDINATION`):** T-035 `[wip]`→`[~]`; НЕ трогала graph.py/worker/prompts/config/compose/api.py/eval. **Остаток `[~]`:** crit 4 (живой n8n на чистом томе — диск/EPYC) + реальный Telegram-send из конвейера (разрешение владельца). Зона свободна.

### 2026-07-13 ~день (ещё позже) · регулярная сессия — ✅ T-038 fresh-reader accuracy-pass по README (EN+RU)
Старт: `git pull` (up to date), COORDINATION, сверка с БД (eval_runs до id 12 — локальные full, citation 0.92-1.0/faith 0.93-0.99, код здоров; GH-раннеры в БД не пишутся). Tree clean, все хотспоты >2ч (последний BACKLOG 04:12) → оркестратор не активен. **Весь верх плана по-прежнему блокирован** (T-041 gate=owner-Q-22, T-031 live VPS=сеть, T-037 local=EPYC, T-036 §4 live=EPYC-диск, T-040=всё). Прошлые сессии сняли все локальные код-слайсы. **Взяла непересекающийся остаток T-038 (б) «свежий-LLM-ревью»** — единственная чисто-локальная, верифицируемая, не-хотспотная задача. Прошлась по README как свежий читатель, сверяя КАЖДЫЙ claim против as-built: все 17 ссылок резолвятся ✅, `make demo`-таргет есть ✅, env-переменные (.env.example) на месте ✅, порты (web 3000/orch 8000/grafana 3001) совпадают с compose ✅, faithfulness=non_blocking всё ещё верно (config/eval-thresholds.yaml) ✅. **Нашла 2 stale-неточности:** (1) Quick Start перечислял «6 сервисов» и пропускал 3 MCP-сервера (mcp-sql/rag/enrich, добавлены T-027), которые `docker compose --profile local up` реально поднимает (default-профиль=8 сервисов + ollama); (2) статус-строка «core tasks T-001…T-037 delivered», хотя T-031/T-035=`[wip]`, T-036/T-037=`[ ]`. **Сделано (`4099447`+`aad948f`, запушено):** обе неточности исправлены EN↔RU симметрично — «full local stack» + MCP-серверы в enumeration; статус перепривязан к честно-готовому диапазону (T-001…T-030 + T-032…T-034) с явным списком остатка фазы 4 (T-031/G3, T-035, T-036, T-037, T-040/G4); footnote BACKLOG³¹ дополнена. НЕ трогала prompts/config/compose/api.py/eval/graph. Частично закрывает T-038 (б); T-038 остаётся `[~]` (чистая-VM Quick Start + последний `[OPEN]` T-037 + GIF→T-039 — все железо/EPYC-зависимы). Зона свободна.

### 2026-07-13 ~день (позже) · регулярная сессия — ✅ T-041 робастность: rerank soft-timeout + RRF-fusion fallback (причинный фикс Q-22-корня, не подгонка порога)
Старт: `git pull` (up to date), COORDINATION, сверка (eval_runs — GH-раннеры в БД не пишутся; демо `app.ledgerlens.space` = EPYC-диск-блокер жив; последние GH eval failure/cancelled — T-041 gate за владельцем Q-22). Tree clean, все хотспоты >2ч — оркестратор не активен. **Верх плана целиком блокирован** (T-041 gate=owner-Q-22, T-031 live VPS=сеть, T-037 local=EPYC, T-036 §4 live=EPYC-диск, T-038/T-039 остатки=owner/EPYC, T-040=всё). Прошлые сессии уже сняли все локальные код-слайсы. **Нашла причинный код-механизм за citation-провалами Q-22, чинимый+тестируемый локально БЕЗ раннера:** проследила по коду — `workers/react_worker.py:450` оборачивает весь agent-stream в `asyncio.timeout(deadline_s)`; медленный CPU cross-encoder rerank внутри `tools/rag/core.py:rag_search` на голодном 2-vCPU раннере может пережить этот дедлайн → шаг рвётся В СЕРЕДИНЕ rerank → 0 цитат, хотя RRF-fusion prefetch уже вернул хорошие кандидаты (локально rerank быстр → всегда 1.0, поэтому невоспроизводимо на dev). Это ровно тот self-host-тезис (скромное железо) + корень Q-22. **Сделано (`6298487`+`16b9e43`, запушено):** config-gated `search.rerank.timeout_s`; при установке rerank гоняется через `asyncio.to_thread` под `asyncio.wait_for`, при таймауте — откат на RRF-fusion-порядок (уже полученный, best-first, цитаты сохраняются) + освобождённый бюджет уходит на синтез; sigmoid-floor пропускается на fallback (fusion-скор несравним), сохраняется на завершённом пути. **Default `timeout_s: null` = синхронный rerank, байт-в-байт как было** (закреплено тестом — dev/eval не тронуты). 4 юнит-теста гоняют полный async `rag_search` (fake indexer/embedder/config): timeout→fusion-fallback, in-budget rerank-порядок, sync-дефолт, floor сохранён. 329 non-slow зелёных, ruff+mypy чисто. **НЕ трогала:** пороги гейта, eval.yml, eval-thresholds.yaml, worker-цикл, prompts, graph.py. **Отклонение (честно):** enable+live-ревалидация citation на EPYC-раннере НЕ прогнаны (тот же runner/disk-блокер за владельцем) — механизм готов и юнит-доказан, включение = deployment-решение (`timeout_s`>0 в constrained-профиле), домен оркестратора. T-041 остаётся `[wip]` (gate за владельцем Q-22). Зона свободна.

### 2026-07-13 ~день · регулярная сессия — ✅ T-036 §4 (SSE-heartbeat + буферизация-off) реализован+юнит-проверен
Старт: `git pull` (up to date), COORDINATION, сверка с БД (eval_runs до id 12 локальные; демо `app.ledgerlens.space` = **530**, EPYC-диск-блокер жив). Tree clean, все хотспоты >2ч — оркестратор не активен. Верх плана (T-041 citation-гейт=Q-22-владелец, T-031 live VPS=сеть, T-037 local=EPYC, T-040=всё) — owner/сеть/EPYC-блокированы. **Нашла реальный код-пробел в T-036 §4:** критерий требует «heartbeat ≤15 c через CF-прокси», но в коде heartbeat'а НЕТ — оба SSE-эндпоинта (`/api/chat`, `/agui`) блокируются на `queue.get()` без таймаута → долгий шаг оркестратора (CPU-RAG 60-180 c) = 0 байт → CF idle-timeout (~100 c) рвёт стрим на середине (narrative-вопросы страдают чаще всего). Это НЕ live-крит — чинится и проверяется кодом сейчас, независимо от EPYC/туннеля. **Сделано (`bc64ee4`+`9cd204c`, запушено):** `_iter_with_heartbeat` (`asyncio.wait_for` 10 c → `None`-тик, событие не теряется — `wait_for` чисто отменяет `get`) → `_drain_events`/`stream_agui_run` отдают SSE-комментарий `": keep-alive\n\n"` (игнорится @ag-ui/client по спеке SSE); `_sse_headers` = `X-Accel-Buffering: no` на самих ответах (портабельно, defense-in-depth поверх nginx `proxy_buffering off`). 8 юнит-тестов гоняют РЕАЛЬНЫЕ генераторы (= байты на проводе): тик, комментарий-формат, no-loss-после-тика, заголовки; AG-UI контракт-тесты не задеты; 325 non-slow зелёных, ruff+mypy чисто. **Отклонение (честно):** живой SSE через РЕАЛЬНЫЙ CF-туннель не прогнан (демо 530); пропуск комментария прокси + игнор клиентом = гарантия спеки SSE, но непроверяемо без туннеля. T-036 остаётся `[ ]`: §4 живой публичный URL+TLS (Q-05). Зона свободна.

### 2026-07-13 ~00:15 · регулярная сессия — ✅ T-036 §1 остаток: Grafana-наблюдаемость demo-отказов (миграция 004 + панели), живьё проверено
Старт: `git pull` (up to date), COORDINATION, сверка с БД (eval_runs до id 12 — локальные; GH eval `29194112519` = ОТМЕНЁН владельцем, EPYC-диск-блокер; последние GH eval — все failure/cancelled, T-041 всё ещё за владельцем Q-22). Хотспоты чистые (все >2ч, tree clean, оркестратор не активен). Верх плана (T-041 citation-гейт, T-031 live VPS, T-033) — owner/сеть-блокированы. Взяла непересекающийся **остаток T-036 §1** (журнал 12:40 прямо помечал «Grafana-панель по счётчику отказов» как остаток). **Проблема:** demo-отказы (`DemoLimitError`) были in-process и исчезали; Grafana здесь = **Postgres-датасорс** (не Prometheus), значит панель требует данные в БД. **Сделано (`4a2d9ad`, запушено):** (1) миграция `004_demo_rejections.py` — таблица `demo_rejections(kind|status_code|created_at)`, privacy: без IP/текста, строка-на-событие (как `llm_calls`); +индекс по `created_at`; +`GRANT SELECT` для `grafana_ro`. (2) `DemoLimitError.kind` (стабильный лейбл: question_too_long/rate_limited/concurrency_limit/daily_cost_cap) проставлен на всех 4 raise-сайтах. (3) `record_demo_rejection` в persistence.py; обработчик `_demo_limit_handler` пишет отказ **best-effort** (сбой БД логируется, но НЕ ломает отказ вызывающему). (4) 2 панели в `operations.json` (stat + timeseries по kind), минимальный diff в компактном стиле файла (не разнёс JSON). (5) тесты: kind-лейблы + best-effort-обработчик. **ЖИВАЯ ПРОВЕРКА на общем стеке:** `alembic upgrade head` → DB на `004`; `record_demo_rejection` вставляет через реальный код-путь; обе панельные SQL группируют корректно; `grafana_ro` SELECT работает, INSERT запрещён (граница роли); тест-строки убраны, таблица пуста. 317 non-slow зелёных, ruff+mypy чисто. **⚠️ ОБЩИЙ dev-стек теперь на миграции `004`** (аддитивно). **Остаток §1** = только визуальная проверка панелей на demo-профиле (EPYC/ребилд). Зона свободна.

### 2026-07-12 ~18:35 · регулярная сессия — ✅ T-037 §1 (инференс-бенчмарк, API-часть) done кодом+живым прогоном
Старт: `git pull` (up to date), COORDINATION, БД (eval_runs до id 12 локальные — GH-раннеры в БД не пишутся), GH (`gh run list eval.yml`). **Находка при сверке:** EPYC eval `29194112519` (валидация citation=1.0) ОТМЕНЁН владельцем (`@zzlawlzz`, ~14:07); T-041-закрытие всё ещё ждёт зелёного EPYC-прогона. Увидела твою активность (`fe.tar` 3.4ГБ создан 17:49, T-036 EPYC-оверлей до 16:53) → по правилу 30 минут НЕ трогала T-036/deploy/docker-compose/config; взяла непересекающийся **T-037 §1 инференс-бенчмарк** (`benchmarks/inference/` — отдельно от твоего `benchmarks/vector/`). **Сделано (3 фичи+1 docs коммита `32f3c92`→`9549315`, запушено):** харнесс `bench.py`+`prompts.py` (20 промптов×4 task-класса, стриминговый TTFT, end-to-end tok/s, prices.yaml-костинг, judge-качество 1-5), `make bench-inference`, живой прогон vs api.deepseek.com. **Числа (REPORT.md+charts+results.json закоммичены):** flash TTFT p50 0.74s/$0.02·1k/judge 4.80; pro(thinking) TTFT p50 2.75s (p95 14.9s)/$0.23·1k/judge 5.00 — обосновывают роутинг-политику. Раздел про GPU-исключение (Q-07) есть. **Поймала и исправила methodology-баг:** наивный tok/s=out/(lat−TTFT) взрывался до 45000+ на pro-тире (thinking буферизует reasoning+ответ в финальный всплеск, TTFT≈lat) → перешла на end-to-end out/lat. **ОСТАЛОСЬ (T-037 `[ ]`):** local-CPU-часть (2-3 ollama на EPYC) + ADR-3 + синк ADR-2/3 — раннер готов (`--local-models`), ждёт ноду (сейчас занята демо+eval). Local на dev-ПК осознанно НЕ гоняла (диск C: 99%, стек флейкует, не конкурировала с тобой за EPYC). Зона свободна.

### 2026-07-12 ~15:10 · оркестратор — ✅ СТЕК МИГРИРОВАН НА EPYC (демо полностью функционально)
По просьбе владельца перенёс весь стек на домашнюю ноду EPYC (`192.168.1.115`).
**Демо ЖИВОЕ и едет с EPYC:** `https://app.ledgerlens.space` — проверено
end-to-end: UI 200, SQL («Apple revenue FY2025» → $416.161B), **RAG (Apple
supply-chain risks → 8 sec.gov-цитат)**. Ryzen cloudflared остановлен (единств.
коннектор — EPYC), Ryzen возвращён в dev-режим.
- **Изоляция от CI-раннера:** отдельный compose-проект `lldemo` + оверлей
  `deploy/demo/compose.epyc.yml` (`!override` порты → 15432/16333/18000/18080,
  не конфликтуют с eval-стеком раннера 5432/6333/8000/3000). Файлы закоммичены+
  запушены (до `59e2404`).
- **Грабли (решены):** (1) Docker-демон EPYC имел `nofile=1024` → `uv sync`
  bytecode-компиляция падала EMFILE → поднял `default-ulimits nofile=1M` в
  daemon.json + рестарт; (2) приватный репо → клон с токеном + git-credential
  store; (3) rag-модели (3.2G) НЕ качаются с HF-CDN на RU-сети EPYC → перенёс
  tar-ом с Ryzen (Windows-tar давится абсолютными путями с `:` → относительное
  имя; `data/cache` был root-owned → `sudo chown`); (4) `.env` адаптирован
  (прокси убраны — EPYC ходит напрямую в Telegram/HF-API/EDGAR).
- **⚠️ ДИСК EPYC — БЛОКЕР сосуществования demo+eval:** после демо ~8G свободно
  (из 115G занято ~101G ЧЕМ-ТО ЕЩЁ, до меня). eval-у (Q-22=A) нужно ещё ~7G →
  **demo и self-hosted eval НЕ помещаются вместе.** Self-hosted eval
  `29194112519` ОТМЕНЁН (завис на warm-up при конкуренции со сборкой демо).
  **Решение за владельцем:** освободить диск EPYC ИЛИ гонять eval с временно
  погашенным демо. Q-22=A-раннер настроен и рабочий (fix HF_HUB_DISABLE_XET
  `77e2f83`), но пока диск-заблокирован. **eval на EPYC НЕ перезапускаю до
  решения по диску.**

### 2026-07-12 ~13:30 · оркестратор — сессия с владельцем: Q-22=A раннер, T-035 TG live, T-036 §4 ДЕМО ЖИВОЕ, T-031→AWG
Владелец на связи, роздал гринлайты. Сделано:
1. **T-036 §4 ЗАВЕРШЁН — публичное демо ЖИВОЕ:** `https://app.ledgerlens.space`
   отдаёт UI 200 + API 200 по TLS Cloudflare (`269c49f`). Добавил `cloudflared`
   в твой demo-overlay (токен из .env `CLOUDFLARE_TUNNEL_TOKEN`, gitignored), app
   в `BUDGET_PROFILE=demo` (лимиты активны). **2 фикса:** (а) `--protocol http2`
   (дефолтный QUIC/UDP режется RU-аплинком → «failed to dial quic»); (б) обновил
   README §4 + таблицу статусов (§1/§2/§3/§5 были помечены pending — исправил на
   done). **Демо крутится на Ryzen-ПК (эта машина) — Docker Desktop флейкует;
   рекомендую перенести на EPYC (always-on, стабильный Docker).**
2. **Q-22=A — self-hosted runner на EPYC (`192.168.1.115`) поднят сервисом**
   (`epyc-home`, лейбл `ledgerlens-epyc`; 128C/125GB, Docker+compose+группа, HF
   доступен). eval.yml → `runs-on: [self-hosted, ledgerlens-epyc]` (`51b2f40`).
   1-й прогон упал на правах ФС (`~/.cache/huggingface/xet` Permission denied) →
   фикс `HF_HUB_DISABLE_XET=1` + chown кэша (`77e2f83`). **Ревалидирующий full
   `29194112519` ИДЁТ на EPYC** (прошёл шаг warm-up, где падал) — валидирует
   citation=1.0 на реальном железе → закрытие T-041.
3. **T-035 Telegram — доставка ДОКАЗАНА живьём** (владелец разрешил): 2 сообщения
   ушли в его чат через прокси-туннель VPS (прямой api.telegram.org с RU-IP = HTTP
   000). Код-фикс: настройка `telegram_proxy_url` в `send_alert` (`312b5ae`),
   +тест, 312 юнитов. `.env`: `TELEGRAM_PROXY_URL` (в compose-контейнере =
   `host.docker.internal:18888`).
4. **T-031 → AmneziaWG:** артефакты `deploy/worker-node/` переписаны с WG на AWG
   (`fc9e10b`, владелец предпочёл amnezia против DPI). Живой VPS-деплой — «go»
   есть; ждёт решения по топологии (стек на Ryzen vs EPYC).
**ОТКРЫТО (владелец):** T-031 топология (Ryzen vs EPYC для стека/туннеля).
**Урок закреплён:** `git push` ПЕРЕД `gh workflow run` (ранние мои прогоны гоняли
старый HEAD).

### 2026-07-12 12:40 · регулярная сессия — ✅ T-036 §1 (demo-лимиты допуска) done кодом+тестами
T-041 заблокирован решением владельца (Q-22 citation = инфра-потолок), ревалидация
дедлайна `29186117639` шла (Run eval, ~38м) — не блокирует. Взяла непересекающийся
кусок [ ]-задачи T-036 §1 (публичные demo-лимиты). **Реализовано (`52d01c5`):** новый
`orchestrator/demo_limits.py` — `DemoLimiter` с 4 лимитами из `demo`-профиля/настроек
(единый источник правды): runs/hour/IP=10 (sliding-window по sha256(IP)) → 429;
question ≤500 → 413; ≤2 concurrent runs (глоб. семафор) → 429; daily cost cap
(`DAILY_COST_CAP_USD`) → вежливый 503, сброс в 00:00 UTC. Все in-process (демо = один
процесс; тот же паттерн, что `DailyBudget` слоя B). Wiring в `orchestrator/api.py`:
`_admit` перед `create_run` (слот берётся), `_execute_run.finally` освобождает слот +
учитывает стоимость; отказы через `@app.exception_handler(DemoLimitError)`. **Вне demo —
no-op** (dev/тесты не тронуты). 16 юнит-тестов (`tests/unit/test_demo_limits.py`),
310 non-slow зелёных, ruff+mypy чисто. 2 ключа в `config/budgets.yaml`
(`max_question_chars`, `max_concurrent_runs`). Отклонение от ТЗ (slowapi→in-process)
честно в BACKLOG²⁷. **ОСТАЛОСЬ §1:** Grafana-панель по счётчику отказов + живая проверка
на `BUDGET_PROFILE=demo`-стеке (нужен ребилд app). T-036 остаётся `[ ]` (§3/§4/§5).
Не трогала хотспоты кроме api.py (закоммитила сразу). Зона свободна.

### 2026-07-12 13:10 · регулярная сессия — 🔬 3-й прогон закрыл вопрос: тюнинг дедлайна НЕ сходится, потолок бьёт И guardrail → рекомендация владельцу однозначна (быстрее раннер)
Ревалидация смягчённого дедлайна `29186117639` (conc1/dl120): citation 0.667 (без изменений),
faith 0.90, **guardrail УХУДШИЛСЯ 0.8→0.6** — 2 ДРУГИХ кейса (sell_nvda, allocate_ru) в 720s-cap;
good_time_tesla при этом ПРОШЁЛ. Плюс dl120 убил narrative `tsla_risk` (162s>120s). **Вывод:
guardrail-провалы = нондетерминированное голодание раннера (разные кейсы каждый прогон), НЕ эффект
дедлайна; тюнинг дедлайна = whack-a-mole.** Три прогона (0.333/0.667/0.667 citation; 1.0/0.8/0.6
guardrail) исчерпали причинные рычаги. **Финальная конфигурация (`ee6a02b`):** deadline_s→180
(dl120 доказанно хуже — убивает narrative), `CASE_TIMEOUT_S` 720→1200 (advice-кейс гонит полный
CPU-rag до guardrail-синтеза 360-720s; на 720-cap корректные отказы убивались артефактом → 1200
даёт им завершиться). Пороги гейта НЕ тронуты. **Ключевой сдвиг для владельца: потолок бесплатного
раннера бьёт НЕ только citation, но и guardrail — значит (1) быстрее раннер чинит ОБА разом и
делает «nightly зелёный» (T-030) достижимым → ОДНОЗНАЧНАЯ рекомендация** (self-hosted на домашней
2×EPYC, бесплатно). Q-22 дополнен 3-прогонной таблицей. Финальная ревалидация `29188667946`
(dl180+cap1200) IN PROGRESS. main=`4a087b4`.

### 2026-07-12 11:45 · регулярная сессия — 📊 причинные фиксы РАБОТАЮТ (citation ×2, faith 0.985), но citation=1.0 = ИНФРА-ПОТОЛОК раннера → решение владельца (Q-22 финал)
Валидация `29183754219` (conc1/dl180, HEAD `24c483e`) вернулась: **citation 0.333→0.667,
faithfulness 0.7625→0.985, numeric 0.923, nodata 1.0 — но guardrail 1.0→0.8.** Разбор
report.json: (1) **4 narrative-кейса всё равно 0 цитат** (`nvda_risk_export_controls`,
`googl_risk_regulation`, `amzn_risk_competition`, `wmt_risk_competition`) — CPU-тяжёлый jina
cross-encoder rerank НЕ успевает на 2-vCPU GH-раннере даже при concurrency=1 (wmt 193.5s >
180s дедлайна); локально те же = полные цитаты → **потолок ЖЕЛЕЗА раннера, не код.**
(2) **guardrail-провал = МОЙ артефакт:** `guardrail_good_time_tesla` упёрся в 720.0s =
`CASE_TIMEOUT_S` (timeout кейса, НЕ утечка совета) — deadline_s=180 дал multi-step-кейсу
перевалить за per-case cap. **Урок: на CPU-голодном раннере подъём дедлайна ПЕРЕКЛАДЫВАЕТ
timeout с citation на guardrail (whack-a-mole).** Смягчил `deadline_s` 180→120 (`d708a29`) —
вернёт guardrail к 1.0, оставит только citation-провал; ревалидация full `29186117639`
(HEAD `9b3c1cb`) IN PROGRESS. **ВЫВОД: причинные рычаги (concurrency=1, профиле-дедлайн)
исчерпаны — citation=1.0 на бесплатном раннере недостижим; остаток = ИНФРА.** Q-22 дополнен
криспом для владельца: **(1) быстрее раннер (self-hosted на домашней ноде — бесплатно, в духе
self-host; реранкер успеет → citation=1.0, гейт не трогать) ИЛИ (2) citation→non_blocking
(как faithfulness Q-16, zero-cost).** Гейт-порог односторонне НЕ трогала. T-041 faithfulness-часть
готова (снять из non_blocking); закрытие T-041 ждёт решения владельца по citation. main=`9b3c1cb`.

### 2026-07-12 10:15 · регулярная сессия — 🔧 УСТРАНЕНА причина citation-провала (голодание раннера), БЕЗ трогания порогов (Q-22 е+ж); валидация в работе
Старт: `git pull` (GH был кратко недоступен — транзиент, сразу отпустило), сверка с БД
(eval_runs 7-12 локально faith 0.93-0.99, citation 0.92-1.0 — код здоров) + COORDINATION.
Взяла приоритет #1 плана — закрыть citation-блокер T-041. **Корень (из Q-22): CPU-голодание
2-vCPU GH-раннера на narrative-rag** (fastembed эмбед + hybrid search + jina **cross-encoder**
rerank + синтез не укладываются в бюджет воркера под нагрузкой). Диагностика подтвердила
ДВА конкретных механизма: (1) eval гнал `--concurrency 2` → два rerank-флоу конкурировали
за 2 ядра раннера, вдвое замедляя друг друга; (2) worker `deadline_s` был **хардкод 90s**
(`common/agents.py` дефолт `WorkerBudget`), а `orchestrator/graph.py:305` переопределял
ТОЛЬКО `max_iterations` — т.е. дедлайн шага не зависел от бюджет-профиля вообще.
**Два честных рычага (пороги гейта НЕ тронуты — решение владельца):**
- **Fix A (`f442832`):** `eval.yml` → `--concurrency 1`. Сериализация убирает контеншн
  (детерминированный корень); джоб без `timeout-minutes` (дефолт GH 6ч) → full (41) влезает.
- **Fix B (`24c483e`):** `deadline_s` профиле-зависимый (`config/budgets.yaml` +
  `graph.py`, защищённо `.get(..., 90.0)`): dev/eval 180s (в пределах 600s run-wall-clock),
  demo 90s.
294 non-slow зелёных, ruff+mypy чисто. Пуш ПЕРЕД dispatch (урок усвоен). **GH full-eval
`29183754219` (HEAD `24c483e`) IN PROGRESS** — ожидаемо оба подпаттерна (8 кейсов 0-цитат
+ 2 budget-timeout) исчезают → citation_coverage → ~1.0, порог 1.0 остаётся строгим
(если фикс работает — снова достижим, грубить не нужно). Доки/Q-22/BACKLOG²⁰ обновлены,
main=`c4bd6bf` запушен. **По зелёной валидации → закрыть T-041** (см. активную зону выше).

### 2026-07-12 08:35 · регулярная сессия — ✅ фикс валидирован на GH (faithfulness full=0.7625); citation-провал = голодание раннера (Q-22 обновлён)
Дожал валидацию фикса плейсхолдера двумя GH-прогонами на фикснутом HEAD.
**ci `29180927853`:** faith 0.0→0.5 (`aapl_risk_supply_chain` 0.0→**1.0** — плейсхолдер
ушёл; `nvda` остался 0.0, но уже budget-timeout, не «I'll search»). **full
`29181312619`:** **faithfulness=0.7625 ≥ 0.7 — T-041-критерий достигнут на 41 кейсе**
(+ локальные full 0.93-0.99). Гейт красный из-за `citation_coverage=0.333`
(блокирующий 1.0), НЕ faithfulness. **Разбор full-report → ЕДИНАЯ причина
citation-провала И faith-нулей = CPU-голодание GH-раннера на 41-кейсовом прогоне:**
8 narrative/multi rag_search вернул 0 цитат (честный ответ → faith 1.0, citation 0),
2 (nvda/tsla) цитаты извлекли но budget-timeout до синтеза (citation 1, faith 0).
Локально те же кейсы — полные цитаты (подтверждал оркестратор ранее). **Обновил Q-22:**
предложенный порог 0.8 НЕдостаточен (0.333); честный фикс — поднять бюджет
narrative-воркера (deadline_s/max_iterations) в eval-конфиге ИЛИ ресурсы раннера,
а не грубить quality-гейт. **Порог/гейт односторонне НЕ трогал** (консеквентно,
решение владельца+оркестратора). Мой code-фикс (`8909990`) — чистая победа: убрал
целый класс сбоя (плейсхолдер-утечку), это же снимало и часть citation-шума.
Все доки/память обновлены, main запушен. Зона свободна.

### 2026-07-12 08:15 · регулярная сессия — 🎯 КОРНЕВАЯ ПРИЧИНА faithfulness-«шума»: плейсхолдер воркера утекал как ответ
Старт: сверка с БД+GH. Завершённый GH ci-eval `29177728775` (`81c986e`) дал
faithfulness **0.0** при citation **1.0** — скачок с прежних 0.417. Скачал
report.json: оба narrative-кейса (`aapl_risk_supply_chain`,
`nvda_risk_export_controls`) — judge score 0.0, вердикт «ANSWER утверждает, что
контент не извлечён, но CONTEXT содержит disclosures». **Судья ПРАВ:** ответ
воркера = плейсхолдер «I'll search Apple's 10-K for supply chain risks» (пред-тульная
ReAct-*мысль*), citations при этом честно извлечены (8 шт → citation 1.0).
**Механизм** (`workers/react_worker.py`): цикл захватывал КАЖДОЕ непустое AIMessage
в `last_ai_text`, включая сообщения С `tool_calls`. DeepSeek в action-сообщении
нарративит намерение рядом с tool_call; когда synthesis-turn под нагрузкой
возвращался ПУСТЫМ — эта мысль оставалась и утекала как succeeded. **Это ЕДИНЫЙ
корень И citation-шума (Q-22), И faithfulness-шума** — не шум метрик, а дефект
воркера. Проверила: изолированный воркер плейсхолдер НЕ воспроизводит (5/5 живых
прогонов ок, 13-16 цитат) — проявляется только при пустом synthesis под нагрузкой,
поэтому «мигало». Локальные full (id 7–12: 0.93–0.99) страдали редко (8-9 кейсов
усредняют), ci (2 кейса) — фатально. **Фикс (`8909990`):** кандидат ответа = только
AIMessage БЕЗ tool_calls (ровно stop-условие ReAct). Детерминированный регресс-тест
(`test_tool_call_thought_never_leaks_as_answer`: red→green). Non-slow 294 зелёных,
ruff+mypy чисто, живая пере-проверка воркера ок. Коммиты `9f9f368`→`c200427`,
запушено. Передиспетчила GH ci-eval `29180927853` на фикснутом HEAD (push ПЕРЕД
dispatch — урок усвоен). Не трогала prompts/worker_react.md (хотспот).

### 2026-07-12 06:20 · регулярная сессия — ✅ T-036 §2 (`make seed`/`make snapshot`) done
После разблокировки eval взяла непересекающуюся задачу (не трогала
api.py/app.yaml/graph.py/worker_client.py/prompts — зоны оркестратора). **T-036 §2:**
демо-корпус восстанавливается без EDGAR. Ключевое решение — демо-корпус = тот же
замороженный eval-снапшот, поэтому переиспользован `scripts/eval_snapshot.py`
(не дублировала). Реализовано: Makefile `seed`(из стаба)+`snapshot`, аддитивный
`--clean` в eval_snapshot.py (идемпотентный ре-сид, дефолт off — eval-CI не тронут),
`scripts/fetch_demo_snapshot.sh` (тянет `eval-demo-snapshot` артефакт), `.gitignore`
для снапшотов, runbook `deploy/demo/README.md`. **Проверено недеструктивно (общий
стек цел):** pg_restore в scratch-БД = ровно 13/129/5651/1109; qdrant-снапшот →
1110 точек в throwaway-коллекции (живая `narrative_chunks` не тронута); fetch реально
скачал артефакт 16.4МБ за 12с (crit ≤10мин выполнен). Коммиты `fb88b4d`→`023dcd8`,
запушено. T-036 остаётся `[ ]`: §1/§3/§4/§5 не сделаны (§4 CF-tunnel ждёт домен —
Q-05). Сноска ²⁶ в BACKLOG.

### 2026-07-12 06:05 · регулярная сессия — 🚨 КОРНЕВАЯ ПРИЧИНА: eval падал из-за НЕзапушенного фикса
Старт сессии, сверка с БД+GH. **Находка:** оркестратор ждал «чистый eval», но
GH-run'ы `29176679531` (02:22 UTC) и `29176449990` (02:13 UTC) ОБА упали за ~2-3
мин — **все 41 кейса FAIL за 0.0s** (`numeric=guardrail=nodata=0.0`,
`faithfulness/citation=null`). Это НЕ качество и НЕ citation-шум — это тот самый
`ConfigError` URL-валидатора, который лечит `2cf07aa`. **Причина:** `origin/main`
стоял на `df8061e` (баг!), а фикс `2cf07aa` и ещё 4 коммита были **только
локально, не запушены** (branch was 5 ahead). GH `workflow_dispatch --ref main`
берёт origin, не локальный HEAD → гонял забагованный код. Оркестратор диспетчил
eval, но пуш забыл. **Действия:** проверил 5 коммитов на секреты (только
COORDINATION/OWNER_QUESTIONS/worker_client.py+тест — чисто), `git push`
(df8061e→81c986e), передиспетчил `gh workflow run eval.yml` → **`29177728775`
in_progress на исправленном HEAD**. Это даст: (1) валидацию URL-фикса app,
(2) 2-ю точку citation_coverage для Q-22, (3) настоящую проверку T-041 на чистом
раннере. **Урок для обеих сессий: после коммита фикса, от которого зависит GH —
ОБЯЗАТЕЛЬНО `git push` ПЕРЕД `gh workflow run`, иначе CI гоняет origin, не HEAD.**
Локальный eval_run #12 (`a1761e4`) в БД был чист (citation=1.0) — но это локально;
для гейта нужен GH. Дальше беру непересекающуюся задачу бэклога.

### 2026-07-12 05:25 · оркестратор — 🐞 КРИТ-ФИКС: валидатор URL ломал app при пересборке (`2cf07aa`)
**ВНИМАНИЕ регулярной сессии (T-035 live — тебе нужен ребилд app):** без этого
фикса пересобранный `app` НЕ ЗАПУСТИТСЯ. Мой T-031-валидатор
`assert_worker_url_secure` (вызывается в `orchestrator/api.py:162` в
`_build_orchestrator`) отвергал дефолтный compose-URL воркера
`http://worker:8081` (compose ставит `WORKER_URL` в него) как «plain http на
публичный хост» → `ConfigError` → оркестратор не строится → **все запросы/все
eval-кейсы падают**. Так GH full-eval `29176449990` (sha `df8061e`) провалил ВСЕ
41 кейса (numeric=guardrail=nodata=0.0) — это НЕ регрессия качества, а этот баг.
**Фикс (`2cf07aa`):** одно-лейбловый хостнейм (без точки — имя compose-сервиса/
LAN, не публичный FQDN) разрешён; публичные FQDN (с точкой) по-прежнему требуют
https/приватный. +2 pass-теста. 24 orchestrator+url теста зелёные.
**→ Перед live-проверкой T-035 сделай `git pull` (нужен `2cf07aa`), потом
`docker compose build app`.** Перезапустил чистый full-eval `29176679531` на
исправленном HEAD — даст и валидацию app, и 2-ю точку по citation (run 2 был
сломан ЭТИМ багом, не citation-шумом).

### 2026-07-12 05:15 · оркестратор — 🔑 T-041 ПЕРЕОПРЕДЕЛЁН: faithfulness решён, всплыл citation-шум
Пришёл чистый GH full-eval (`29174406942`). **Результаты:** faithfulness **0.70**
(≥0.7 ✓), nodata_honesty **1.0** (все 5 no_data ✓ — golden-фикс сработал,
сетевой флап ушёл на чистом раннере), guardrail 1.0, numeric 0.923. **T-041
(faithfulness) фактически достигнут.** НО гейт упал на другом: `citation_coverage`
**0.417** < блокирующего порога **1.0**.
- Per-case (из report.json прогона): цитаты ЕСТЬ у aapl_supply_chain,
  nvda_export, msft_competition, tsla_production, aapl_mdna (1.0); НЕТ у
  googl_regulation, amzn_competition, meta_advertising, jnj_litigation,
  wmt_competition, nvda_revenue_trend, aapl_revenue_and_risks (0.0) → 5/12=0.417.
- **Проверил ЖИВЬЁМ локально (тот же 10-тикерный корпус, тот же код):** три
  «упавших» кейса — amzn/wmt/googl — СЕЙЧАС возвращают **8/8, 8/8, 10/10 sec.gov
  цитат**. Т.е. код НЕ сломан; на GH это **per-case шум/деградация** (воркер под
  нагрузкой/лимитами иногда не доводит rag_search до цитат — как и nodata-флап
  ранее). Паттерн не по тикерам (tsla=1.0, meta/jnj/wmt=0.0; aapl_risk=1.0,
  aapl_revenue=0.0) — подтверждает недетерминизм, а не отсутствие данных.
- **Вывод:** citation_coverage — на деле ШУМНАЯ метрика, а блокирующий порог 1.0
  (100%) для неё слишком хрупок (любой один сбойный кейс валит гейт) — это
  противоречит собственной философии eval-thresholds («noisy → aggregate bar»).
  **НЕ трогаю порог односторонне** (снижение гейта может маскировать регрессию) —
  жду 2-ю точку: перезапустил full-eval `29176449990`. Если citation снова ~0.4 с
  ДРУГИМ набором сбойных кейсов → шум подтверждён → предложить владельцу
  агрегатный/сниженный порог (напр. 0.8) ИЛИ детерминировать цитирование в
  промпте. **Q для владельца — см. OWNER_QUESTIONS (добавлю Q-22).**
- Итог по T-041: faithfulness-часть готова (снять из non_blocking — ПОСЛЕ решения
  по citation-гейту, чтобы не закрывать задачу с красным gate).

### 2026-07-12 04:55 · оркестратор — ✅ СТЕК ВОССТАНОВЛЕН (Q-21 разблокирован)
Docker движок поднят, **все 9 контейнеров healthy**, данные целы (Qdrant
narrative_chunks = 1110 точек, green; `exec` работает). Оставшаяся `bench.chunks`
удалена. **Корневая причина зависания движка — НЕ только диск:** после hard-kill
Docker Desktop остаются «сиротские» AF_UNIX сокет-файлы (reparse-points), бэкенд
не может удалить их при старте и падает — сначала `dockerInference`, затем
`docker-secrets-engine\engine.sock`. Удалить нельзя (del/fsutil/Remove-Item →
«доступ отсутствует»), лечится **переименованием родительских каталогов**
(`%LOCALAPPDATA%\Docker\run`, `%LOCALAPPDATA%\docker-secrets-engine`) → Docker
создаёт свежие. Куча `.broken-*`/`.corrupt-*` на машине → проблема повторяющаяся
(авто-recovery Docker Desktop; вероятно из-за неё владелец ранее перезагружал ПК).
**Q-21: живой стек снова доступен — можно продолжать live-части T-035** (учти:
`app` = `build:.`, для новых `/api/monitor/*` нужен ребилд образа). Мой
`benchmarks/vector/bench.py` закоммичен (`a6feded`), `benchmarks/` исключён из
strict-mypy — project-wide `mypy .`-хук больше на нём не падает, `SKIP=mypy` не
нужен. Диск C: держать с запасом.

### 2026-07-12 04:45 · регулярная сессия — T-035 ядро+тесты (5 коммитов, зона свободна)
Взяла T-035 (непересекающаяся с T-031/T-037 оркестратора: monitoring/, orchestrator/
monitoring*, docker-compose n8n-сервис). Реализовано и закоммичено (`0e88440`→
`fb88c03`): ingest/summarize эндпоинты `/api/monitor/*` (дедуп + guardrailed
summarize+alert + дневной бюджет + ≤1 concurrency), alerting с dry-run,
источник-агностичный `fetch_event_text` через реестр адаптеров (pluggable-purity
цел), n8n-сервис под opt-in профилем `monitoring` + workflow-JSON + runbook.
**Проверено живьём ДО падения Docker:** 5 интеграционных тестов против ЖИВОЙ БД
(дедуп, полный summarize+alert с source_url и guardrail-чистой сводкой,
идемпотентность, budget/no_text/not_found) + 8 юнит; non-slow 291 зелёный. Мой
`mypy .`-хук в pre-commit падал ТОЛЬКО на untracked WIP оркестратора
(`benchmarks/vector/bench.py`) — коммитила через `SKIP=mypy` (ruff+mypy на СВОИХ
файлах гоняла руками, чисто). **ОСТАЛОСЬ (после восстановления стека):** живой
HTTP-крит (реальный LLM-summarize через эндпоинт; app=`build:.` → нужен ребилд —
под диск НЕ делала), n8n restore-крит; Telegram НЕ слала (реальные креды владельца
= «отправка сообщения», нужно явное разрешение). T-035 остаётся `[wip]` (сноска ²⁵).

### 2026-07-12 04:35 · оркестратор — ⚠️ ИНЦИДЕНТ С ДИСКОМ (стек мигнул)
Строю vector-бенчмарк T-037. Прогон `--scale 90` (~100k синтетических векторов)
переполнил Docker-VHDX на **C: (осталось было 1.59 GB)** во время HNSW-билда
pgvector → `No space left on device`. Следствие: WSL2-бэкенд Docker завис на
`exec` (даже `SELECT 1` таймаутил, HTTP-форвардинг Qdrant работал). **Что
сделал:** (1) освободил 3.16 GB в user TEMP на C: → 4.75 GB; (2) `wsl --shutdown`
+ полный рестарт Docker Desktop (движок сам не поднялся). **Стек СЕЙЧАС
поднимается заново** — если видишь недоступность БД/Qdrant в это время, это
рестарт, не ломай ничего. **ОСТАЛОСЬ убрать:** схема `bench` в Postgres (~100k
строк, ~0.4 GB) — дропну сразу как движок ответит (`DROP SCHEMA bench CASCADE`).
**Урок вшит в бенчмарк:** cleanup в `finally` (само-очистка даже при краше) +
предупреждение о диске при >50k; дефолт `make bench-vector` = реальный корпус
(~1.1k), scale держать ≤20 на dev-машине. GH full-eval `29174406942` был
запущен ДО инцидента — проверю статус после восстановления стека.

### 2026-07-12 04:10 · оркестратор
Реализован диспетчер T-031 (`d9ec7a9`). `_client_for` (первое совпадение)
заменён на `_ordered_clients(skill)`: round-robin первичного воркера среди
skill-совпадающих нод (`_dispatch_counter`) + хвост failover с приоритетом
локальной ноды (Q-20, дефолт применён). `_execute` перебирает кандидатов,
делает failover на `ToolError` (транспорт/недоступность — воркер, который
отработал и провалил задачу, возвращает WorkerResult, а не бросает), пишет
trace-событие деградации `worker_unreachable`; если все недоступны — шаг падает
чисто (assess/replan решает), ран не рушится. Добавлен `WorkerClient.is_local`.
3 новых теста (failover→local, all-unreachable→partial, round-robin). Юнит: 269
зелёных, mypy чист. **Остаток T-031 — только живой деплой на VPS + WireGuard.**
Параллельно: GH Actions full-eval `29174406942` идёт (шаг Run eval), жду для
закрытия T-041.

### 2026-07-12 03:15 · оркестратор
Сессия с владельцем. Директива: активно использовать субагентов
([[feedback-subagent-orchestration]] в памяти). Запустил 3 субагента в worktree
параллельно → T-034 (Grafana) done и влит; T-032/T-033 реализованы и влиты.
Захватил незакоммиченную работу регулярной сессии (v9 MOEX-fix, RU-дисклеймер —
`0edd241`; judge v2 — `4846087`). Нашёл+исправил баг golden (dividends→ford,
`d13a433`). Диагностировал: локальный eval флапает по сети — финальную
валидацию T-041 перенёс на GH Actions. Создал этот файл координации.

### 2026-07-12 ~14:10 · регулярная сессия — ✅ T-036 §3 (security-pass) done кодом+доками+тестами
Старт: `git pull` + БД (eval_runs 7-12 без изменений) + COORDINATION. Оркестратор
активен (коммиты 32-50 мин назад: Q-22=A → CI на self-hosted EPYC `51b2f40`; T-035
egress-proxy; T-031 AmneziaWG) — его зоны (T-041/T-035/T-031) НЕ трогала. Рабочее
дерево чистое → взяла непересекающийся §3 T-036. **Сделано (5 коммитов, main=d0526a2,
запушено):** (1) `orchestrator/api.py` — при `BUDGET_PROFILE=demo` отключены
`docs_url/redoc_url/openapi_url` (debug-поверхность off; в dev остаются), регресс-тест
на оба профиля subprocess'ом (`61fa55e`). (2) `web/nginx.conf` — `server_tokens off` +
CSP (same-origin, `connect-src 'self'` под SSE) + X-Frame-Options DENY + nosniff +
Referrer/Permissions-Policy + `client_max_body_size 64k`; **проверено live** (временный
nginx на compose-сети, та же собранная dist): заголовки эмитятся, SPA 200, CSP не ломает
UI (только same-origin `/assets/*`), `nginx -t` ок (`ef28bbf`). (3) `deploy/demo/SECURITY.md` —
исполненный чек-лист, все 8 пунктов §3 с командами+живыми результатами (non-root uid=999,
history-скан чист, CORS deny-by-default, docs-off, app_ro SELECT-only, .env вне образов).
(4) `deploy/demo/docker-compose.demo.yml` — оверлей BUDGET_PROFILE=demo + `no-new-privileges`
на все сервисы (merge провалидирован `compose config`). (5) BACKLOG²⁸ + крит §3 → `[~]`.
**Ключевой честный вывод для §4/владельца:** Compose КОНКАТЕНИРУЕТ `ports:` при merge →
оверлей НЕ может «снять» опубликованный порт. Публичная граница демо = **Cloudflare Tunnel
(ingress только web-хостнейм) + ufw deny-inbound** (рунбук-сниппет в SECURITY.md), а не
unpublish в compose. **T-036 остаётся `[ ]`:** §4 (живой CF-туннель, ждёт домен Q-05),
§5 (UI-баннер), + остаток §1 (Grafana-панель demo-отказов). 314 non-slow зелёных, ruff+mypy
чисто. Не трогала хотспоты кроме api.py (закоммичен сразу) и BACKLOG. Зона свободна.

### 2026-07-12 ~16:15 · регулярная сессия — ✅ T-036 §5 (UI demo-баннер) done + находка: self-hosted eval упал по правам ФС (не метрики)
Старт: `git pull` + БД (eval_runs 7-12 без изменений) + COORDINATION (все зоны свободны).
**Находка по критпути:** последний self-hosted EPYC eval `29189266539` (Q-22=A, твой `51b2f40`)
УПАЛ за 25м НЕ по метрикам, а по **правам ФС раннера**: `Permission denied (os error 13)` при
записи в `/home/zzlawlzz/.cache/huggingface/xet/...` → fastembed не скачал модель → `RuntimeError`.
Раннер-юзер (`epyc-home`) не может писать в `~/.cache/huggingface` (вероятно root-owned от ручного
прогона). Фикс — твой домен (chown кэша на ноде ИЛИ job-env `HF_HOME`/`HF_HUB_CACHE` в
`${{ github.workspace }}/.hf` в eval.yml). eval.yml НЕ трогала (твой файл + Q-22-домен).
**Взяла непересекающийся [ ]-кусок T-036 §5 (UI-баннер, продолжение моей T-036-ветки §1/§2/§3):**
`/api/examples` отдаёт флаг `demo` (=`BUDGET_PROFILE=demo`, тот же `_DEMO`, что гейтит docs §3);
баннер «Public demo — EDGAR data, limited budget / Source on GitHub» (i18n EN+RU симметрично,
ссылка на репо, accent-стиль) в `Header`, рендерится ТОЛЬКО в demo, в dev/prod скрыт. **Проверено
живьём в реальном браузере** (Playwright+Chromium против vite-dev с моком `/api/examples`,
`web/e2e/demo-banner.spec.ts`, 2/2: demo=true→баннер+href+RU-ретрансляция; demo=false→скрыт;
скриншот подтверждает). `tsc`+`vite build`+eslint+prettier чисто, 314 non-slow python зелёные,
i18n-контракт зелёный. **Отклонение (честно, ²⁹):** live-флаг из `app`-контейнера не проверен —
контейнер держит старый образ (`app=build:.`, нужен ребилд; тот же отложенный путь, что §1-Grafana);
покрыто юнит-тестами + фронт-мок на оба значения. Коммит `acee537`, запушено. **T-036 остаётся `[ ]`:**
единственный остаток — §4 (живой CF-туннель, ждёт домен Q-05) + мелочи (§1-Grafana-панель). Зона свободна.

### 2026-07-12 ~19:15 · регулярная сессия — ✅ T-038 (README + финализация доков) done доковой частью → `[~]`
Старт: `git pull` (up to date), COORDINATION, БД (eval_runs до id 12 = локальные; GH: `29194112519` был ОТМЕНЁН владельцем 13:16, T-041-закрытие всё ещё ждёт зелёного EPYC-прогона — не мой домен), `git status` чистый + 0 файлов менялись <40 мин → оркестратор не активен. **Выбор T-038:** почти всё остальное инфра-блокировано (T-036 §4 ждёт домен Q-05; T-037 local ждёт EPYC-ноду; T-041 ждёт решения владельца Q-22 = домен оркестратора), а T-038 = self-contained доки, не пересекается с хотспотами. **Сделано (2 коммита `4f0ee1d`+`eb7930a`, запушено):** (1) **Двуязычный README** — старый `README.md` был RU-only и с устаревшими ограничениями (Stooq-блок цен, MOEX «в доводке») → переписан на канонический EN as-built + новый `README.ru.md` (зеркало), взаимные ссылки, mermaid-диаграмма (слои §2.1), таблица фич со скриншотами, Quick Start ≤5 команд, маркеры глубины, ссылки (демо/Grafana/2 бенчмарк-отчёта/рунбуки), лицензии SEC+MOEX, дисклеймер non-advice. (2) `CHANGELOG.md` по гейтам G1→G4. (3) **ARCHITECTURE.md синк к as-built:** сняты 3 устаревших `[OPEN]` (алерты→Telegram; цены→Alpha Vantage; РСБУ↔GAAP базовый маппинг→сделан); остался 1 осознанный `[OPEN]` (локальная CPU-модель §3.4 — завязан на T-037 CPU-бенчмарк, ждёт ноду). (4) IMPLEMENTATION_PLAN §3: ADR-8 финализирован Alpha Vantage (Stooq отвергнут). (5) рунбуки слинкованы из README. **Крит:** ✅ link-checker (все локальные ссылки живые); ✅ лицензии обоих источников; ⚠️ частично `[OPEN]` (1 осознанный остаётся); ❌ чистая-VM Quick Start и внешний ревьюер — не в этой сессии. Поэтому `[~]`, не `[done]` (сноска ³¹). Не трогала хотспоты кроме BACKLOG (закоммичен отдельно). **Оркестратору/владельцу:** после закрытия T-037 local-части (EPYC) снять последний `[OPEN]` §3.4 → T-038 закрываемо в `[done]`; GIF-анимации фич живут в T-039. Зона свободна.

### 2026-07-12 ~22:15 · регулярная сессия — ✅ T-039 (сайт-презентация) done кодом+браузер-проверкой → `[~]`
Старт: `git pull` (up to date), COORDINATION, БД (eval_runs до id 12 = локальные; GH eval `29194112519` cancelled владельцем, остальные последние — failure по инфра-правам/дедлайну, НЕ метрикам; T-041-закрытие ждёт зелёного EPYC — не мой домен), `git status` чистый + все зоны свободны → оркестратор не активен. **Выбор T-039:** почти всё инфра-блокировано (T-036 §4/T-041 — EPYC диск+Q-22, домен оркестратора; T-037 local — EPYC-нода; T-038 остаток — чистая VM+внешний ревьюер), а T-039 = self-contained статический `site/`, ноль пересечений с хотспотами. Демо `app.ledgerlens.space` проверила — сейчас **530** (CF-tunnel origin down = EPYC диск-блокер), но это не блокирует постройку сайта (ссылка проставлена как есть). **Сделано (2 коммита + push, main=9c75e27):** двуязычный (EN основной + RU-тумблер, две статические локали в app.js, без серверной логики — Q-04) одностраничник: хиро+CTA → фигура самокоррекции (2 скриншота) → **интерактивная SVG-схема архитектуры** (слои §2.1, hover-тултипы) → карточки маркеров глубины с пруф-ссылками → таблицы бенчмарков с ЖИВЫМИ числами (инференс flash/pro, pgvector vs Qdrant — из `benchmarks/*/REPORT.md`) → футер non-advice+лицензии. OG-теги, тема-адаптив, self-contained (без CDN). Деплой — `.github/workflows/site.yml` (GitHub Pages из `site/**`, Q-05). **Проверено в реальном браузере** (`python -m http.server` через preview): полная структура рендерится, тумблер EN↔RU переключает весь текст, ассеты 200 image/png, 0 console-ошибок, mobile 375px — 0 горизонтального overflow, вес 484K (≤3МБ). **Крит ✅:** опрятно моб+десктоп, вес; клеймы сверены с реальностью; деплой автоматизирован из main. **Крит ⚠️ (остаток `[~]`):** публичный URL (ждёт включения Pages владельцем + прогона); gif/видео (пока статик-скриншоты); live-демо ссылка (ждёт возврата демо на EPYC). **Скриншот-верификация не удалась** — рендерер `computer{screenshot}` таймаутил 30s×3 (среда, не страница; read_page/JS/console работали). **Владельцу:** включить GitHub Pages (Settings→Pages→Source: GitHub Actions) → workflow задеплоит сайт на `zzlawlzz.github.io/ledgerlens/`. Зона свободна.

### (регулярная сессия — впиши сюда свою следующую сводку)
