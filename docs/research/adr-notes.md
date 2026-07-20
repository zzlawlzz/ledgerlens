# T-002 · Актуализация [OPEN]-решений — заметка с источниками

**Дата проверки всех пунктов: 2026-07-10.** Исполнитель: агент-разработчик. Формат: пункт ТЗ → факт → следствие → источники.

## 1. LangGraph

- Актуальная линия — **v1 (1.2.x)**; запинено `langgraph==1.2.8` (May 2026). `create_react_agent` живёт в `langgraph-prebuilt` (ставится с langgraph). PostgresSaver — `langgraph-checkpoint-postgres` 3.1 — рекомендованный прод-чекпоинтер.
- Источники: [What's new in LangGraph v1](https://docs.langchain.com/oss/python/releases/langgraph-v1), [create_react_agent reference](https://reference.langchain.com/python/langgraph.prebuilt/chat_agent_executor/create_react_agent), [langgraph GitHub](https://github.com/langchain-ai/langgraph), [langgraph-prebuilt PyPI](https://pypi.org/project/langgraph-prebuilt/).

## 2. MCP

- Официальный python-SDK `mcp` (FastMCP включён) — запинен **1.28.1**; транспорт streamable-HTTP поддержан. `langchain-mcp-adapters` **0.3.0** — рабочий мост MCP↔LangGraph-агенты.
- Источники: [LangChain MCP docs](https://docs.langchain.com/oss/python/langchain/mcp), [langchain-mcp-adapters GitHub](https://github.com/langchain-ai/langchain-mcp-adapters), [PyPI](https://pypi.org/project/langchain-mcp-adapters/).

## 3. A2A

- Пакет `a2a-sdk` (официальный, a2aproject) — запинен **1.1.0**. Протокол **A2A v1.0** (12 марта 2026, Linux Foundation); SDK реализует спецификацию 1.0 с compat-режимом 0.3. Новое в 1.0: **подписанные AgentCard** (криптоподпись домена издателя) — учесть в T-021/T-031 (наш bearer `A2A_TOKEN` остаётся, подпись карточки — бонус).
- Источники: [a2a-python GitHub](https://github.com/a2aproject/a2a-python), [a2a-sdk PyPI](https://pypi.org/project/a2a-sdk/).

## 4. AG-UI

- Серверная python-библиотека — `ag-ui-protocol` (PyPI), запинена **0.1.19**; клиент — `@ag-ui/client` (TS). Имена событий EventType подтверждены: RUN_STARTED/FINISHED/ERROR, STATE_SNAPSHOT, STATE_DELTA (JSON Patch RFC 6902), TOOL_CALL_START/ARGS/END, TEXT_MESSAGE_START/CONTENT/END, CUSTOM — маппинг CONTRACTS §10 корректен без изменений. SDK включает SSE-энкодер pydantic-событий.
- Источники: [docs.ag-ui.com events](https://docs.ag-ui.com/sdk/python/core/events), [ag-ui-protocol PyPI](https://pypi.org/project/ag-ui-protocol/).

## 5. Локальные CPU-модели (ADR-3, дефолт до T-037)

- Ollama актуален (v0.30.x, июнь 2026). Линейка Qwen обновилась: **qwen3.5** — sparse-MoE + Gated Delta Networks, 256K ctx, мультимодальная; варианты 0.8b/2b/4b/9b/27b/35b/122b. **Дефолт-кандидат сменён: qwen3:30b-a3b → qwen3.5:27b** (17 ГБ Q4, влезает с запасом); лёгкий fallback — qwen3.5:4b (3.4 ГБ).
- Кандидаты CPU-бенчмарка T-037 на локальной ноде (после вывода EPYC-ноды 2026-07 это воркстейшн: Ryzen 9 5900X, 12C/24T, 64 ГБ): qwen3.5:27b, qwen3:30b-a3b (прежний дефолт, для сравнения). qwen3.5:122b (81 ГБ) в 64 ГБ RAM **не влезает** — снят с кандидатов.
- Источники: [ollama.com/library/qwen3.5](https://ollama.com/library/qwen3.5), [ollama.com/library/qwen3](https://ollama.com/library/qwen3), [Ollama June 2026 update](https://www.promptquorum.com/local-llms/top-open-source-models-ollama), [Best Ollama Models 2026](https://www.morphllm.com/best-ollama-models).

## 5-бис. Доступность внешних API из РФ

Живая проверка 2026-07-10 **с dev-машины (РФ)** — все цели доступны:

| Хост | Результат |
|---|---|
| data.sec.gov (companyfacts AAPL) | HTTP 200, 3.7 МБ за 0.64 с |
| www.sec.gov (company_tickers.json) | HTTP 200, 0.8 МБ за 0.27 с |
| api.deepseek.com | доступен: 401 без ключа; с ключом `/models` отдаёт список |
| iss.moex.com | HTTP 200 |
| stooq.com (EOD CSV) | HTTP 200 |

Следствие: `EDGAR_PROXY_URL`, вероятно, **не понадобится**, но поддержка в T-008 остаётся (сеть дом-ноды может отличаться от dev-машины; повторить проверку с неё при развёртывании T-017/T-031). ⚠️ PowerShell `Invoke-WebRequest` отвергает наш EDGAR User-Agent валидацией заголовка — с httpx такой проблемы нет, но помнить при отладке на Windows.

## 6. Эмбеддинги (ADR-6 — закрыт)

- **bge-m3 остаётся верным выбором**: dense 1024d + learned-sparse (lexical weights) + multi-vector в одной модели; 100+ языков (закрывает RU-режим), 8192 токена. fastembed (запинен **0.8.0**) поддерживает bge-m3, sparse BM25 и reranker **bge-reranker-v2-m3** (ONNX/CPU) — весь RAG-стек на одной библиотеке.
- Источники: [BAAI/bge-m3 HF](https://huggingface.co/BAAI/bge-m3), [bge-model.com](https://bge-model.com/bge/bge_m3.html), [обзор 2026](https://pristren.com/blog/bge-m3-embeddings-multilingual/).

## 7. DeepSeek (ADR-7 — пины)

- **Критично: `deepseek-chat` и `deepseek-reasoner` deprecated 2026-07-24** (через 2 недели). Сейчас это алиасы deepseek-v4-flash (non-thinking / thinking). Живой `/models` с нашим ключом отдаёт: `deepseek-v4-flash`, `deepseek-v4-pro`.
- Пины: **cheap = deepseek-v4-flash** (`thinking: {"type":"disabled"}`), **strong/judge = deepseek-v4-pro** (thinking enabled, дефолт). Обоснование выбора pro вместо alias-преемника (flash-thinking): флагманское качество для plan/synthesize/judge при цене ниже одобренной в Q-01 ($0.435/$0.87 против $0.55/$2.19 у reasoner на момент решения). Откат на all-flash — одна строка router.yaml.
- Параметры: `thinking.type: enabled|disabled`; `reasoning_effort: high|max`; tool calls и `response_format: json_object` — у обеих моделей; контекст 1M, max output 384K; concurrency flash 2500 / pro 500. Цены (постоянные с 2026-05-22): flash $0.14/$0.28 за 1M in/out (cache-hit in $0.0028), pro $0.435/$0.87 (cache-hit $0.003625).
- Риск одно-провайдерности (Q-01) сохраняется: при недоступности DeepSeek деградация только на локальную модель; рекомендация владельцу добавить второй провайдер остаётся открытой.
- Источники: [Models & Pricing](https://api-docs.deepseek.com/quick_start/pricing/), [Create Chat Completion](https://api-docs.deepseek.com/api/create-chat-completion), живой запрос `/models` (2026-07-10).

## 8. RAGAS + DeepEval

- Оба живы и совместимы; сложившийся паттерн 2026 — **RAGAS как метрики RAG, DeepEval как pytest-совместимый CI-раннер** (у DeepEval есть обёртка RAGAS-метрик). Подход T-029 подтверждён; версии пинуются при добавлении зависимостей в T-029 (в ядро не тянем — тяжёлые).
- Источники: [deepeval.com/docs/metrics-ragas](https://deepeval.com/docs/metrics-ragas), [DeepEval vs Ragas](https://deepeval.com/blog/deepeval-vs-ragas).

## 9. qdrant-client: hybrid + RRF

- Запинен **1.18.0**. Query API: `client.query_points(prefetch=[Prefetch(sparse), Prefetch(dense)], query=RRF-fusion)` — server-side fusion RRF/DBSF подтверждён. Примечание: в свежем API фьюжн задаётся `models.RrfQuery(rrf=models.Rrf())` (форма изменилась со старой `FusionQuery(fusion=Fusion.RRF)`) — сверить с сигнатурой 1.18 при реализации T-019.
- Источники: [Hybrid Queries](https://qdrant.tech/documentation/search/hybrid-queries/), [Hybrid Search Revamped](https://qdrant.tech/articles/hybrid-search/).

## 10. Провайдер EOD-цен (ADR-8 — закрыт)

- **Дефолт: Stooq** — EOD CSV без ключа и регистрации; живая проверка с RU IP: HTTP 200, корректный CSV (`https://stooq.com/q/d/l/?s=aapl.us&i=d&d1=...&d2=...`). Лимиты мягкие (при злоупотреблении — дневной бан по IP) → в T-033 обязателен кэш в `prices` + rate-limiter + суточный счётчик (и так в ТЗ).
- Альтернатива: Alpha Vantage (ключ, free-tier ~25 запросов/день) — заметно хуже для нашего паттерна; не выбрана.

## Сводка изменений по итогам T-002

- CONTRACTS §1 — все `[verify:T-002]` сняты, версии вписаны; §10 — маппинг AG-UI подтверждён; §11 — router.yaml на V4-модели + блок пинов/цен.
- IMPLEMENTATION_PLAN §3 — ADR-3 (дефолт qwen3.5:27b, финал T-037), ADR-6 (закрыт: bge-m3), ADR-7 (пины V4), ADR-8 (закрыт: Stooq).
- `pyproject.toml`/`uv.lock` — 17 runtime-зависимостей запинены; `import langgraph, mcp` и весь стек импортируются.
- `.env.example` — `LOCAL_MODEL=qwen3.5:27b`.

## Поправка ADR-6 (T-018, 2026-07-10, живая проверка)

Вывод исследования T-002 о поддержке bge-m3 в fastembed **не подтвердился**:
`TextEmbedding('BAAI/bge-m3')` в fastembed 0.8.0 бросает `ValueError: Model ... is not
supported` (проверено запуском на этой машине); `BAAI/bge-reranker-v2-m3` отсутствует в
`TextCrossEncoder.list_supported_models()`. Урок: перечень моделей проверять только через
`list_supported_models()` установленной версии, не по статьям/README.

Замена (те же требования: multilingual для RU-режима, dense 1024d, ONNX/CPU):
- dense: **intfloat/multilingual-e5-large** (1024d, 2.2 ГБ; префиксы query:/passage: —
  через `query_embed`/`passage_embed` fastembed);
- reranker: **jinaai/jina-reranker-v2-base-multilingual** (1.1 ГБ);
- sparse: Qdrant/bm25 (без изменений).
