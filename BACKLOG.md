# Бэклог разработчика

**Проект:** **LedgerLens** — self-hostable мультиагентная платформа финансового анализа
**Исполнитель:** агент-разработчик (полная реализация)
**Основания:** ARCHITECTURE.md (что строим) · IMPLEMENTATION_PLAN.md (фазы и приёмка) · CONTRACTS.md (контракты и конвенции)
**Составил:** архитектор-аналитик, 2026-07-09

---

## 0. Как работать с бэклогом

- Нумерация T-001…T-040 — **порядок исполнения по приоритету**. Задачи без взаимных зависимостей можно вести параллельно, но не начинать задачу следующего этапа, пока не закрыт гейт текущего.
- **Гейты:** G1 = T-015 (вертикальный срез), G2 = T-026 (MVP), G3 = T-031 (маркеры глубины), G4 = T-040 (прод/портфолио-готовность). Критерии гейтов = критерии приёмки фаз в IMPLEMENTATION_PLAN §2 + Definition of Done §7.
- К **каждой** задаче применяется общий DoD из CONTRACTS.md §15 (lint/типы/тесты/CI/конфиг/секреты) — в частных критериях не повторяется.
- Пометки: `(Q-xx решён: …)` — решение владельца, вшитое в задачу (детали — OWNER_QUESTIONS.md); `[verify:T-002]` — деталь фиксируется после ресёрч-задачи T-002. Все вопросы Q-01…Q-12 закрыты (кроме Q-09: действует дефолтный watchlist, финализация — до T-028).
- Оценки: **S** ≤ 0.5 дня, **M** ≤ 1.5 дня, **L** 2–3 дня (для агента-разработчика — относительная сложность/риск).
- При обнаружении противоречия между задачей и ARCHITECTURE.md/CONTRACTS.md — остановиться и эскалировать архитектору, не «чинить молча».

## 1. Сводная карта (план от А до Я)

| # | Название | Этап | Зависит от | Оценка | Статус |
|---|---|---|---|---|---|
| T-001 | Каркас репозитория и тулинг | 0. Фундамент | — | S | [done 2026-07-10 feb9ca7]¹ |
| T-002 | Актуализация [OPEN]-решений (ресёрч) | 0 | T-001 | M | [done 2026-07-10 e557332] |
| T-003 | Конфигурация и секреты | 0 | T-001 | S | [done 2026-07-10 8e08623] |
| T-004 | Логирование и шина трейс-событий | 0 | T-003 | S | [done 2026-07-10 7b01fe8] |
| T-005 | Postgres, миграции v1, клиент БД | 0 | T-003 | M | [done 2026-07-10 b08fe25] |
| T-006 | CI-скелет (GitHub Actions) | 0 | T-001 | S | [done 2026-07-10 fd640c6] |
| T-007 | Доменные модели и интерфейс адаптера | 1. Срез | T-003 | S | [done 2026-07-10 ede571a] |
| T-008 | EDGAR: HTTP-клиент (rate-limit, кэш) | 1 | T-007 | M | [done 2026-07-10 bcc85b2] |
| T-009 | EDGAR: XBRL-факты и нормализация метрик | 1 | T-008 | L | [done 2026-07-10 5dee4c6]² |
| T-010 | EDGAR: нарративные разделы 10-K | 1 | T-008 | L | [done 2026-07-10 be533e2] |
| T-011 | Ingestion CLI (идемпотентный) | 1 | T-005, T-009, T-010 | M | [done 2026-07-10 e7d6112]³ |
| T-012 | Инструменты sql_query и schema_introspect | 1 | T-005 | M | [done 2026-07-10 3a8d661] |
| T-013 | Worker-агент ReAct (LangGraph) | 1 | T-004, T-012 | M | [done 2026-07-10 d095941] |
| T-014 | Chat API v0 (SSE) + запись ранов в БД | 1 | T-013 | M | [done 2026-07-10 e38bfa4] |
| T-015 | Compose v1, смоук-тест — **гейт G1** | 1 | T-006, T-011, T-014 | S | [done 2026-07-10 e7d6112]⁴ **G1 ✅** |
| T-016 | Model Router (tiered + fallback + стоимость) | 2. MVP | T-002, T-004 | L | [done 2026-07-10 1c3cc0e]⁵ |
| T-017 | Локальный инференс (Ollama) в контуре | 2 | T-016 | S | [done 2026-07-10]⁶ |
| T-018 | Qdrant, чанкинг, эмбеддинги в ingestion | 2 | T-002, T-011 | M | [done 2026-07-10]⁷ |
| T-019 | Инструмент rag_search (hybrid + rerank + цитаты) | 2 | T-018 | L | [done 2026-07-10]⁸ |
| T-020 | Оркестратор Plan-and-Execute | 2 | T-013, T-016 | L | [done 2026-07-10]⁹ |
| T-021 | A2A: воркер-сервер и клиент оркестратора | 2 | T-020 | M | [done 2026-07-10]¹⁰ |
| T-022 | Guardrail non-advice | 2 | T-016, T-020 | S | [done 2026-07-10]¹¹ |
| T-023 | AG-UI эндпоинт | 2 | T-020 | M | [done 2026-07-10]¹² |
| T-024 | Web UI (стрим плана, шагов, цитат) | 2 | T-023 | L | [ ] |
| T-025 | Наблюдаемый сценарий самокоррекции | 2 | T-020, T-019 | M | [ ] |
| T-026 | Compose v2 (полный MVP) — **гейт G2** | 2 | T-017, T-021…T-025 | S | [ ] |
| T-027 | MCP-серверы инструментов и MCP-клиенты | 3. Глубина | T-026 | M | [ ] |
| T-028 | Golden dataset | 3 | T-026 | M | [ ] |
| T-029 | Eval-харнесс (RAGAS/DeepEval/judge/SQL-check) | 3 | T-028 | L | [ ] |
| T-030 | Eval в CI (пороги, baseline, отчёты) | 3 | T-029 | M | [ ] |
| T-031 | Вторая нода (VPS-FI): удалённый воркер по A2A — **гейт G3** | 3 | T-027 | M | [ ] |
| T-032 | RU-адаптер: MOEX ISS (Q-02: в скоупе) | 3 | T-011 | L | [ ] |
| T-033 | Инструмент price_enrich (Q-10: в скоупе) | 3 | T-027 | S | [ ] |
| T-034 | Наблюдаемость: Grafana-дашборды | 4. Упаковка | T-026 | M | [ ] |
| T-035 | Слой B: n8n мониторинг 8-K → алерт (Telegram) | 4 | T-026 | M | [ ] |
| T-036 | Демо-режим и публичное ужесточение (CF Tunnel) | 4 | T-026 | M | [ ] |
| T-037 | Бенчмарки: инференс (CPU vs API) и Qdrant vs pgvector | 4 | T-018 | L | [ ] |
| T-038 | README и финализация документации | 4 | T-034…T-037 | S | [ ] |
| T-039 | Сайт-презентация | 4 | T-038 | M | [ ] |
| T-040 | Релиз v1.0: чистая машина, DoD — **гейт G4** | 4 | все | S | [ ] |

¹ T-001: репозиторий https://github.com/zzlawlzz/ledgerlens создан 2026-07-10 после gh auth (коммит 86cdbdb — LICENSE holder); Q-14 закрыт.
⁴ T-015: **гейт G1 пройден 2026-07-10** — чистый volume → compose up (авто-миграции) → demo-ingest 5 тикеров (67 filings, 2168 facts, 34 sections, 0 ошибок) → smoke 3/3; трейс ReAct в логах контейнера; pluggable-purity grep-тест. SEC-троттлинг обойдён прокси через VPS-FI (`deploy/edgar-proxy.md`).
⁵ T-016: live-критерий «route уходит в local-тир» финализируется в T-017 на железе с Ollama; на dev-машине доказан fallback local→cloud (падение локального тира прозрачно уводит в облако).
⁶ T-017: все критерии живьём: qwen3.5:4b запуллен идемпотентно; route→ollama (15с на тёплой модели); холодный старт (~3.5 мин CPU-загрузки) превышает 60с-таймаут тира → фолбэк в облако срабатывает штатно; лечение: warmup в ollama_pull.sh + OLLAMA_KEEP_ALIVE=-1. Интеграционный тест зелёный (host-probe скипается: порт ollama наружу не публикуется — проверка через контейнер).
⁷ T-018: живые критерии: 5 тикеров → 503 чанка, паритет postgres↔qdrant 503=503; повторный ingest идемпотентен (те же счётчики); пин модели работает (смена → ConfigError c подсказкой make reindex). ADR-6 поправлен: bge-m3 не поддержан fastembed 0.8 (вывод T-002 не подтвердился) → multilingual-e5-large + jina-reranker-v2; кэш моделей data/cache/fastembed примонтирован в app. Сетевые обходы: большие файлы HF — через hf-mirror или VPS-прокси (HTTPS_PROXY=http://127.0.0.1:18888), Qdrant/bm25 — только прямой HF.
⁸ T-019: живые критерии: поиск «supply chain risks» по AAPL — релевантные чанки с цитатами (3 года 10-K); порог 0.3 откалиброван (5 позитивных 0.46–0.78 vs 5 негативных 0.03–0.12, методика в config/rag.yaml); no_results честен (офф-корпус вопрос про Tesla → воркер не выдумал ни факта); сквозной API-прогон: воркер сам вызвал rag_search с фильтрами, ответ с цитатой на каждый пункт; cost_usd в run_finished теперь суммируется из llm_calls (фикс persistence).
⁹ T-020: критерии: (live) «Compare Apple and NVIDIA revenue growth…» → план из шагов по компаниям (SQL) + RAG, синтез с реальными числами (+65.5% NVDA vs +6.43% AAPL) — контейнерный прогон; (live) фоллоу-ап «And Microsoft's…» в той же сессии решён через Postgres-чекпоинтер; (unit, 9 тестов на фейках) no_data → ровно один replan с изменённым планом; исчерпание replans → partial-синтез; превышение cost-бюджета после шага 2 → partial без исключений; simple-вопрос → план из 1 шага без вызова планировщика. Отклонения/заметки: LLM-assess вызывается только для подтверждения детерминированного противоречия (экономия) ; правило «одна компания на SQL-шаг» в план-промпте; psycopg[binary]+psycopg-pool добавлены для чекпоинтера (Windows: тесты на SelectorEventLoop, conftest); dev wall-clock 600с (локальный тир на dev-CPU медленный); finalize_run агрегирует и токены из llm_calls; шаги плана пишутся в steps по строке на шаг.
¹⁰ T-021: критерии живьём: полный ран через воркер в отдельном контейнере (step_finished с worker_node=local; a2a_task_received в логах воркера; ответ $416.161B из БД); RPC без токена → 401 (live + contract-тест), карточка открыта (discovery); остановка контейнера worker → ран завершился честным partial за 2м42с без зависания (fail-fast на connect + replan-цикл); AgentCard — оба skills, JSONRPC-интерфейс (contract-тест). Реализация: полезная нагрузка — WorkerTask/WorkerResult JSON в Part.text/артефакте worker_result (протокол наш, транспорт A2A); грабли SDK 1.1: (а) первым событием очереди обязан быть полный Task (TaskUpdater.submit шлёт только статус — Task энкьюится вручную), (б) финальное состояние приходит в status_update, артефакты в artifact_update — клиент собирает всё из стрима; entrypoint контейнера теперь исполняет compose command (иначе воркер запускал API); WORKER_URL=${WORKER_URL:local} в workers.yaml — хост-тесты in-process, compose — A2A; порт 8081 наружу для dev-проверок.
¹¹ T-022: критерии: (unit, 32 теста) 18 advice-фраз RU/EN ловятся regex-ступенью, 11 легитимных аналитических проходят двухступенчатую проверку без ложных срабатываний; LLM-ступень ловит парафраз («seems wise»), при падении guard-LLM остаётся regex; (live) «Стоит ли покупать акции Apple?» → план переформулирован в анализ (classify/plan промпты v2), ответ без рекомендаций, TraceEvent guardrail в стриме; решение guardrail в steps (node='guardrail', output={triggered, action, spans}) — проверяется интеграционным тестом. Политика: advice → один ре-синтез с перечнем spans (вторая проверка — только regex, детерминированная) → шаблонный отказ с фактами из key_values. Дисклеймер (RU/EN по языку вопроса) добавляется к каждому ответу. Каноническая non-advice формулировка §12 вшита в промпт синтеза (v3).
¹² T-023: POST /agui (RunAgentInput → SSE AG-UI) c адаптером строго по таблице §10 (STATE_SNAPSHOT{plan,steps} / STATE_DELTA JSON Patch / TOOL_CALL_START+ARGS/END / CUSTOM(thought,guardrail,llm_call,run_summary) / TEXT_MESSAGE_* чанками по 160 символов); thread_id = сессия чекпоинтера; /api/chat остался debug-эндпоинтом. Контракт-тесты (6): записанный ран → энкодер протокола, RUN_STARTED первым/RUN_FINISHED последним, парность tool-call/text-message c общими id, RFC6902-форма дельт, сборка ответа из чанков. Live: полный ран через /agui — гистограмма событий полная (включая TOOL_CALL_* от A2A-воркера). Бонус-фикс: трейс удалённого воркера (result.trace) ре-публикуется оркестратором (relays_trace=False у A2AWorkerClient) — восстановлены llm_calls/tool_calls в БД (= честный учёт стоимости в бюджете) и полнота стрима. Критерий «референсный @ag-ui/client подключается» перенесён в T-024 (React UI и есть референсный клиент). Примечание: живой стриминг прогресса воркера ЧЕРЕЗ A2A (не постфактум) — кандидат в T-031.
³ T-011: живой критерий закрыт 2026-07-10 через EDGAR-прокси (SEC троттлил полосу домашнего IP >4 часов — зеркальный риск §5.6 подтверждён и замитигирован по плану: tinyproxy на VPS-FI, `deploy/edgar-proxy.md`).
² T-009: отклонение критерия «≥12 из 16 метрик» для JPM — у банков структурно отсутствуют 6 метрик (cost_of_revenue, gross_profit, operating_income, capex, rnd_expense; long_term_debt не под нашими тегами в свежих 10-K); честный максимум 10/16, зафиксирован тестом. AAPL — 16/16, эталонные значения сверены. Словарь §7 расширен тегом CashAndDueFromBanks (банки).

**Критический путь:** T-001→T-003→T-005→T-007→T-008→T-009→T-011→T-012→T-013→T-014→T-015 → T-016→T-018→T-019→T-020→T-023→T-024→T-026 → T-027→T-029→T-030→T-031 → T-036→T-038→T-040. Вне пути: T-032 и T-033 (обязательны решениями Q-02/Q-10, ведутся параллельно этапу 3; при срыве сроков режутся в порядке T-033 → часть T-037 → T-032 — последней, т.к. явно затребована владельцем), T-039 частично — параллельно.

---

## 2. Задачи. Этап 0 — Фундамент

### T-001 · Каркас репозитория и тулинг
**Зависимости:** — · **Оценка:** S · (Q-08 решён: имя **LedgerLens**, репо `ledgerlens`, приватный до G2, MIT)

**Цель:** пустой, но полностью оснащённый монорепозиторий, в котором каждая следующая задача добавляет только свой код.

**ТЗ:**
1. `git init`, ветка `main`; создать приватный репозиторий `ledgerlens` на GitHub владельца (имя занято → `ledger-lens`, зафиксировать фактическое в README); `.gitignore` (python, node, `.env`, `data/cache/`, `*.gguf`, дампы).
2. Структура каталогов строго по CONTRACTS.md §2 (пустые пакеты с `__init__.py`).
3. Корневой `pyproject.toml` (uv): python 3.12; пакеты-каталоги (`common`, `adapters`, `ingestion`, `tools`, `model_router`, `rag`, `workers`, `orchestrator`, `eval`); dev-группа: ruff, mypy, pytest, pytest-asyncio, respx, pre-commit.
4. Настроить ruff (line-length 100) и mypy; `pre-commit` с ruff-format/ruff/mypy.
5. `Makefile` с заглушками целей: `up down lint test ingest demo seed eval smoke`.
6. `.env.example` — стартовый набор переменных из CONTRACTS.md §5 с комментариями.
7. `LICENSE` — MIT (дефолт до ответа Q-08).

**Критерии выполнения:**
- [ ] `uv sync && uv run ruff check . && uv run mypy .` — 0 ошибок на пустом каркасе.
- [ ] `uv run pytest` находит и проходит placeholder-тест `tests/test_sanity.py`.
- [ ] `pre-commit run --all-files` зелёный; первый коммит `T-001: repo scaffold` создан.
- [ ] Структура каталогов совпадает с CONTRACTS.md §2 (сверка списком).

### T-002 · Актуализация [OPEN]-решений (ресёрч)
**Зависимости:** T-001 · **Оценка:** M

**Цель:** снять риск устаревших знаний: проверить веб-поиском фактическое состояние библиотек/моделей/цен на дату реализации и запинить версии. Дефолты уже стоят в CONTRACTS.md — задача подтверждает или заменяет их.

**ТЗ:** проверить и зафиксировать (каждый пункт: источник + дата проверки):
1. LangGraph: актуальная мажорная версия, API `create_react_agent`, Plan-and-Execute паттерн, PostgresSaver.
2. MCP python-SDK (FastMCP) + `langchain-mcp-adapters`: версии, транспорт streamable-HTTP.
3. `a2a-sdk` (python): статус, имя пакета, серверный/клиентский API, механизм AgentCard.
4. AG-UI: серверная python-библиотека, актуальный список событий протокола, `@ag-ui/client` — заполнить точные имена в CONTRACTS.md §10.
5. Локальные CPU-модели: актуальные MoE-кандидаты под Ollama (класс qwen3-30b-a3b и новее); железо известно (Q-06: 2×EPYC 7551, 128 ГБ RAM) — кандидаты до ~30B MoE включительно; выбрать дефолт + лёгкий fallback.
5-бис. **Доступность из РФ (домашняя нода):** фактически проверить с неё доступность `data.sec.gov`/`www.sec.gov` и `api.deepseek.com` (зеркальный риск §5.6 ARCHITECTURE); при блокировке — зафиксировать необходимость `EDGAR_PROXY_URL` через VPS-FI (поддержка — T-008) и/или прокси для LLM-API.
6. Эмбеддинги: bge-m3 vs более новые multilingual-модели; поддержка в fastembed (dense + sparse BM25 + reranker).
7. Cloud-провайдер (Q-01: DeepSeek): актуальные модели/цены deepseek-chat и deepseek-reasoner, лимиты API, поддержка structured output/tool-use → пины в router.yaml и `config/prices.yaml`; зафиксировать в заметке риск одно-провайдерности (fallback только на локальную модель).
8. RAGAS + DeepEval: актуальные API и совместимость.
9. qdrant-client: Query API для hybrid + RRF.
10. Провайдеры EOD-цен (Q-10: слой цен в скоупе, T-033) и их актуальные free-tier (Stooq / Alpha Vantage / прочие) — выбрать дефолт; проверить доступность провайдера с RU IP.

**Результат:** обновлённые CONTRACTS.md §1/§10/§11 (снятые `[verify:T-002]`), пины версий в `pyproject.toml`, обновлённые статусы ADR в IMPLEMENTATION_PLAN §3, короткая заметка `docs/research/adr-notes.md` с источниками.

**Критерии выполнения:**
- [ ] В CONTRACTS.md не осталось меток `[verify:T-002]`.
- [ ] `uv lock` успешен с запиненными версиями; `uv run python -c "import langgraph, mcp"` работает.
- [ ] ADR-3/6/7/8 в IMPLEMENTATION_PLAN §3 имеют конкретное решение или явную пометку «ждёт Q-xx».
- [ ] Заметка с источниками и датами проверки лежит в `docs/research/adr-notes.md`.

### T-003 · Конфигурация и секреты
**Зависимости:** T-001 · **Оценка:** S

**Цель:** единая точка конфигурации `common/config.py` — ни один модуль не читает env напрямую.

**ТЗ:**
1. pydantic-settings: класс `Settings` (env) + загрузчики YAML из `config/` с подстановкой `${VAR}`.
2. Завести файлы-дефолты: `config/app.yaml` (mode, список адаптеров, watchlist), `config/router.yaml` (из CONTRACTS §11), `config/prices.yaml`, `config/rag.yaml` (chunk_size=800 ток., overlap=150, top_k=5, rerank on/off), `config/budgets.yaml` (CONTRACTS §13), `config/workers.yaml` (реестр воркеров: name, url|local, skills).
3. Валидация на старте: отсутствие обязательных переменных → понятная ошибка со списком; наличие хотя бы одного LLM-провайдера.
4. Функция `get_settings()` с кэшем; переопределение для тестов.

**Критерии выполнения:**
- [ ] `uv run python -m common.config --validate` печатает итоговую конфигурацию (секреты маскированы) или список отсутствующих переменных.
- [ ] Unit-тесты: env-подстановка в YAML, ошибка при пустом наборе LLM-ключей, override в тестах.
- [ ] `.env.example` синхронизирован с полями `Settings` (тест-сверка полей).

### T-004 · Логирование и шина трейс-событий
**Зависимости:** T-003 · **Оценка:** S

**Цель:** JSON-логи и внутренняя шина `TraceEvent` (CONTRACTS §10) — фундамент наблюдаемости и AG-UI-стрима.

**ТЗ:**
1. `common/logging.py`: structlog JSON, поля `ts, level, event, run_id, step_id, node, service`; contextvars-биндинг `run_id/step_id`.
2. `common/tracing.py`: датакласс `TraceEvent` (схема CONTRACTS §10), `TraceBus` — async-паблишер с подписчиками (лог-подписчик; позже: SSE, БД).
3. Маскирование секретов в логах (процессор, вырезающий значения известных ключей).
4. `common/errors.py`: таксономия из CONTRACTS §13.

**Критерии выполнения:**
- [ ] Unit: событие, опубликованное в TraceBus, получают все подписчики в порядке seq; run_id из contextvars подставляется автоматически.
- [ ] Лог-строка — валидный JSON с обязательными полями; API-ключ в payload маскируется.
- [ ] Ошибки таксономии наследуются корректно, `ToolError.retryable` работает.

### T-005 · Postgres, миграции v1, клиент БД
**Зависимости:** T-003 · **Оценка:** M

**Цель:** поднятая БД со схемой CONTRACTS §6 и асинхронным доступом.

**ТЗ:**
1. `docker-compose.yml`: сервис `postgres` (образ с pgvector, например `pgvector/pgvector:pg16`), volume, healthcheck.
2. Alembic в `db/`: миграция 001 (домен: companies, filings, financial_facts + view latest_facts, filing_sections, section_chunks) и 002 (runs, steps, llm_calls, tool_calls, eval_runs, eval_results, monitored_events, prices) — DDL дословно из CONTRACTS §6.
3. Инициализация ролей: `app` (rw) и `app_ro` (SELECT на домен + latest_facts) — идемпотентный скрипт/миграция.
4. `common/db.py`: async engine (SQLAlchemy 2), session-фабрика, отдельный engine под `app_ro`.
5. Makefile: `make db-up db-migrate db-reset`.

**Критерии выполнения:**
- [ ] `make db-up db-migrate` на чистом volume создаёт все таблицы и view; повторный запуск — no-op.
- [ ] Тест: `app_ro` может SELECT из latest_facts и **не** может INSERT в financial_facts (ошибка прав).
- [ ] Тест идемпотентности ключей: повторная вставка компании/файлинга/факта с теми же натуральными ключами конфликтует по UNIQUE (ON CONFLICT работает).
- [ ] `alembic downgrade -1 && upgrade head` проходит.

### T-006 · CI-скелет (GitHub Actions)
**Зависимости:** T-001 · **Оценка:** S · (Q-08 решён: GitHub Actions в репо `ledgerlens`)

**Цель:** каждый коммит проверяется автоматически с первого дня.

**ТЗ:**
1. `.github/workflows/ci.yml`: триггер PR/push в main; jobs: lint (ruff+mypy), unit (pytest без меток slow, с сервисом postgres для тестов миграций), build (docker build всех имеющихся образов).
2. Кэш uv; матрица не нужна (только 3.12).
3. Заготовка `.github/workflows/eval.yml` (workflow_dispatch + schedule nightly, пока no-op с TODO на T-030).
4. Бейдж CI в README.

**Критерии выполнения:**
- [ ] CI зелёный на текущем каркасе; падение ruff/mypy/pytest валит PR.
- [ ] В CI job unit применяются alembic-миграции к сервис-контейнеру postgres (доказательство воспроизводимости схемы).
- [ ] Локально `make lint test` эквивалентен CI-джобам.

---

## 3. Задачи. Этап 1 — Вертикальный срез (гейт G1)

### T-007 · Доменные модели и интерфейс адаптера
**Зависимости:** T-003 · **Оценка:** S

**Цель:** зафиксировать кодом pluggable-ядро (§3.6 ARCHITECTURE): второй источник должен вставать без изменения ingestion.

**ТЗ:**
1. `common/models.py`: pydantic DTO `Company, Filing, FinancialFact, FilingSection, Event` — поля 1:1 с DDL (натуральные ключи, без БД-id).
2. `adapters/base.py`: абстрактный `DataSourceAdapter` дословно по CONTRACTS §8 + реестр адаптеров `get_adapter(source: str)` на основе конфига.
3. `common/metrics.py`: словарь канонических метрик v1 из CONTRACTS §7 (canonical, description, unit_hint, gaap_tags: list, rsbu_codes: list).
4. Контракт ошибок: методы адаптера бросают только `SourceUnavailableError` / `ToolError`.

**Критерии выполнения:**
- [ ] mypy-строгость: попытка реализовать адаптер без метода — ошибка типов; фейковый `DummyAdapter` в тестах реализует интерфейс и регистрируется через конфиг.
- [ ] Снапшот-тест словаря метрик (16 метрик v1, каноничные имена неизменны).
- [ ] Контракт-тест: DTO сериализуются в JSON и обратно без потерь.

### T-008 · EDGAR: HTTP-клиент (rate-limit, кэш, ретраи)
**Зависимости:** T-007 · **Оценка:** M

**Цель:** вежливый и быстрый доступ к SEC (§5.1 ARCHITECTURE): не более ~10 req/s по правилам SEC, у нас — пауза 100 мс, всё кэшируется.

**ТЗ:**
1. `adapters/edgar/client.py`: httpx.AsyncClient с обязательным `User-Agent` из `EDGAR_USER_AGENT` (старт без него — ошибка конфигурации).
2. Глобальный rate-limiter (asyncio, min-интервал 100 мс между запросами к `*.sec.gov`).
3. Дисковый кэш `data/cache/edgar/` (ключ — URL-хэш; TTL: бессрочно для Archives-документов и companyfacts закрытых периодов, 1 час для submissions). Формат: json/бинарь + метафайл.
4. Ретраи: tenacity, 3 попытки на 5xx/сетевые, экспоненциальный backoff; 403/404 — не ретраить.
5. Методы: `get_company_tickers()` (www.sec.gov/files/company_tickers.json), `get_submissions(cik)` (data.sec.gov/submissions/CIK{cik:010d}.json), `get_companyfacts(cik)` (data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json), `get_archive_document(cik, accession, filename)`.
6. Опциональный egress-прокси `EDGAR_PROXY_URL` (httpx proxies) — применяется **только** к запросам на `*.sec.gov` (митигация недоступности sec.gov с RU IP, §5.6 ARCHITECTURE); при заданном прокси и его падении — ошибка с внятной диагностикой (не тихий фолбэк на прямое соединение).

**Критерии выполнения:**
- [ ] Unit на respx-фикстурах: rate-limiter выдерживает интервал (проверка временных меток), кэш-хит не ходит в сеть, ретраи на 500 работают, 404 пробрасывается сразу.
- [ ] Записаны реальные фикстуры (сокращённые) для 2 компаний в `tests/fixtures/edgar/`.
- [ ] Запуск без `EDGAR_USER_AGENT` падает с понятной ошибкой.
- [ ] Тест прокси: при заданном `EDGAR_PROXY_URL` запросы к sec.gov идут через него (respx/мок-прокси), запросы к другим хостам — напрямую.

### T-009 · EDGAR: XBRL-факты и нормализация метрик
**Зависимости:** T-008 · **Оценка:** L

**Цель:** из `companyfacts` получить чистые `FinancialFact` в канонических метриках — фундамент SQL-аналитики.

**ТЗ:**
1. `adapters/edgar/facts.py`: парсер companyfacts → для каждой метрики словаря идти по цепочке тегов (us-gaap + dei), брать первый тег с данными.
2. Отбор значений: юниты USD / USD-per-share / shares; годовые: `fp=FY` + form 10-K (полный год — по `start`/`end` ≈ 12 мес.); квартальные: `fp∈{Q1,Q2,Q3}` + form 10-Q. Q4 не дорисовывать (v1).
3. Дедупликация: на (metric, period_end, fiscal_period) может прийти несколько записей (рестейтменты в поздних filings) — сохранять каждую с её accession (`source_filing_id`), выбор «последней версии» делает view `latest_facts`.
4. Валидация: value — число; period_end в диапазоне 2000–2035; unit нормализован; подозрительные аномалии (выручка < 0) — warning, но сохраняются.
5. `extract_facts()` адаптера собирает Filing-записи (из submissions: 10-K/10-Q с accession, filed_at, period, source_url на Archives) + факты, связанные с filing по accession.
6. Статистика прогона: сколько метрик найдено/не найдено по каждому тикеру (structlog) — материал для расширения словаря.

**Критерии выполнения:**
- [ ] На фикстурах Apple и JPMorgan (разные отрасли — разный набор тегов): извлекаются ≥ 12 из 16 метрик за 3 последних FY, значения совпадают с ручной сверкой по фикстуре (тест с эталонными числами).
- [ ] Рестейтмент-кейс: два filings с разными значениями одной метрики → в `latest_facts` побеждает поздний filed_at (интеграционный тест с БД).
- [ ] Пропущенный тег у компании не валит извлечение остальных метрик.
- [ ] Юнит-эджкейсы: shares (unit='shares'), EPS (unit='USD/share') маппятся корректно.

### T-010 · EDGAR: нарративные разделы 10-K
**Зависимости:** T-008 · **Оценка:** L

**Цель:** извлечь Item 1A (Risk Factors) и Item 7 (MD&A) из HTML 10-K — сырьё для RAG.

**ТЗ:**
1. `adapters/edgar/sections.py`: по submissions выбрать N последних 10-K, скачать primaryDocument (Archives), распарсить (bs4+lxml).
2. Очистка: script/style/ix-теги прочь; таблицы линеаризуются в текст (ячейки через « | »); нормализация пробелов и `&nbsp;`.
3. Поиск границ разделов: регекс-семейство по заголовкам (`Item\s*1A\.?\s*Risk Factors`, `Item\s*7\.?\s*Management'?s Discussion`, вариации регистра/пунктуации); текст раздела — от заголовка до следующего `Item X`-заголовка того же уровня.
4. Ловушка «оглавление»: первое вхождение может быть в TOC — брать вхождение с максимальным объёмом текста после него / пропускать совпадения, за которыми < 2000 символов.
5. Санити: длина раздела 2k–1.5M символов, иначе warning + пропуск раздела (не падение); результат → `FilingSection(section='risk_factors'|'mdna', title, text)`.
6. Опциональный флаг также извлекать `business` (Item 1) — выключен по умолчанию.

**Критерии выполнения:**
- [ ] На записанных 10-K трёх разных эмитентов (разные генераторы EDGAR-HTML) извлекаются оба раздела; начало/конец совпадают с ручными маркерами фикстур.
- [ ] TOC-ловушка покрыта тестом (документ, где Item 1A встречается в оглавлении).
- [ ] Некорректный/битый HTML одного filing не прерывает обработку остальных (warning + skip).

### T-011 · Ingestion CLI (идемпотентный)
**Зависимости:** T-005, T-009, T-010 · **Оценка:** M

**Цель:** одна команда наполняет БД реальными данными и безопасно перезапускается.

**ТЗ:**
1. `ingestion/run.py`: CLI (argparse/typer): `uv run python -m ingestion.run --source edgar --tickers AAPL,MSFT,... --years 3 [--skip-narrative]`.
2. Пайплайн: adapter.list_entities → upsert companies → fetch_filings → upsert filings → extract_facts → upsert facts → extract_sections → upsert sections. Все upsert — `ON CONFLICT ... DO UPDATE/NOTHING` по натуральным ключам (CONTRACTS §6).
3. Пер-тикерная изоляция ошибок: падение одного тикера (SourceUnavailable) → лог, продолжение остальных; итоговый exit code 1, если упало всё.
4. Отчёт в конце: таблица тикер × {filings, facts, sections, ошибки}; запись сводки в лог.
5. `make ingest TICKERS=... YEARS=3`; дефолтный watchlist в `config/app.yaml` — AAPL, MSFT, NVDA, GOOGL, AMZN, META, TSLA, JPM, JNJ, XOM (Q-09: владелец не финализировал — дефолт действует, замена дёшева до T-028).
6. Прогресс — простыми лог-строками (без TUI).

**Критерии выполнения:**
- [ ] Живой прогон на 3 тикерах × 3 года заполняет companies/filings/financial_facts/filing_sections; в БД для AAPL revenue за 3 FY совпадает с EDGAR (ручная сверка, зафиксировать числа в тесте-марке `slow`).
- [ ] Повторный запуск той же команды не добавляет ни одной строки (тест: счётчики строк до/после равны).
- [ ] Сбой сети посреди прогона (эмуляция) → команда завершает остальные тикеры и печатает отчёт об ошибке.

### T-012 · Инструменты sql_query и schema_introspect
**Зависимости:** T-005 · **Оценка:** M

**Цель:** безопасный SQL-доступ для агента с ошибками, спроектированными под самокоррекцию.

**ТЗ:**
1. `tools/sql/core.py`: реализация контрактов CONTRACTS §9 как чистых функций-библиотек (MCP-обёртка — T-027).
2. Защита: соединение только под `app_ro`; sqlglot: parse ок, единственный statement, тип SELECT/WITH (CTE), запрещены `INTO`/локи; инъекция `LIMIT row_limit`, если нет; `statement_timeout=10s`.
3. Формат ошибки — «обучающий»: `{error, hint, schema_excerpt}`; для ошибки неизвестной колонки/метрики hint содержит ближайшие по расстоянию имена и совет вызвать `schema_introspect`; для пустого результата по `metric='X'` — список доступных метрик.
4. `schema_introspect`: собирает описание из information_schema + `common/metrics.py` + 3 примерных SQL (по latest_facts); кэш на процесс.
5. Результат > row_limit → `truncated=true`; ячейки длиннее 500 симв. обрезаются.

**Критерии выполнения:**
- [ ] Тесты защиты: `UPDATE`, `DROP`, `SELECT ...; DELETE`, `COPY` — отклонены до исполнения; SELECT под ролью app_ro на запись невозможен (двойной барьер).
- [ ] Тест «обучающей ошибки»: запрос с `metric='profit'` возвращает hint со списком канонических метрик.
- [ ] `schema_introspect` отдаёт все 16 метрик и описание `latest_facts`; примеры SQL исполняются без ошибок.
- [ ] Таймаут срабатывает (тест с pg_sleep) и возвращается как observation, не исключение.

### T-013 · Worker-агент ReAct (LangGraph)
**Зависимости:** T-004, T-012 · **Оценка:** M

**Цель:** первый агент: ReAct-цикл над sql-инструментами с трейсом и бюджетом.

**ТЗ:**
1. `workers/react_worker.py`: LangGraph ReAct-агент (prebuilt `create_react_agent` или явный граф) с инструментами sql_query, schema_introspect (позже rag_search добавится конфигом `allowed_tools`).
2. Промпт `prompts/worker_react.md`: роль аналитика, обязательные блоки CONTRACTS §12, инструкция при ошибке инструмента — прочитать hint, поправить запрос, не сдаваться до лимита итераций; при отсутствии данных — статус `no_data`.
3. Вход/выход строго `WorkerTask`/`WorkerResult` (CONTRACTS §10); лимиты из `budget` задачи (max_iterations → recursion limit, deadline — asyncio.timeout).
4. Каждый шаг публикует TraceEvent (agent_thought — сокращённая мысль ≤ 300 симв., tool_call_started/finished, llm_call).
5. LLM: на этом этапе — временно один cloud-провайдер напрямую через тонкую заглушку `model_router.simple_client()` (полный роутер — T-016; интерфейс вызова уже роутерный: `chat(task_class='reason', ...)`).
6. Учёт usage: токены из ответов провайдера суммируются в WorkerResult.usage.

**Критерии выполнения:**
- [ ] Интеграционный тест (live-LLM, метка `slow`): «Какая выручка была у Apple в последнем финансовом году?» → ответ содержит число, совпадающее с БД, трейс содержит ≥ 1 tool_call sql_query.
- [ ] Тест самокоррекции на уровне воркера (фейковый LLM по сценарию): первый SQL с ошибкой → в следующей итерации вызван schema_introspect → исправленный SQL → succeeded.
- [ ] Превышение max_iterations → `budget_exceeded`, частичный ответ, без исключений.
- [ ] Unit: WorkerResult сериализуется по схеме; usage ненулевой.

### T-014 · Chat API v0 (SSE) + запись ранов в БД
**Зависимости:** T-013 · **Оценка:** M

**Цель:** голый HTTP-вход: вопрос → стрим шагов → ответ; каждый ран фиксируется в БД.

**ТЗ:**
1. `orchestrator/api.py`: FastAPI; `POST /api/chat` (body: `{question, mode?}`) → SSE-стрим TraceEvent-ов как JSON-строк (v0, без AG-UI-обёртки) + финальное `run_finished` с ответом; `GET /healthz` (проверка БД).
2. Пока без P&E: единственный «план» из одного шага — вызов ReAct-воркера напрямую (через интерфейс `WorkerClient` → `LocalWorkerClient`, чтобы T-020/T-021 подменяли реализацию без правки API).
3. Подписчик TraceBus → БД: runs (создание при старте, финализация со статусом/стоимостью/латентностью), steps, llm_calls, tool_calls.
4. Ошибка внутри рана → SSE `run_error` + статус failed в runs, HTTP-соединение закрывается корректно.

**Критерии выполнения:**
- [ ] `curl -N POST /api/chat` показывает живой поток событий и финальный ответ.
- [ ] После рана в runs/steps/llm_calls/tool_calls есть согласованные записи (run_id совпадает, стоимость > 0, латентность заполнена) — интеграционный тест.
- [ ] Обрыв клиента посреди стрима не оставляет ран в статусе running навсегда (финализация в фоне).
- [ ] `/healthz` возвращает 200 при живой БД и 503 без неё.

### T-015 · Compose v1, смоук-тест — гейт G1
**Зависимости:** T-006, T-011, T-014 · **Оценка:** S

**Цель:** фаза 1 по IMPLEMENTATION_PLAN §2 закрыта: `docker compose up` → вопрос → ответ на реальных данных.

**ТЗ:**
1. Dockerfile приложения (multi-stage, uv, non-root user); сервис `app` в compose (api+worker в одном контейнере на этом этапе), зависимость от postgres healthcheck; авто-применение миграций на старте (entrypoint).
2. `make demo-ingest`: ingest 5 тикеров × 3 года (с кэшем EDGAR).
3. `scripts/smoke_test.py`: поднимает 3 канонических вопроса (выручка одной компании; сравнение двух; динамика за 3 года) через /api/chat, проверяет: статус succeeded, в ответе есть числа, трейс содержит sql_query.
4. `make smoke` = compose up → ingest (если пусто) → smoke_test; добавить в CI как manual job (metка slow).
5. Обновить README: рабочий Quick Start фазы 1.

**Критерии выполнения (= критерии гейта G1):**
- [ ] На чистом volume: `cp .env.example .env` (+ключи) → `docker compose up -d` → `make demo-ingest && make smoke` — всё зелёное одной сессией.
- [ ] Реальный вопрос по загруженным компаниям возвращает корректный ответ на фактических данных (сверка чисел с БД в smoke).
- [ ] Трейс шагов ReAct виден в логах контейнера (structlog JSON).
- [ ] В коде нет импортов `adapters.edgar` вне реестра адаптеров (pluggable-чистота, проверка grep в тесте).

---

## 4. Задачи. Этап 2 — MVP (гейт G2)

### T-016 · Model Router (tiered + fallback + стоимость)
**Зависимости:** T-002, T-004 · **Оценка:** L · (Q-01 решён: единственный облачный провайдер — DeepSeek; cheap=deepseek-chat, strong/judge=deepseek-reasoner; резервный tier — только локальная модель)

**Цель:** все LLM-вызовы системы идут через провайдер-агностичный роутер (§3.4 ARCHITECTURE): дешёвое — локально, тяжёлое — в API, с fallback и учётом стоимости.

**ТЗ:**
1. `model_router/`: `RouterClient.chat(task_class, messages, tools=None, response_format=None, stream=False) -> RouterResponse{text|tool_calls, usage, provider, model, fallback_used}`.
2. Провайдер-адаптеры поверх LangChain chat-моделей (anthropic, openai-совместимые: DeepSeek/OpenAI/Ollama, gemini) — конструирование из `config/router.yaml`; тиры без ключа в env исключаются на старте с warning.
3. Fallback-семантика по CONTRACTS §11: таймаут/5xx/429/connection → следующий tier; ретраи tenacity внутри tier (2 попытки); все исходы → TraceEvent `llm_call` + запись в llm_calls (стоимость из prices.yaml; неизвестная модель → cost NULL + warning).
4. Обёртка `RouterChatModel(task_class)` — LangChain-совместимая модель для LangGraph-агентов (bind_tools поддержан), внутри делегирует RouterClient.
5. Замена заглушки T-013: воркер получает `RouterChatModel('reason')`.
6. Structured output: json-схема через response_format с валидацией pydantic + один repair-retry.

**Критерии выполнения:**
- [ ] Unit с фейковыми провайдерами: первый tier падает по таймауту → ответ со второго, `fallback_used=true`, оба вызова в llm_calls.
- [ ] Тир с отсутствующим ключом не участвует (тест конфигурации из env).
- [ ] Интеграционно (live, slow): `route`-вызов уходит в local-тир (лог provider=ollama), `synthesize` — в cloud_strong; в llm_calls корректные token counts и cost > 0 для облака.
- [ ] Воркер T-013 работает через роутер без изменения своих тестов.

### T-017 · Локальный инференс (Ollama) в контуре
**Зависимости:** T-016 · **Оценка:** S

**Цель:** локальная CPU-модель реально обслуживает дешёвые классы задач (ADR-3).

**ТЗ:**
1. Сервис `ollama` в compose: официальный образ, volume моделей, healthcheck (`/api/tags`); entrypoint-скрипт `scripts/ollama_pull.sh` — идемпотентный pull модели из `LOCAL_MODEL`.
2. Дефолт модели по T-002 (Q-06: 128 ГБ RAM — MoE-класс ~30B проходит); документировать выбор и требования в README-секции.
3. Прогрев: после старта — тестовый вызов, время ответа в лог (SLA-ориентир: короткая classify ≤ 10 c на целевом железе — иначе warning в лог, роутер и так уйдёт в fallback по таймауту).
4. Профиль compose `no-local` (для машин без ресурсов): тир local выключен, политика деградирует в облако — задокументировать.

**Критерии выполнения:**
- [ ] `docker compose up` на чистой машине докачивает модель и `route`-вызовы обслуживаются локально (llm_calls: provider=ollama).
- [ ] Остановка ollama-контейнера посреди работы → система продолжает отвечать (fallback в cloud_cheap, тест).
- [ ] Профиль `no-local` поднимается и работает без ollama.

### T-018 · Qdrant, чанкинг, эмбеддинги в ingestion
**Зависимости:** T-002, T-011 · **Оценка:** M

**Цель:** нарратив из filing_sections превращается в поисковый индекс (ADR-2/6).

**ТЗ:**
1. Сервис `qdrant` в compose (volume, healthcheck). Коллекция `narrative_chunks`: dense (размерность по ADR-6) + sparse (BM25) векторы; payload: company_id, ticker, filing_id, form_type, period_end, section, source_url, chunk_index, text.
2. `rag/chunking.py`: разбиение по абзацам с целевым размером из `config/rag.yaml` (800 токенов, overlap 150), без разрыва предложений; токенизатор — от embedding-модели.
3. `rag/embedding.py`: fastembed (dense bge-m3 + sparse bm25) `[verify:T-002 снят]`, батчами, CPU.
4. Ingestion-шаг `--embed`: чанки → section_chunks (текст; embedding-колонка заполняется отдельным флагом `--pgvector` — для бенчмарка T-037) → upsert в Qdrant (id = hash(section_id, chunk_index) — идемпотентно).
5. Версионирование: имя embedding-модели в метаданных коллекции; несовпадение при старте → ошибка с инструкцией на реиндекс `make reindex`.

**Критерии выполнения:**
- [ ] После `make ingest` c embed: количество точек в Qdrant = count(section_chunks); повторный прогон не меняет количество.
- [ ] Unit чанкера: перекрытие соблюдено, предложения не рвутся, пустые/короткие абзацы слиты.
- [ ] Поиск «supply chain risks» по AAPL-фикстуре возвращает чанки из risk_factors с корректным payload (интеграционный тест).
- [ ] Смена модели в конфиге без реиндекса → старт падает с понятной ошибкой.

### T-019 · Инструмент rag_search (hybrid + rerank + цитаты)
**Зависимости:** T-018 · **Оценка:** L

**Цель:** качественный поиск по нарративу с обязательными цитатами и честным «нет данных» (§3.5 ARCHITECTURE).

**ТЗ:**
1. `tools/rag/core.py` по контракту CONTRACTS §9: dense+sparse запрос → серверный fusion (RRF, Qdrant Query API) → top-20 → reranker (CPU cross-encoder) → top-k.
2. Фильтры → Qdrant filter (tickers, form_types, sections, period range); rerank выключаем конфигом (rag.yaml) — для сравнения в eval.
3. Citation-объект собирается из payload; `snippet` — первые 300 симв. чанка.
4. Порог релевантности: если после rerank лучший score < порога (rag.yaml) → `no_results=true` (антигаллюцинация); порог откалибровать на 5 позитивных/5 негативных запросах вручную, значение и методику зафиксировать в комментарии конфига.
5. Подключить к воркеру: `allowed_tools` расширяется `rag_search`; промпт воркера дополняется правилом «нарративные утверждения — только с citations из результатов rag_search».
6. Латентность вызова (embed+search+rerank) — в tool_calls; ориентир p95 ≤ 3 c на демо-корпусе.

**Критерии выполнения:**
- [ ] Интеграционно: «Какие основные риски называет Apple?» → воркер отвечает с ≥ 2 цитатами (source_url ведёт на реальный 10-K), текст ответа опирается на чанки (ручная проверка + фиксация в тесте по chunk_id).
- [ ] Запрос про компанию, которой нет в индексе → `no_results=true` → ответ воркера «в загруженных данных нет» (тест).
- [ ] Тест фильтров: sections=['risk_factors'] не возвращает mdna-чанки.
- [ ] Hybrid лучше dense-only хотя бы на точных терминах: запрос с тикером/термином («Item 1A litigation») находит нужный чанк в top-5 при hybrid и фиксируется сравнительный мини-тест.

### T-020 · Оркестратор Plan-and-Execute
**Зависимости:** T-013, T-016 · **Оценка:** L

**Цель:** ядро агентности (§3.1 ARCHITECTURE): явный план, исполнение через воркеров, репланирование, бюджет, синтез с цитатами.

**ТЗ:**
1. `orchestrator/graph.py`: LangGraph StateGraph. State: `{question, mode, plan: [PlanStep{id, goal, needs:[step_ids], status, result_summary}], results: {step_id: WorkerResult}, budget_used, replans, answer, key_values, citations}`.
2. Узлы: `classify` (task_class=route: простой фактовый вопрос → короткий план из 1 шага; аналитический → полный планинг), `plan` (task_class=plan, structured output список шагов ≤ max_plan_steps), `execute` (последовательный цикл: следующий pending-шаг → WorkerClient.run(WorkerTask из goal+контекст предыдущих результатов)), `assess` (после каждого шага: failed/no_data/противоречие → `replan` с передачей причины, лимит max_replans; иначе продолжение), `synthesize` (task_class=synthesize: текст + key_values + citations по CONTRACTS §10), `guardrail` (T-022), `finalize`.
3. Правило противоречия v1 (детерминированное): числовые key_values разных шагов об одной величине расходятся > 2% → replan шага-источника; плюс LLM-assess как второй сигнал.
4. Бюджет: перед каждым LLM/worker-вызовом проверка счётчиков (стоимость/токены/шаги/время из budgets.yaml); превышение → переход к synthesize с флагом partial (ответ явно помечается «анализ не завершён полностью»).
5. Чекпоинтер PostgresSaver (thread_id = session_id из API) — фоллоу-ап вопросы видят контекст сессии.
6. `WorkerClient` уже интерфейс (T-014); dispatch: выбор воркера по skills из `config/workers.yaml` (пока один local).
7. API `/api/chat` переключается на оркестратор; TraceEvents plan_created/plan_updated/step_* публикуются.

**Критерии выполнения:**
- [ ] Многошаговый вопрос («Сравни динамику выручки Apple и Microsoft за 3 года и назови главные риски Apple») → план ≥ 3 шагов (2×SQL, 1×RAG) → синтез с числами и цитатами; всё видно в SSE-трейсе (интеграционный live-тест).
- [ ] Unit на фейковых воркерах/LLM: шаг возвращает no_data → происходит ровно один replan с изменённым планом; после max_replans — partial-синтез.
- [ ] Unit: бюджет max_cost исчерпан после шага 2 → synthesize вызван с partial, статус рана budget_exceeded, исключений нет.
- [ ] Фоллоу-ап «А какая из них росла быстрее?» в той же сессии использует контекст (checkpointer) — live-тест.
- [ ] Простой вопрос («выручка Apple в 2024») не строит многошаговый план (classify-ветка, план из 1 шага) — контроль стоимости.

### T-021 · A2A: воркер-сервер и клиент оркестратора
**Зависимости:** T-020 · **Оценка:** M

**Цель:** межагентное взаимодействие по стандарту (§6 ARCHITECTURE) — локально сейчас, вторая нода в T-031 без изменений кода.

**ТЗ:**
1. `workers/a2a_server.py`: воркер как A2A-сервер (a2a-sdk): AgentCard (name, skills: financial_sql_analysis, narrative_rag_analysis; capabilities), обработчик принимает WorkerTask-payload, стримит прогресс (если SDK поддерживает update-события — транслировать TraceEvents), возвращает WorkerResult.
2. Авторизация: bearer `A2A_TOKEN` (middleware; отсутствие/несовпадение → 401).
3. `orchestrator/worker_client.py`: `A2AWorkerClient` (fetch AgentCard при старте, healthcheck, отправка задачи, таймаут = deadline задачи + 10 c).
4. Compose: воркер выделяется в отдельный сервис `worker` (свой контейнер), оркестратор ходит к нему только по A2A (LocalWorkerClient остаётся для тестов/fallback).
5. `config/workers.yaml`: `[{name: worker-local, url: http://worker:8081, skills: [...]}]`; диспетчер T-020 выбирает по skills, при недоступности воркера — ToolError retryable → assess/replan.
6. TraceEvent step_started получает `worker_node` из ответа воркера (`WORKER_NODE_NAME`).

**Критерии выполнения:**
- [ ] Полный ран проходит с воркером в отдельном контейнере через A2A (в трейсе worker_node=local, транспорт A2A виден в логах).
- [ ] Запрос без токена к воркеру → 401 (тест).
- [ ] Остановка контейнера worker → оркестратор возвращает осмысленную ошибку рана (или partial), не зависает (таймаут-тест).
- [ ] AgentCard воркера валиден по схеме SDK и содержит оба skills (контракт-тест).

### T-022 · Guardrail non-advice
**Зависимости:** T-016, T-020 · **Оценка:** S

**Цель:** архитектурное требование §1.3: итоговый ответ никогда не содержит инвестрекомендаций.

**ТЗ:**
1. `orchestrator/guardrail.py`: двухступенчатая проверка по CONTRACTS §12 — regex-паттерны (RU+EN, файл `config/guardrail_patterns.yaml`) + LLM-классификатор (task_class=guard, structured `{advice: bool, spans}`).
2. Политика: advice=true → один ре-синтез с явным запретом и списком spans; повторно true → шаблонный отказ с аналитической переформулировкой вопроса.
3. Вопросы-провокации («что мне купить?») перехватываются и на этапе classify: план строится про анализ, не про совет (инструкция в промпте планировщика), guardrail остаётся последним рубежом.
4. TraceEvent `guardrail` (triggered, action) + лог; дисклеймер-строка добавляется к каждому ответу (UI рендерит отдельно — T-024).
5. Набор тест-фраз: ≥ 15 advice-примеров (RU/EN, разные формулировки) и ≥ 10 легитимных аналитических (не должны срабатывать ложно) — станет частью golden (T-028).

**Критерии выполнения:**
- [ ] Все 15 advice-фраз ловятся (unit: regex или фейковый guard-LLM); 10 легитимных проходят без срабатывания.
- [ ] Live: «Стоит ли покупать акции Apple?» → ответ без рекомендации, с аналитической переформулировкой; TraceEvent guardrail в стриме.
- [ ] Guardrail-решение видно в runs/steps (поле в output синтез-шага).

### T-023 · AG-UI эндпоинт
**Зависимости:** T-020 · **Оценка:** M

**Цель:** стандартный протокол стрима для фронта (§6 ARCHITECTURE) вместо самодельного SSE.

**ТЗ:**
1. `orchestrator/agui.py`: `POST /agui` по спецификации протокола (RunAgentInput: thread_id, run_id, messages, state) → SSE потока AG-UI-событий.
2. Адаптер TraceEvent→AG-UI строго по таблице CONTRACTS §10 (имена событий — по версии, запиненной в T-002): план и статусы шагов — STATE_SNAPSHOT/STATE_DELTA (JSON Patch), мысли/инструменты — TOOL_CALL_*/CUSTOM, финальный ответ — TEXT_MESSAGE_* токен-стримом из synthesize.
3. Session: thread_id ↔ checkpointer-сессия T-020 (фоллоу-апы работают).
4. Старый `/api/chat` остаётся как debug-эндпоинт (curl-friendly).
5. Контракт-тест: записанный ран → последовательность событий валидируется схемой протокола (порядок: RUN_STARTED → … → RUN_FINISHED, парность START/END).

**Критерии выполнения:**
- [ ] Референсный клиент AG-UI (`@ag-ui/client` в мини-скрипте или dojo) подключается и получает события без ошибок парсинга.
- [ ] Полный ран: в потоке есть снапшот плана, дельты статусов шагов, tool-call-события, токен-стрим ответа, RUN_FINISHED (контракт-тест на записи).
- [ ] Ошибка рана транслируется как RUN_ERROR, соединение закрывается корректно.

### T-024 · Web UI (стрим плана, шагов, цитат)
**Зависимости:** T-023 · **Оценка:** L · (Q-04 решён: двуязычный интерфейс EN/RU)

**Цель:** лицо продукта: незнакомец видит план, живые шаги агентов, ответ с цитатами.

**ТЗ:**
1. `web/`: Vite + React + TS strict; подключение через `@ag-ui/client` к `/agui`; pnpm; ESLint+prettier.
2. Экран: (а) чат-колонка: вопрос, токен-стрим ответа, key_values-блок (таблица ключевых цифр), цитаты — карточки с ticker/form/period/section и ссылкой на первоисточник; (б) панель «ход анализа»: план с живыми статусами шагов (pending/running/done/failed/replanned), разворачиваемые tool-calls (аргументы/превью результата), мысли агента (свёрнуты по умолчанию), бейдж worker_node у шага; (в) шапка: дисклеймер non-advice (постоянный), индикатор бюджета рана (потрачено $ / лимит).
3. Примеры вопросов (4–6 кнопок, покрывают SQL/RAG/многошаговый/самокоррекцию) — из `config/app.yaml` через `/api/examples`.
4. Состояния: пустое/ждём план/ошибка (человекочитаемо)/rate-limited; мобильная вёрстка — читабельно, без интерактива панели шагов.
5. Сборка: multi-stage Dockerfile → nginx static + proxy `/api`,`/agui` на orchestrator; сервис `web` в compose.
6. Без бэкенд-авторизации; session_id — в localStorage (фоллоу-апы).
7. i18n (Q-04): react-i18next или лёгкий словарь локалей; переключатель EN/RU в шапке, дефолт EN с автоопределением по браузеру; локализуются все статические строки UI, дисклеймер и примеры вопросов (в `config/app.yaml` — обе локали); язык ответа агента определяется языком вопроса (уже в промптах, CONTRACTS §12) — UI его не переводит.

**Критерии выполнения:**
- [ ] Живой сценарий в браузере: многошаговый вопрос → виден план, шаги перещёлкиваются в реальном времени, ответ стримится, цитаты кликабельны и ведут на sec.gov (ручная проверка + Playwright-смоук: селекторы плана/шагов/цитат появляются).
- [ ] Replan отображается (шаг помечен replanned, план обновился) — на сценарии T-025.
- [ ] Дисклеймер виден всегда; при guardrail-срабатывании ответ показывается с пометкой.
- [ ] `docker compose up` отдаёт UI на `http://localhost:3000` (или задокументированный порт) без dev-сервера.
- [ ] Переключатель EN/RU меняет все статические строки без перезагрузки; ни одной захардкоженной строки вне словаря (lint-проверка или grep-тест).

### T-025 · Наблюдаемый сценарий самокоррекции
**Зависимости:** T-020, T-019 · **Оценка:** M

**Цель:** дифференциатор проекта (§1.2 ARCHITECTURE) воспроизводимо демонстрируется, а не «иногда случается».

**ТЗ:**
1. Зафиксировать два штатных механизма самокоррекции (не искусственные хаки): (а) **уровень воркера** — ошибка sql_query с обучающим hint → schema_introspect → исправленный запрос (надёжный триггер: вопрос с бытовым термином «прибыль»/«доход», которого нет среди канонических имён — модель почти всегда сперва пробует profit/income-подобную колонку или метрику); (б) **уровень оркестратора** — шаг вернул no_data (вопрос частично вне загруженного набора) → replan: сузить период/компании и честно пометить пропуск в ответе.
2. `demo/self_correction.md`: 2–3 конкретных вопроса-триггера с ожидаемым поведением, скрипт показа для демо.
3. Автотест (live, slow): вопрос-триггер (а) → трейс содержит паттерн `tool_call(sql_query, error) → tool_call(schema_introspect) → tool_call(sql_query, ok)` и финальный корректный ответ; допускается ≤ 1 ретрай теста (LLM-недетерминизм), устойчивость подтверждается 5 прогонами локально (зафиксировать результат в PR).
4. UI: убедиться, что цепочка коррекции читабельна в панели шагов (ошибка подсвечена, коррекция следом) — при необходимости мелкие правки T-024.
5. Кнопка-пример в UI ведёт на вопрос-триггер.

**Критерии выполнения:**
- [ ] Live-тест сценария (а) зелёный; последовательность коррекции присутствует в трейсе.
- [ ] Сценарий (б): вопрос с недоступной компанией → replan-событие в трейсе, ответ явно указывает границы данных.
- [ ] В UI обе цепочки визуально различимы (скриншоты в PR).
- [ ] `demo/self_correction.md` написан и воспроизводим по шагам.

### T-026 · Compose v2 (полный MVP) — гейт G2
**Зависимости:** T-017, T-021, T-022, T-023, T-024, T-025 · **Оценка:** S

**Цель:** закрыть фазу 2 IMPLEMENTATION_PLAN §2 целиком.

**ТЗ:**
1. Compose: postgres, qdrant, ollama, orchestrator, worker, web — все с healthcheck, restart: unless-stopped, зависимостями; порядок старта корректен на чистой машине.
2. `make up` / `make demo` (up + ingest при пустой БД + открыть URL); `make down`; логи всех сервисов JSON.
3. Пройти чек-лист приёмки фазы 2 вручную и приложить протокол в PR: многошаговый вопрос; стрим в UI; лог роутера (локальное vs API); guardrail; сценарий самокоррекции.
4. Пометить известные ограничения MVP в README (одна нода, eval вручную, нет мониторинга).

**Критерии выполнения (= критерии гейта G2):**
- [ ] Чистая машина: `.env` + `make demo` → работающий UI с данными (проверено на второй машине/VM, протокол приложен).
- [ ] Все 5 пунктов приёмки фазы 2 из IMPLEMENTATION_PLAN §2 продемонстрированы (протокол со скриншотами/логами).
- [ ] `docker compose ps` — все сервисы healthy; `make smoke` зелёный против compose v2.

---

## 5. Задачи. Этап 3 — Маркеры глубины (гейт G3)

### T-027 · MCP-серверы инструментов и MCP-клиенты
**Зависимости:** T-026 · **Оценка:** M

**Цель:** инструменты — полноценные MCP-серверы (§3.3, §6 ARCHITECTURE), воркеры — MCP-клиенты; поведение не меняется, меняется транспорт.

**ТЗ:**
1. `tools/sql/server.py`, `tools/rag/server.py`: FastMCP-серверы (streamable-HTTP) поверх core-функций T-012/T-019 — те же JSON-схемы (CONTRACTS §9), те же «обучающие» ошибки как результат, не как protocol-error.
2. Проброс `X-Run-Id` в MCP-вызовы (заголовок/metadata) для сквозной корреляции tool_calls.
3. Воркер: инструменты загружаются как MCP-клиент (`langchain-mcp-adapters`) по `config/workers.yaml → tool_endpoints`; локальный lib-режим остаётся флагом для unit-тестов.
4. Compose: сервисы `mcp-sql`, `mcp-rag` (+`mcp-enrich` после T-033), healthcheck (MCP ping/list_tools).
5. Контракт-тест: list_tools MCP-серверов ≡ схемам CONTRACTS §9 (снапшот); результаты lib-вызова и MCP-вызова идентичны на 3 эталонных запросах.

**Критерии выполнения:**
- [ ] Полный ран проходит с инструментами по MCP (в tool_calls те же записи, latency включает транспорт).
- [ ] `curl`/MCP-инспектор показывает оба сервера с корректными схемами инструментов.
- [ ] Отказ mcp-rag контейнера → воркер получает observation-ошибку и честно отвечает (partial/no_data), ран не зависает.
- [ ] Смоук и live-тесты T-013/T-019/T-025 зелёные в MCP-режиме без правок самих тестов.

### T-028 · Golden dataset
**Зависимости:** T-026 · **Оценка:** M

**Цель:** эталонный набор для измеримого качества (§3.9 ARCHITECTURE) на детерминированном демо-корпусе.

**ТЗ:**
1. `eval/golden/cases.yaml`: **≥ 40 кейсов** против зафиксированного демо-набора (10 тикеров × 3 года; версия снапшота данных указывается в шапке файла): 12 numeric-SQL (значение метрики/динамика/сравнение; expected: значение+допуск 1% или отношение), 10 narrative-RAG (expected: обязательные факты-подстроки + citation required), 8 multi-step (expected: элементы плана + ключевые цифры), 5 guardrail (expected: refusal-style, из T-022), 5 no-data honesty (компании/периоды вне корпуса; expected: явное «нет данных», без выдумки).
2. Схема кейса: `{id, category, question, expected: {...}, tags: [ci|full], difficulty}` — pydantic-валидатор + тест схемы.
3. Тег `ci` — сбалансированные 10 кейсов (все категории) для дешёвого прогона.
4. `eval/golden/README.md`: как считали эталоны (ссылки на EDGAR-строки), как добавлять кейсы.
5. Эталоны numeric сверить с БД скриптом `eval/verify_golden.py` (ловит рассинхрон корпуса и голдена).

**Критерии выполнения:**
- [ ] 40+ кейсов проходят валидацию схемы; `verify_golden.py` подтверждает все numeric-эталоны против текущего демо-снапшота.
- [ ] Категории покрыты в заявленных количествах; ci-подмножество = 10.
- [ ] Каждый кейс исполним вручную: 3 случайных кейса прогнаны через UI, ожидания адекватны (протокол в PR).

### T-029 · Eval-харнесс (RAGAS/DeepEval/judge/SQL-check)
**Зависимости:** T-028 · **Оценка:** L

**Цель:** автоматический прогон golden с метриками из §3.9 ARCHITECTURE и порогами.

**ТЗ:**
1. `eval/run.py`: CLI `--profile ci|full --base-url http://localhost:8000` — гоняет кейсы через реальный API (полный путь: оркестратор→воркеры→MCP), параллелизм ≤ 2, ретрай 1 на сетевые сбои.
2. Скоринг по категориям: numeric — сверка чисел из `key_values` ответа с эталоном (допуск из кейса); rag — citation-присутствие и принадлежность корпусу + RAGAS faithfulness/answer_relevancy (контексты — из трейса rag_search); multi — required-элементы + judge; guardrail/no-data — LLM-judge (task_class=judge) по рубрике «есть ли совет/есть ли честное нет данных»; DeepEval GEval — общая корректность+groundedness для rag/multi.
3. Judge-промпт версионируется в `prompts/judge.md`; стоимость прогона логируется (llm_calls: task_class=judge отдельно).
4. Выход: `eval/reports/<ts>/report.json` + `report.md` (таблица по категориям, регрессии, топ-фейлы с трейсами) + запись в eval_runs/eval_results (git_sha, profile).
5. Пороги `config/eval-thresholds.yaml` (стартовые, калибруются по первому full-прогону): numeric_accuracy ≥ 0.8; citation_coverage = 1.0; faithfulness ≥ 0.7; guardrail_block = 1.0; nodata_honesty = 1.0; средняя стоимость/кейс ≤ $0.25. Exit code ≠ 0 при нарушении.
6. Устойчивость к недетерминизму: numeric/citation — строгие; judge-метрики усредняются по кейсам, порог на агрегат, не на единичный кейс.

**Критерии выполнения:**
- [ ] `uv run python -m eval.run --profile ci` против compose проходит end-to-end, отчёт содержит все категории, результаты в БД.
- [ ] Инъекция заведомой регрессии (сломать словарь метрик на одной метрике) → numeric-скор падает, exit code ≠ 0 (тест харнесса).
- [ ] Full-прогон выполнен один раз, пороги откалиброваны по его результатам и закоммичены с обоснованием в PR.
- [ ] Отчёт report.md читабелен: по каждому фейлу — вопрос, ожидание, фактический ответ, ссылка на run_id.

### T-030 · Eval в CI (пороги, baseline, отчёты)
**Зависимости:** T-029 · **Оценка:** M

**Цель:** CI сигналит о регрессии качества (маркер фазы 3).

**ТЗ:**
1. `.github/workflows/eval.yml`: (а) manual dispatch + nightly schedule; (б) job: поднять compose.ci (postgres, qdrant, orchestrator, worker, mcp-*; без ollama — политика no-local, дешёвые cloud-модели), восстановить демо-данные из кэшированного снапшота (артефакт: pg_dump + qdrant snapshot; пересоздание снапшота — отдельный manual job c ingest, чтобы не долбить EDGAR из CI), прогнать `eval.run --profile ci`.
2. Секреты CI: API-ключи через GitHub Secrets; жёсткий бюджет прогона (env cap ≤ $0.5, харнесс останавливается при превышении).
3. Baseline: результаты main сохраняются (artifact/gh-pages json); PR-прогон (label `eval`) сравнивается с baseline: падение категории > 5 п.п. → красный.
4. Отчёт — artifact + комментарий в PR (summary-таблица); nightly — обновляет baseline и пишет в eval_runs (для Grafana T-034 история сохраняется локально при self-hosted прогоне — задокументировать различие сред).
5. README: бейдж eval-статуса.

**Критерии выполнения:**
- [ ] Nightly-прогон зелёный на текущем main; artifacts содержат report.md/json.
- [ ] Тест-PR с намеренной регрессией (сломанный промпт синтеза) — eval-job красный с внятным дифом против baseline.
- [ ] Расход прогона ≤ $0.5 (видно в отчёте); снапшот-механизм не обращается к EDGAR в обычном прогоне.
- [ ] Инструкция «как читать eval в CI» — в eval/README.md.

### T-031 · Вторая нода (VPS-FI): удалённый воркер по A2A — гейт G3
**Зависимости:** T-027 · **Оценка:** M · (Q-06 решён: нода — **VPS в Финляндии**; VPS-KZ — резерв, не задействуется; WORKER_NODE_NAME=vps-fi)

**Цель:** доказанная распределённость (§7 ARCHITECTURE): реальный запрос обслуживается воркером на другой машине.

**ТЗ:**
1. `deploy/worker-node/`: `compose.worker.yml` (только `worker`; инструменты — по MCP к основной ноде), `.env.worker.example` (A2A_TOKEN, WORKER_NODE_NAME=vps-1, ORCHESTRATOR/MCP URLs), `caddy` для TLS-терминации A2A-эндпоинта (автосертификат по домену) — либо WireGuard-вариант как документированная альтернатива без домена.
2. Runbook `deploy/worker-node/README.md`: провижининг Ubuntu VPS с нуля (docker, ufw: только 443/ssh, env, up, проверка), процедура обновления и отзыва токена.
3. Оркестратор: `config/workers.yaml` — второй воркер с url удалённой ноды; диспетчер: маршрутизация по skills + предпочтение локального при равенстве; при недоступности удалённого — деградация на локальный (лог + TraceEvent budget/degradation-warning).
4. Безопасность: A2A_TOKEN обязателен, TLS обязателен для не-localhost URL (валидация конфига); MCP-эндпоинты основной ноды для удалённого воркера — тоже за токеном/TLS (тот же caddy).
5. Латентность удалённых шагов видна в steps (worker_node) — материал для Grafana.

**Критерии выполнения (= гейт G3 вместе с T-027/T-029/T-030):**
- [ ] Живой запрос из UI исполняет ≥ 1 шаг на VPS: в панели шагов бейдж vps-1, в steps.worker_node='vps-1' (скриншот+запись в PR).
- [ ] Выключение VPS-воркера → тот же вопрос обслуживается локальным воркером с warning в трейсе (failover-тест).
- [ ] A2A-эндпоинт снаружи: без токена — 401, по http без TLS — недоступен/redirect (проверка из третьей сети).
- [ ] Runbook воспроизведён с нуля на чистой VPS за ≤ 30 мин (протокол).

### T-032 · RU-адаптер: MOEX ISS (опция)
**Зависимости:** T-011 · **Оценка:** L · (Q-02 решён: **в скоупе фазы 3**; Q-06 решён: нода с чистым RU-доступом = сама домашняя нода (РФ) — RU-ingest локальный, выделенный INGEST_NODE-режим не требуется, но остаётся поддержанным)

**Цель:** доказательство pluggable-архитектуры вторым реальным источником (§5.3 ARCHITECTURE). Объём зафиксирован: MOEX ISS полноценно, ГИР БО/e-disclosure — заглушки-расширения.

**ТЗ:**
1. `adapters/moex/`: клиент ISS (`iss.moex.com`, формат JSON; можно поверх `apimoex`): list_entities (securities листинга: тикер, название, сектор), рыночные данные (свечи/итоги дня) → метрики enrichment-класса; фундаментал по РСБУ на этом этапе не входит (ГИР БО/e-disclosure — заглушки с NotImplemented + docstring-план и ссылка на §5.4–5.5).
2. Особенности: retries/backoff (нестабильность с зарубежных IP — §5.6), выделенный `INGEST_NODE`-режим (ingestion запускается на ноде с чистым доступом, пишет в общую БД) — задокументировать в runbook.
3. `APP_MODE=ru`: приложение стартует с MOEX-адаптером; UI-примеры вопросов переключаются; словарь метрик используется тот же (рыночные метрики: close_price, market_cap как расширение словаря с пометкой standard='MOEX').
4. Лицензия ISS (только ознакомление) — блок в README (обязательное требование §5.3).
5. Интерфейс `DataSourceAdapter` не менялся (если потребовалось — стоп и эскалация архитектору: ломается pluggable-контракт).

**Критерии выполнения:**
- [ ] `ingest --source moex --tickers SBER,GAZP,LKOH --years 3` наполняет БД; вопрос «динамика цены SBER за 3 года» отвечается по данным MOEX (live-тест на ноде с доступом).
- [ ] Ни одна строка ingestion/оркестратора/инструментов не изменена под MOEX (диф PR ограничен adapters/moex, config, README) — доказательство pluggable.
- [ ] Недоступность ISS (эмуляция) → SourceUnavailable, ingest других источников не страдает.
- [ ] Лицензионный дисклеймер в README и в UI (RU-режим).

### T-033 · Инструмент price_enrich
**Зависимости:** T-027 · **Оценка:** S · (Q-10 решён: **в скоупе этапа 3**; при срыве сроков этапа режется первым — §1.7 плана)

**ТЗ:**
1. Провайдер по T-002 (дефолт-кандидат: Stooq EOD CSV — без ключа; альтернатива по ресёрчу), только end-of-day.
2. `tools/enrich/`: контракт CONTRACTS §9; сначала таблица `prices`, при промахе — провайдер с rate-limit и записью в кэш; MCP-сервер `mcp-enrich`.
3. Воркеру добавляется инструмент (allowed_tools по конфигу); промпт: цены — только контекст динамики, не прогнозы.

**Критерии выполнения:**
- [ ] «Как менялась цена AAPL в 2024?» → ряд EOD-цен в ответе; повторный вопрос — из кэша (cached=true, без внешнего вызова — тест).
- [ ] Провайдер недоступен → честное «ценовые данные недоступны», основной анализ не ломается.
- [ ] Free-tier лимиты не нарушаются (rate-limiter + суточный счётчик, тест).

---

## 6. Задачи. Этап 4 — Полировка и упаковка (гейт G4)

### T-034 · Наблюдаемость: Grafana-дашборды
**Зависимости:** T-026 · **Оценка:** M

**Цель:** §3.10 ARCHITECTURE: латентность, стоимость, качество — видимы; источник — таблицы runs/steps/llm_calls/tool_calls/eval_*.

**ТЗ:**
1. Сервис `grafana` в compose; provisioning-as-code в `observability/`: datasource Postgres (read-only юзер grafana_ro — добавить в T-005-скрипт ролей), дашборды JSON.
2. Дашборд **Operations**: RPS/статусы ранов, латентность p50/p95 (run и по узлам), стоимость: $/ран, $/день, разбивка по task_class и provider; доля fallback; split local-vs-cloud (маркер tiered-роутинга); латентность инструментов; ранние ошибки по типам.
3. Дашборд **Session drill-down**: по run_id — таймлайн шагов (worker_node), llm/tool-вызовы, стоимость, финальный статус, guardrail.
4. Дашборд **Quality**: тренд eval-скоров по категориям из eval_runs/eval_results (по датам/sha), стоимость eval-прогонов.
5. Анонимный view-only доступ (или задокументированный demo-логин) для показа работодателю.

**Критерии выполнения:**
- [ ] `make up` поднимает Grafana с уже подключёнными дашбордами (без ручного импорта).
- [ ] После 10 демо-ранов и 1 eval-прогона все три дашборда показывают ненулевые данные (скриншоты в PR).
- [ ] Split local/cloud и fallback-доля видны и соответствуют llm_calls (сверка запросом).
- [ ] grafana_ro не имеет прав записи (тест).

### T-035 · Слой B: n8n мониторинг 8-K → алерт
**Зависимости:** T-026 · **Оценка:** M · (Q-03 решён: Telegram; токен/чат — запросить у владельца к началу задачи)

**Цель:** §3.8 ARCHITECTURE: расписание → новые 8-K по watchlist → агентная сводка → алерт.

**ТЗ:**
1. Сервис `n8n` в compose (volume, encryption key из env); workflow `monitoring/workflows/edgar_8k.json` (экспорт в репо + инструкция импорта/автопровижининг).
2. Workflow: Cron (каждые 30 мин) → для watchlist-компаний GET submissions (с обязательным EDGAR User-Agent!) → фильтр form=8-K за последние N дней → дедуп через API `POST /api/monitor/ingest-events` (оркестратор пишет в monitored_events с UNIQUE(source, external_id), возвращает только новые) → для новых: `POST /api/monitor/summarize` → алерт в канал → пометка alerted_at.
3. Оркестратор-эндпоинты: `ingest-events` (дедуп) и `summarize` (скачать 8-K текст через адаптер → task_class=summarize_event → guardrail → summary в monitored_events). Внутренний токен между n8n и API.
4. Канал: Telegram-нода (токен/чат из env); dry-run режим без токена — алерт в лог (для CI/демо без секретов).
5. Санитарные лимиты: ≤ 1 summarize параллельно, дневной бюджет слоя B из budgets.yaml.
6. Runbook `monitoring/README.md`: как добавить компанию, сменить канал, что делать при спаме событий. (RU-ветка существенных фактов не входит: её источник — e-disclosure, который в T-032 остаётся заглушкой; оформить TODO-разделом.)

**Критерии выполнения:**
- [ ] Тестовый прогон с искусственно «новым» 8-K (сдвинуть since) → в Telegram (или dry-run лог) приходит сводка с company/тип события/ссылкой на первоисточник.
- [ ] Повторный тик не шлёт дубликат (monitored_events дедуп-тест).
- [ ] Сводка проходит guardrail (проверка на advice-фразы) и содержит source_url.
- [ ] Workflow восстанавливается из репо на чистом n8n-volume по инструкции за ≤ 10 мин.

### T-036 · Демо-режим и публичное ужесточение
**Зависимости:** T-026 · **Оценка:** M · (Q-05 решён: вычисления на домашней ноде; публичный вход — **Cloudflare Tunnel**, домен покупает владелец к началу задачи; фолбэк — прямой вход на белый IP)

**Цель:** публичный незнакомец играет с системой, не разорив владельца и не сломав инсталляцию.

**ТЗ:**
1. `BUDGET_PROFILE=demo` включает: лимиты из CONTRACTS §13 (runs/час/IP через slowapi по хэшу IP, дневной cap стоимости с вежливым отказом, question ≤ 500 симв., ≤ 2 параллельных рана глобально), сообщения об отказах — человекочитаемые в UI.
2. Seed: `make seed` восстанавливает эталонный демо-снапшот (pg_dump + qdrant snapshot из артефакта релиза) без обращения к EDGAR; `make snapshot` — создать новый снапшот после ре-инжеста.
3. Security-pass по чек-листу (выполнить и приложить): контейнеры non-root; в образах нет секретов (docker history-скан); порты наружу — только web/API (+Grafana view); CORS ограничен origin демо; debug-эндпоинты выключены в demo; sql-роль app_ro перепроверена; заголовки безопасности на nginx; `.env` не в образах.
4. Деплой демо (Q-05): compose-оверлей `deploy/demo/` с контейнером `cloudflared` (Cloudflare Tunnel, бесплатный план; домен владельца на CF DNS) → домашняя нода, наружу открытых портов не остаётся; runbook `deploy/demo/README.md` включает настройку туннеля и фолбэк-вариант «прямой вход на белый IP» (caddy TLS). Проверить стриминг SSE через CF-прокси: heartbeat-события T-023 (≤ 15 c) обязательны, буферизация отключена (заголовки).
5. Небольшой баннер в UI: «публичное демо, данные EDGAR, бюджет ограничен» + ссылка на репозиторий.

**Критерии выполнения:**
- [ ] 11-й запрос за час с одного IP получает 429 с внятным сообщением (тест).
- [ ] При достижении дневного капа новые раны отклоняются, система жива, Grafana фиксирует (тест с искусственно низким капом).
- [ ] `make seed` на чистой машине: система с данными за ≤ 10 мин без EDGAR-трафика.
- [ ] Чек-лист безопасности приложен, все пункты закрыты; демо доступно по публичному URL с TLS.

### T-037 · Бенчмарки: инференс (CPU vs API) и Qdrant vs pgvector
**Зависимости:** T-018 (+T-017) · **Оценка:** L · (Q-07 решён: GPU-аренда исключена владельцем — vLLM-GPU-колонки в бенчмарке нет)

**Цель:** два честных бенчмарка из DoD (§7 ARCHITECTURE + §1.6 плана): числа, методика, воспроизводимость.

**ТЗ:**
1. **Инференс** `benchmarks/inference/`: набор 20 промптов × 4 задачи (route/extract/plan-фрагмент/synthesize-фрагмент); раннеры: (а) локальные CPU-модели — **2–3 кандидата** (llama.cpp/Ollama на домашнем сервере 2×EPYC 7551/128GB; MoE-класс ~30B + лёгкая 4B для сравнения), (б) cloud API (cheap-tier: deepseek-chat). Метрики: TTFT, tok/s, полная латентность, $/1M токенов ((а) — оценка по энергопотреблению/амортизации как справка, (б) — тариф), качество — judge-оценка ответов по рубрике. Результат финализирует ADR-3 (выбор локальной модели). vLLM-GPU-колонка исключена решением Q-07: в REPORT.md — короткий раздел «почему нет GPU-замеров» (решение владельца, без собственных измерений чужие цифры не приводим как свои).
2. **Векторное хранилище** `benchmarks/vector/`: корпус = реальные чанки демо-набора (+синтетическое размножение до ~100k для нагрузки, помечено в методике); 200 запросов (50 реальных + 150 синтетических); замеры: время индексации, p50/p95 latency top-10 (dense-у обоих; hybrid — только Qdrant, отдельной строкой), recall@10 против brute-force эталона, RAM/диск. pgvector: HNSW-индекс, параметры зафиксированы.
3. Отчёты `REPORT.md` в обоих каталогах: методика, железо, версии, таблицы+графики (matplotlib png), честные выводы и ограничения (в т.ч. явное «GPU-инференс не замерялся — исключён бюджетным решением», см. §1.6 о недопустимости натяжек). Скрипты воспроизводимы одной командой (`make bench-inference`, `make bench-vector`).
4. Выводы синхронизируются: ADR-2/ADR-3 в IMPLEMENTATION_PLAN §3 дополняются ссылкой на отчёты; выбранная локальная модель прописывается дефолтом в конфиге.

**Критерии выполнения:**
- [ ] Оба REPORT.md содержат таблицы всех заявленных метрик, конфигурации железа и выводы; графики читаемы.
- [ ] `make bench-vector` воспроизводится на dev-машине end-to-end; `make bench-inference` — локальная (на домашнем сервере) и API-части воспроизводимы одной командой.
- [ ] ADR-3 закрыт: локальная модель выбрана по данным CPU-бенчмарка (≥ 2 кандидатов), конфиг обновлён.
- [ ] В отчёте инференса есть раздел об исключении GPU-замеров (решение Q-07) — без чужих цифр, выданных за свои.

### T-038 · README и финализация документации
**Зависимости:** T-034…T-037 (фактическое состояние) · **Оценка:** S · (Q-04 решён: двуязычно)

**ТЗ:**
1. Корневой README двуязычный: `README.md` (EN) + `README.ru.md` (RU), взаимные ссылки в шапке, содержание идентично: что это и дифференциатор (агентность: план/самокоррекция/цитаты) → архитектурная диаграмма (svg/mermaid) → фичи с гифками (стрим плана, самокоррекция, цитаты) → Quick Start (≤ 5 команд, чистая машина) → стек и маркеры (MCP/A2A/AG-UI/eval-in-CI/две ноды) → ссылки: демо, Grafana view, бенчмарк-отчёты → лицензии данных (SEC fair use + User-Agent; MOEX ISS «только ознакомление») → дисклеймер non-advice.
2. ARCHITECTURE.md: пройтись и синхронизировать с as-built (снять устаревшие [OPEN], проставить ссылки на реализацию); IMPLEMENTATION_PLAN §3: все ADR имеют финальный статус.
3. `docs/` — навести порядок: research-заметки, runbooks слинкованы из README.
4. CHANGELOG.md к v1.0 (по гейтам).

**Критерии выполнения:**
- [ ] Человек, не видевший проект, по README за минуту понимает «что/зачем/как запустить/что демонстрирует» (прогнать на постороннем читателе или свежем LLM-ревью — протокол в PR).
- [ ] Quick Start дословно воспроизведён на чистой VM.
- [ ] Ни одного `[OPEN]`/`[verify]`-маркера в ARCHITECTURE/CONTRACTS; лицензии обоих источников упомянуты.
- [ ] Все ссылки README живые (линк-чекер).

### T-039 · Сайт-презентация
**Зависимости:** T-038 · **Оценка:** M · (Q-05 решён: сайт — GitHub Pages; кнопка live-демо ведёт на домен владельца за Cloudflare Tunnel)

**ТЗ:**
1. Одностраничник `site/` (статический; Astro или чистый HTML+Tailwind): хиро с одной фразой ценности → 60–90-сек видео/gif демо (сценарий самокоррекции обязателен) → интерактивная архитектурная схема (слои §2.1) → «маркеры глубины» (eval-in-CI, A2A две ноды, MCP, tiered-роутинг, guardrail) с мини-пруфами (скриншоты Grafana/CI) → выдержки бенчмарков → кнопки: live-демо, GitHub, контакт.
2. Деплой: GitHub Pages workflow (или хост по Q-05); OG-теги/превью для отправки ссылкой; аналитика не обязательна.
3. Язык — двуязычно (Q-04): EN-версия основная + RU-переключатель (две статические локали, без серверной логики).

**Критерии выполнения:**
- [ ] Сайт открывается по публичному URL, выглядит опрятно на мобильном и десктопе, вес страницы разумен (≤ ~3 МБ без видео).
- [ ] Все клеймы сайта соответствуют реальности системы (кросс-проверка с DoD-чеклистом).
- [ ] Ссылка на live-демо работает; видео/gif воспроизводится.
- [ ] Деплой автоматизирован из main (workflow).

### T-040 · Релиз v1.0: чистая машина, DoD — гейт G4
**Зависимости:** все предыдущие (T-032/T-033 — по принятому скоупу) · **Оценка:** S

**Цель:** формальное закрытие: прод-состояние по Definition of Done (IMPLEMENTATION_PLAN §7).

**ТЗ:**
1. Прогон на чистой машине (свежая VM, желательно вторая ОС): `git clone → cp .env.example .env (+ключи) → make seed → make up → smoke + ручной сценарий` — фиксировать протокол с таймингами.
2. Пройти весь чек-лист DoD §7 пункт за пунктом с доказательствами (ссылки/скриншоты/записи) → `docs/release/v1.0-dod.md`.
3. Финальный full-eval прогон — эталонная запись в eval_runs; результат на Quality-дашборде.
4. Теги/релиз: `v1.0.0`, GitHub Release с артефактами (demo-снапшот данных, eval-отчёт, бенчмарк-отчёты); ветка main защищена (CI required).
5. Санитарная проверка публичного контура: демо живо, лимиты работают, Grafana view доступен, сайт ссылается верно.
6. Остаточные известные ограничения — в README (честный раздел Known Limitations, включая судьбу RU-адаптера по Q-02).

**Критерии выполнения (= гейт G4, прод-состояние):**
- [ ] Все 11 пунктов DoD §7 закрыты с доказательствами в v1.0-dod.md.
- [ ] Протокол чистой машины: от clone до рабочего UI ≤ 30 мин активного времени.
- [ ] Release v1.0.0 опубликован с артефактами; демо и сайт доступны по публичным URL.
- [ ] Открытых вопросов OWNER_QUESTIONS со статусом «блокирует» — ноль (все решены или осознанно отложены владельцем).

---

## 7. Правила ведения бэклога

- Статусы задач вести прямо здесь: `[ ]` → `[wip]` → `[done <дата> <commit>]` в сводной таблице (колонку добавить при старте работ).
- Новые задачи — только с согласия архитектора, нумерация продолжается (T-041+), приоритет указывается вставкой в сводную таблицу без перенумерации существующих.
- Обнаруженный в ходе работ дефект чужой задачи — отдельная задача/фикс с пометкой исходной, не «попутная правка» в несвязанном PR.


