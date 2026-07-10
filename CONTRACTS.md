# Технические контракты и конвенции

**Проект:** **LedgerLens** (Q-08) — self-hostable мультиагентная платформа финансового анализа
**Аудитория:** агент-разработчик. Документ дополняет ARCHITECTURE.md конкретикой: DDL, схемы, интерфейсы, конвенции.
**Статус:** v1.1 — дефолты проверены задачей T-002 (2026-07-10), метки `[verify:T-002]` сняты. Источники и даты проверки — `docs/research/adr-notes.md`. Точные версии пакетов запинены в `uv.lock`; мажоры не апгрейдить без отдельной задачи.

---

## 1. Стек и версии (дефолты)

| Компонент | Выбор по умолчанию | Примечание |
|---|---|---|
| Python | 3.12, пакетный менеджер **uv** | один корневой `pyproject.toml` |
| Линт/типы | ruff (line-length 100) + mypy | в pre-commit и CI |
| Тесты | pytest + pytest-asyncio + respx (записанные HTTP-фикстуры) | |
| Web-бэкенд | FastAPI + uvicorn, SSE | |
| Агенты | LangGraph **1.2.x** (v1-линия; `create_react_agent` — в `langgraph-prebuilt`) + `langgraph-checkpoint-postgres` 3.1 ✅T-002 | |
| MCP | python-SDK `mcp` **1.28+** (FastMCP, транспорт streamable-HTTP) + `langchain-mcp-adapters` **0.3** ✅T-002 | |
| A2A | `a2a-sdk` **1.1** (протокол A2A v1.0, март 2026; подписанные AgentCard) ✅T-002 | |
| AG-UI | `ag-ui-protocol` (python, PyPI) + `@ag-ui/client` на фронте; имена событий подтверждены (§10) ✅T-002 | |
| БД | PostgreSQL 16 + pgvector; SQLAlchemy 2 (async) + Alembic | |
| Векторная БД | Qdrant (server, docker) + qdrant-client | ADR-2 |
| Эмбеддинги | **intfloat/multilingual-e5-large** (1024d, multilingual; префиксы query/passage через fastembed API) через fastembed **0.8**/ONNX на CPU. ⚠️Поправка T-018: bge-m3 из вывода T-002 в fastembed 0.8 НЕ поддержан (вскрыто живым запуском) | ADR-6 решён (поправлен T-018) |
| Sparse (hybrid) | fastembed BM25 + server-side fusion (RRF) в Qdrant Query API (`query_points` + `prefetch`) ✅T-002 | |
| Reranker | jinaai/jina-reranker-v2-base-multilingual (CPU, ONNX; fastembed 0.8 — поправка T-018: bge-reranker-v2-m3 не поддержан); fallback: ms-marco-MiniLM cross-encoder | |
| Локальный LLM | Ollama (0.30+); дефолт-кандидат: **qwen3.5:27b** (sparse-MoE, 17 ГБ Q4, 256K ctx — сменил qwen3:30b-a3b по T-002); лёгкий fallback **qwen3.5:4b**; финал — CPU-бенчмарк T-037 (кандидаты: qwen3.5:27b / qwen3:30b-a3b / qwen3.5:122b — 81 ГБ, в 128 ГБ RAM влезает) | ADR-3 дефолт |
| Cloud LLM | **DeepSeek** (Q-01), пины T-002 (2026-07-10): cheap = **deepseek-v4-flash** (thinking disabled), strong/judge = **deepseek-v4-pro** (thinking enabled). ⚠️ Старые имена deepseek-chat/deepseek-reasoner **deprecated 2026-07-24** — не использовать. Облачного fallback между провайдерами нет — резерв только локальная модель (риск Q-01) | ADR-7 решён |
| HTTP-клиент | httpx + tenacity (ретраи) | |
| SQL-валидация | sqlglot | для sql_query guard |
| Логи | structlog (JSON) | |
| Фронт | Node 22 + pnpm, Vite, React 18, TypeScript strict | стили — Tailwind (рекомендация) |
| Автоматизация | n8n (официальный docker-образ) | слой B |
| Наблюдаемость | Grafana OSS + datasource PostgreSQL (без отдельного TSDB — метрики из таблиц) | |
| CI | GitHub Actions (Q-08: репо `ledgerlens`, приватный до G2, MIT) | |

Правило: версии запинены T-002 (2026-07-10) в `pyproject.toml`+`uv.lock` (ключевые: langgraph 1.2.8, mcp 1.28.1, langchain-mcp-adapters 0.3.0, a2a-sdk 1.1.0, ag-ui-protocol 0.1.19, qdrant-client 1.18.0, fastembed 0.8.0, sqlalchemy 2.0.51, sqlglot 30.12); `package.json` пинуется при создании фронта (T-024). Не апгрейдить мажоры без задачи.

---

## 2. Структура репозитория (уточняет §9 ARCHITECTURE.md)

```
/
├── docker-compose.yml          # базовый; + compose.demo.yml, compose.ci.yml оверлеи
├── Makefile                    # up, down, ingest, demo, seed, test, eval, lint
├── pyproject.toml / uv.lock
├── .env.example                # все переменные с комментариями, без значений секретов
├── README.md / ARCHITECTURE.md / IMPLEMENTATION_PLAN.md / CONTRACTS.md / BACKLOG.md / OWNER_QUESTIONS.md
├── .github/workflows/          # ci.yml, eval.yml
├── config/                     # app.yaml, router.yaml, prices.yaml, rag.yaml, workers.yaml, budgets.yaml, eval-thresholds.yaml
├── common/                     # config.py, logging.py, db.py, models.py (доменные pydantic), errors.py, tracing.py
├── prompts/                    # версионируемые промпты (см. §12)
├── adapters/                   # base.py + edgar/ moex/ girbo/ edisclosure/
├── ingestion/                  # CLI пайплайна
├── db/                         # alembic + миграции
├── tools/                      # sql/, rag/, enrich/ — библиотеки + MCP-обёртки серверов
├── model_router/
├── rag/                        # chunking, embedding, rerank
├── workers/                    # ReAct-воркер, A2A-сервер
├── orchestrator/               # LangGraph P&E, FastAPI (chat + AG-UI + служебные эндпоинты)
├── web/                        # React/TS UI
├── monitoring/                 # n8n workflows (json) + runbook
├── observability/              # Grafana provisioning (datasources, dashboards json)
├── eval/                       # golden/, runner, метрики
├── benchmarks/                 # inference/, vector/
├── deploy/                     # worker-node/ (вторая нода), runbooks
├── demo/                       # сиды, сценарии демо, self-correction скрипт
├── scripts/                    # smoke_test.py, seed.py и пр.
├── tests/                      # зеркалит пакеты; unit/ contract/ integration/
└── data/cache/                 # HTTP-кэш источников (gitignore)
```

---

## 3. Конвенции кода

- Идентификаторы, docstrings и комментарии в коде — **английский**; внутренние документы — русский; витрина (README/UI/сайт) — двуязычная EN+RU (Q-04).
- Только типизированный код (mypy проходит); pydantic v2 для всех DTO на границах.
- Никакой бизнес-логики в эндпоинтах — тонкие ручки поверх сервисных модулей.
- Все внешние вызовы (HTTP, LLM, БД) — с таймаутом; без «голых» `except`.
- Секреты — только из env (см. §5); в коде и конфигах в git — никогда.
- Один PR/коммит на задачу бэклога, сообщение начинается с `T-0XX:`.

## 4. Логирование и корреляция

- structlog, JSON в stdout. Обязательные поля: `ts, level, event, run_id?, step_id?, node, service`.
- `run_id` — UUID запроса пользователя; `step_id` — шаг плана; пробрасываются через контекст (contextvars) во все слои, включая MCP-вызовы (заголовок `X-Run-Id`).
- Никогда не логировать: значения API-ключей, полные простыни промптов на INFO (полные — на DEBUG).

## 5. Конфигурация

Env-переменные (полный список — в `.env.example`):

```
APP_MODE=us|ru            APP_ENV=dev|demo|prod       BUDGET_PROFILE=dev|demo
POSTGRES_HOST/PORT/DB/USER/PASSWORD    POSTGRES_RO_PASSWORD   # read-only роль для sql_query
QDRANT_URL                OLLAMA_BASE_URL              LOCAL_MODEL
ANTHROPIC_API_KEY / OPENAI_API_KEY / DEEPSEEK_API_KEY / GOOGLE_API_KEY   # опциональны, роутер учитывает наличие
EDGAR_USER_AGENT="ledgerlens zzlawlzz5@gmail.com"   # обязателен, см. §5.1 ARCHITECTURE (Q-11)
EDGAR_PROXY_URL           # опционально: egress-прокси для *.sec.gov (зеркальный риск §5.6 — основная нода в РФ)
DAILY_COST_CAP_USD=1.5    # дневной потолок стоимости системы (Q-07: бюджет $50/мес)
A2A_TOKEN                 WORKER_NODE_NAME=local|vps-fi
TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID        # слой B (Q-03: Telegram; без токена — dry-run)
GRAFANA_ADMIN_PASSWORD    N8N_ENCRYPTION_KEY
```

YAML-конфиги в `config/` загружаются через `common/config.py` (pydantic-settings, env-подстановка `${VAR}`). Изменение контракта конфига = изменение `.env.example` + этого раздела.

---

## 6. Модель данных v1 (DDL)

Миграция 001 (домен):

```sql
CREATE TABLE companies (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source TEXT NOT NULL,                -- 'edgar'|'moex'|'girbo'|'edisclosure'
  external_id TEXT NOT NULL,           -- CIK | ИНН | тикер MOEX
  ticker TEXT, name TEXT NOT NULL, sector TEXT,
  meta JSONB NOT NULL DEFAULT '{}', created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (source, external_id)
);

CREATE TABLE filings (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  company_id BIGINT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
  source_filing_id TEXT NOT NULL,      -- accession number (EDGAR) | id источника
  form_type TEXT NOT NULL,             -- '10-K'|'10-Q'|'8-K'|'БФО'|'сущ.факт'|...
  period_end DATE, fiscal_year INT, fiscal_period TEXT,   -- 'FY'|'Q1'..'Q4'
  filed_at TIMESTAMPTZ, correction_number INT NOT NULL DEFAULT 0,
  source_url TEXT, meta JSONB NOT NULL DEFAULT '{}',
  UNIQUE (company_id, source_filing_id, correction_number)
);

CREATE TABLE financial_facts (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  filing_id BIGINT NOT NULL REFERENCES filings(id) ON DELETE CASCADE,
  company_id BIGINT NOT NULL REFERENCES companies(id) ON DELETE CASCADE,   -- денормализация для простого SQL агента; CASCADE добавлен в T-005: без него NO ACTION-проверка этого FK срабатывает раньше каскада companies→filings→facts и блокирует удаление компании
  metric TEXT NOT NULL,                -- каноническое имя (§7)
  value NUMERIC(28,4) NOT NULL, unit TEXT NOT NULL,      -- 'USD'|'RUB'|'shares'|'USD/share'
  period_start DATE, period_end DATE NOT NULL,
  fiscal_year INT, fiscal_period TEXT NOT NULL,
  standard TEXT NOT NULL,              -- 'US-GAAP'|'РСБУ'
  source_tag TEXT,                     -- исходный XBRL-тег / код строки РСБУ
  UNIQUE (filing_id, metric, period_end, fiscal_period, unit)
);
CREATE INDEX ix_facts_lookup ON financial_facts (company_id, metric, fiscal_period, period_end);

-- Один и тот же факт легитимно повторяется в разных filings (рестейтменты).
-- Агент по умолчанию читает представление «последняя версия факта»:
CREATE VIEW latest_facts AS
SELECT DISTINCT ON (ff.company_id, ff.metric, ff.period_end, ff.fiscal_period, ff.unit)
       ff.*, c.ticker, c.name AS company_name, f.form_type, f.filed_at, f.source_url
FROM financial_facts ff
JOIN companies c ON c.id = ff.company_id
JOIN filings f ON f.id = ff.filing_id
ORDER BY ff.company_id, ff.metric, ff.period_end, ff.fiscal_period, ff.unit,
         f.filed_at DESC, f.correction_number DESC;

CREATE TABLE filing_sections (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  filing_id BIGINT NOT NULL REFERENCES filings(id) ON DELETE CASCADE,
  section TEXT NOT NULL,               -- 'risk_factors'|'mdna'|'business'|RU-эквиваленты
  title TEXT, text TEXT NOT NULL,
  UNIQUE (filing_id, section)
);

CREATE TABLE section_chunks (          -- текст чанков + pgvector (для бенчмарка и связки)
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  section_id BIGINT NOT NULL REFERENCES filing_sections(id) ON DELETE CASCADE,
  chunk_index INT NOT NULL, text TEXT NOT NULL,
  embedding vector(1024),              -- размерность = ADR-6; менять только миграцией
  UNIQUE (section_id, chunk_index)
);
```

Миграция 002 (наблюдаемость и eval):

```sql
CREATE TABLE runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  question TEXT NOT NULL, mode TEXT NOT NULL DEFAULT 'us',
  status TEXT NOT NULL,                -- 'running'|'succeeded'|'failed'|'budget_exceeded'
  plan JSONB, answer TEXT, error TEXT,
  tokens_in BIGINT NOT NULL DEFAULT 0, tokens_out BIGINT NOT NULL DEFAULT 0,
  cost_usd NUMERIC(12,6) NOT NULL DEFAULT 0, latency_ms INT,
  client_meta JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), finished_at TIMESTAMPTZ
);
CREATE TABLE steps (
  id UUID PRIMARY KEY, run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  node TEXT NOT NULL,                  -- 'planner'|'worker'|'synthesizer'|'guardrail'
  worker_node TEXT,                    -- 'local'|'vps-1'
  status TEXT NOT NULL, goal TEXT, output JSONB, error TEXT,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(), finished_at TIMESTAMPTZ
);
CREATE TABLE llm_calls (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id UUID, step_id UUID, task_class TEXT NOT NULL,
  provider TEXT NOT NULL, model TEXT NOT NULL,
  tokens_in INT, tokens_out INT, cost_usd NUMERIC(12,6), latency_ms INT,
  fallback_used BOOLEAN NOT NULL DEFAULT false, error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE tool_calls (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id UUID, step_id UUID, tool TEXT NOT NULL, arguments JSONB,
  status TEXT NOT NULL, latency_ms INT, result_preview TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE eval_runs (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  git_sha TEXT, profile TEXT NOT NULL, summary JSONB,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(), finished_at TIMESTAMPTZ
);
CREATE TABLE eval_results (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  eval_run_id BIGINT NOT NULL REFERENCES eval_runs(id) ON DELETE CASCADE,
  case_id TEXT NOT NULL, category TEXT NOT NULL, passed BOOLEAN,
  scores JSONB, run_id UUID, details JSONB
);
CREATE TABLE monitored_events (        -- слой B
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  source TEXT NOT NULL, external_id TEXT NOT NULL,
  company_id BIGINT REFERENCES companies(id), event_type TEXT NOT NULL,
  occurred_at TIMESTAMPTZ, payload JSONB, summary TEXT, alerted_at TIMESTAMPTZ,
  UNIQUE (source, external_id)
);
CREATE TABLE prices (                  -- опционально (price_enrich)
  company_id BIGINT NOT NULL REFERENCES companies(id),
  trade_date DATE NOT NULL, close NUMERIC(18,6) NOT NULL,
  currency TEXT NOT NULL, source TEXT NOT NULL,
  PRIMARY KEY (company_id, trade_date, source)
);
```

Роли БД: `app` (rw) и `app_ro` (только SELECT на домен-таблицы и `latest_facts`) — sql_query работает **только** под `app_ro`.

---

## 7. Словарь канонических метрик v1 (ADR-1)

Хранится в коде: `common/metrics.py` (+ отдаётся через `schema_introspect`). Цепочка тегов — приоритет слева направо, берётся первый найденный. RU-коды — строки форм РСБУ, ориентир для будущего адаптера `[уточнить при реализации RU]`.

| canonical | описание | us-gaap теги (цепочка) | РСБУ |
|---|---|---|---|
| revenue | Выручка | RevenueFromContractWithCustomerExcludingAssessedTax → Revenues → SalesRevenueNet | 2110 |
| cost_of_revenue | Себестоимость | CostOfRevenue → CostOfGoodsAndServicesSold | 2120 |
| gross_profit | Валовая прибыль | GrossProfit | 2100 |
| operating_income | Операционная прибыль | OperatingIncomeLoss | 2200 |
| net_income | Чистая прибыль | NetIncomeLoss | 2400 |
| eps_basic | Базовая прибыль на акцию | EarningsPerShareBasic | 2900 |
| eps_diluted | Разводнённая прибыль на акцию | EarningsPerShareDiluted | — |
| total_assets | Активы | Assets | 1600 |
| total_liabilities | Обязательства | Liabilities | 1400+1500 |
| equity | Собственный капитал | StockholdersEquity → StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest | 1300 |
| cash_and_equivalents | Денежные средства | CashAndCashEquivalentsAtCarryingValue → CashAndDueFromBanks (банки, добавлен T-009) | 1250 |
| long_term_debt | Долгосрочный долг | LongTermDebtNoncurrent → LongTermDebt | 1410 |
| operating_cash_flow | Денежный поток от операций | NetCashProvidedByUsedInOperatingActivities | 4100 |
| capex | Капитальные затраты | PaymentsToAcquirePropertyPlantAndEquipment | 4221 |
| rnd_expense | Расходы на R&D | ResearchAndDevelopmentExpense | — |
| shares_outstanding | Акций в обращении | dei:EntityCommonStockSharesOutstanding → CommonStockSharesOutstanding | — |

Правила: расширять словарь — добавлением, не переименованием; `source_tag` в фактах всегда хранит фактический тег; неотмапленные теги логируются со статистикой (материал для расширения).

---

## 8. Интерфейс DataSourceAdapter

`adapters/base.py`:

```python
class DataSourceAdapter(ABC):
    source: str  # 'edgar' | 'moex' | ...

    @abstractmethod
    async def list_entities(self, tickers: list[str] | None = None) -> list[Company]: ...
    @abstractmethod
    async def fetch_filings(self, company: Company, years: int) -> list[Filing]: ...
    @abstractmethod
    async def extract_facts(self, company: Company, filings: list[Filing]) -> list[FinancialFact]: ...
    @abstractmethod
    async def extract_sections(self, company: Company, filing: Filing) -> list[FilingSection]: ...
    @abstractmethod
    async def poll_events(self, watchlist: list[Company], since: datetime) -> list[Event]: ...
```

DTO (`common/models.py`, pydantic): `Company`, `Filing`, `FinancialFact`, `FilingSection`, `Event` — поля 1:1 с DDL §6 (без id БД; натуральные ключи). Адаптер не пишет в БД — только возвращает DTO; запись — ответственность ingestion. Ошибки источника → `SourceUnavailableError` (ingestion деградирует, не падает).

---

## 9. Контракты инструментов (JSON-схемы, единые для lib- и MCP-варианта)

**sql_query** — read-only SQL к Postgres под ролью `app_ro`.
- Вход: `{"sql": str, "row_limit": int<=200 (default 50)}`
- Валидация: sqlglot-парс; только `SELECT`/`WITH`; один statement; `statement_timeout=10s`; принудительный LIMIT.
- Выход: `{"columns": [...], "rows": [[...]], "row_count": int, "truncated": bool}`
- Ошибка — не исключение, а observation для самокоррекции: `{"error": str, "hint": str, "schema_excerpt": str}` (hint: «вызови schema_introspect», список доступных метрик при ошибке по metric).

**schema_introspect** — без параметров. Выход: `{"tables": [{name, columns:[{name,type,description}]}], "metrics": [{name, description, unit_hint}], "examples": [sql...]}`. Обязательно описывает `latest_facts` как основную точку входа.

**rag_search**
- Вход: `{"query": str, "top_k": int<=10 (default 5), "filters": {"tickers": [..]?, "form_types": [..]?, "sections": [..]?, "period_from": date?, "period_to": date?}}`
- Выход: `{"chunks": [{"text": str, "score": float, "citation": {"ticker","form_type","period","section","source_url","filing_id","chunk_id"}}], "no_results": bool}`
- При пустой выдаче `no_results=true` + текст «данных нет» — агент обязан честно ответить «нет данных», не фантазировать.

**price_enrich** (опция)
- Вход: `{"ticker": str, "date_from": date, "date_to": date}`; выход: `{"series": [{"date","close","currency"}], "source": str, "cached": bool}`. Только EOD, сперва кэш (`prices`), затем провайдер.

Общие требования: строгая JSON-схема параметров; идемпотентность чтения; таймауты; latency и статус каждого вызова пишутся в `tool_calls`.

---

## 10. Агентские контракты

**WorkerTask / WorkerResult** (единый формат для локального вызова и A2A):

```json
WorkerTask  = {"task_id","run_id","goal","context":{"prior_results":[],"constraints":[],"mode":"us"},
               "allowed_tools":["sql_query","rag_search"],
               "budget":{"max_iterations":8,"max_tokens":30000,"deadline_s":90}}
WorkerResult= {"task_id","status":"succeeded|failed|no_data|budget_exceeded",
               "answer":str,"evidence":{"facts":[],"citations":[]},
               "trace":[TraceEvent],"usage":{"tokens_in","tokens_out","cost_usd","tool_calls"}}
```

**TraceEvent** — сквозная шина событий (внутренняя, сериализуется в SSE и в БД):

```
{ts, run_id, step_id?, seq, event, payload}
event ∈ run_started | plan_created | plan_updated | step_started | agent_thought |
        tool_call_started | tool_call_finished | llm_call | step_finished |
        guardrail | answer_delta | budget | run_finished | run_error
```

**Маппинг TraceEvent → AG-UI** (имена событий сверены с `ag-ui-protocol` 0.1.x — T-002, 2026-07-10: RUN_*, STATE_SNAPSHOT/STATE_DELTA — JSON Patch RFC 6902, TOOL_CALL_*, TEXT_MESSAGE_*, CUSTOM — всё присутствует в EventType протокола):

| TraceEvent | AG-UI |
|---|---|
| run_started / run_finished / run_error | RUN_STARTED / RUN_FINISHED / RUN_ERROR |
| plan_created | STATE_SNAPSHOT (state.plan) |
| plan_updated, step_started/finished | STATE_DELTA (JSON Patch по state.plan/steps) |
| tool_call_started/finished | TOOL_CALL_START / TOOL_CALL_ARGS / TOOL_CALL_END |
| agent_thought | CUSTOM ("thought") — UI рендерит в ленте шагов |
| answer_delta | TEXT_MESSAGE_START / CONTENT / END |

**A2A:** воркер публикует AgentCard (skills: `financial_sql_analysis`, `narrative_rag_analysis`); оркестратор шлёт задачу как A2A message с payload `WorkerTask`, получает `WorkerResult`. Авторизация: bearer `A2A_TOKEN`. Интерфейс в коде — `WorkerClient` (реализации: `LocalWorkerClient`, `A2AWorkerClient`) — оркестратор не знает, локальный воркер или удалённый.

**Синтезатор** обязан вернуть, помимо текста: `key_values: [{"label","value","unit","source_step"}]` (машиночитаемые ключевые цифры — используется eval) и `citations: [...]`.

---

## 11. Model Router

Классы задач: `route | extract | plan | reason | synthesize | guard | summarize_event | judge`.

`config/router.yaml` (пример-дефолт):

```yaml
tiers:
  local:        {provider: ollama,   model: "${LOCAL_MODEL}",  timeout_s: 60}
  cloud_cheap:  {provider: deepseek, model: deepseek-v4-flash, thinking: disabled, timeout_s: 60}
  cloud_strong: {provider: deepseek, model: deepseek-v4-pro,   thinking: enabled,  timeout_s: 120}  # пины T-002 (2026-07-10); Q-01: единственный облачный провайдер
policy:                      # порядок = основная → fallback-цепочка
  route:       [local, cloud_cheap]
  extract:     [local, cloud_cheap]
  guard:       [local, cloud_cheap]
  summarize_event: [cloud_cheap, local]
  plan:        [cloud_strong, cloud_cheap]
  reason:      [cloud_cheap, cloud_strong]
  synthesize:  [cloud_strong, cloud_cheap]
  judge:       [cloud_strong]
retries: {attempts: 2, backoff: exponential}
```

Семантика: недоступность/таймаут/429 → следующий tier в списке, `fallback_used=true` в `llm_calls`. Тиры с отсутствующим ключом выкидываются из цепочки на старте (warning). Цены — `config/prices.yaml` (`model: {in_per_1m, out_per_1m}`), стоимость считается на каждый вызов. Каждый LLM-вызов в системе идёт **только** через роутер.

Пины DeepSeek (T-002, 2026-07-10, источник api-docs.deepseek.com): thinking-режим переключается параметром запроса `thinking: {"type": "enabled"|"disabled"}` (дефолт enabled; дополнительно `reasoning_effort: high|max`); обе V4-модели поддерживают tool calls и `response_format: json_object`; контекст 1M, max output 384K; concurrency-лимиты flash 2500 / pro 500. Цены для `config/prices.yaml`: v4-flash **$0.14 / $0.28** за 1M in/out (cache-hit in $0.0028); v4-pro **$0.435 / $0.87** (cache-hit $0.003625). Обоснование strong=v4-pro (а не alias-преемника reasoner→v4-flash-thinking): флагманское качество для plan/synthesize/judge при цене **ниже** одобренной в Q-01 (reasoner стоил $0.55/$2.19); откат на all-flash — одна строка конфига. ⚠️ Старые имена deepseek-chat/deepseek-reasoner deprecated 2026-07-24 — в коде не использовать.

## 12. Промпты и guardrail

- Промпты — файлы в `prompts/` (`planner.md`, `worker_react.md`, `synthesizer.md`, `guard.md`, ...), с заголовком: id, task_class, версия. Изменение промпта = изменение версии (важно для интерпретации eval-трендов).
- Обязательные блоки каждого ответогенерирующего промпта: (1) роль «аналитик, не советник»; (2) правило цитат: каждое нарративное утверждение — с citation, при отсутствии данных — явное «в загруженных данных нет ответа»; (3) язык ответа = язык вопроса.
- Каноническая non-advice формулировка (включать дословно): «Ты — финансовый аналитик, а не советник. Не давай инвестиционных рекомендаций (покупать/продавать/держать), целевых цен и советов по распределению капитала. Отвечай фактами, расчётами, сравнениями и объяснениями с указанием источников.»
- Guardrail (после синтеза): (а) regex-паттерны RU/EN («покупа(й|ть)», «прода(й|вать)», «держать», «рекоменду. (акци|купить|продать)», «целевая цена», «buy/sell/hold», «price target», ...) → (б) LLM-классификатор `guard` возвращает `{advice: bool, spans: [...]}`. Срабатывание → один ре-синтез с запретом; повторное → блок с шаблонным аналитическим ответом. Решение пишется в TraceEvent `guardrail`.

## 13. Бюджеты и ошибки

Профили `config/budgets.yaml`:

| Параметр | dev | demo |
|---|---|---|
| max_plan_steps | 8 | 6 |
| max_replans | 2 | 1 |
| worker max_iterations | 8 | 6 |
| max_cost_usd / run | 0.50 | 0.15 |
| max_tokens / run | 200k | 80k |
| wall_clock / run | 300s | 180s |
| runs / час / IP | — | 10 |
| дневной cap стоимости системы | — | задаётся env, при достижении — вежливый отказ |

Превышение бюджета — не ошибка: оркестратор переходит к синтезу частичного ответа с пометкой. Таксономия ошибок (`common/errors.py`): `ToolError(retryable)`, `SourceUnavailableError`, `BudgetExceededError`, `GuardrailViolation`, `PlanningError`. Ошибки инструментов доходят до агента как observation (§9), а не рушат ран.

## 14. Тестирование и CI-профили

- **unit** — без сети; HTTP источников — записанные фикстуры (respx); LLM — фейковые провайдеры.
- **contract** — JSON-схемы инструментов/DTO стабильны (снапшот-тесты), MCP-обёртка идентична lib-версии.
- **integration** — против docker compose (метка `slow`), включая `scripts/smoke_test.py`.
- **eval** — отдельный харнесс (не pytest), профили: `ci` (≈10 кейсов, дешёвые модели, бюджет ≤ $0.30) и `full` (весь golden, nightly/manual).
- CI (GitHub Actions): PR → lint + mypy + unit + contract + build; nightly/manual → integration + eval-ci с порогами из `config/eval-thresholds.yaml`; регрессия → красный.
- Ориентир покрытия ядра (adapters/tools/router/orchestrator): ≥70% unit; главное — покрыты ветки ошибок и идемпотентность.

## 15. Общий Definition of Done задачи бэклога

1. Код + типы + unit-тесты новой логики; lint/mypy — 0 ошибок; CI зелёный.
2. Конфиг — через `common/config`, логи — через `common/logging`, секреты — env.
3. Затронутые контракты отражены здесь (CONTRACTS.md) и в `.env.example`.
4. Частные критерии выполнения задачи проверены буквально (команды из критериев выполняются).
5. Изменение видно работающим: для фич — воспроизводимая команда/сценарий из Makefile.
