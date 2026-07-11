# LedgerLens — Multi-Agent Financial Analysis Platform

[![CI](https://github.com/zzlawlzz/ledgerlens/actions/workflows/ci.yml/badge.svg)](https://github.com/zzlawlzz/ledgerlens/actions/workflows/ci.yml)
[![eval](https://github.com/zzlawlzz/ledgerlens/actions/workflows/eval.yml/badge.svg)](https://github.com/zzlawlzz/ledgerlens/actions/workflows/eval.yml)

Self-hostable мультиагентная платформа финансового анализа: вопрос на естественном языке → многошаговый план → агенты (LangGraph, ReAct) работают с фактами отчётности (SQL) и нарративом (RAG) → ответ с числами, динамикой и цитатами на первоисточник, со стримом хода рассуждения в UI (AG-UI). Инструменты — MCP-серверы, межагентное взаимодействие — A2A (в т.ч. между нодами), tiered-роутинг LLM (локальный CPU-инференс + cloud API), eval в CI, наблюдаемость в Grafana.

> ⚠️ Система предоставляет аналитику по публичной отчётности и **не даёт инвестиционных рекомендаций**.

**Статус:** MVP (гейт G2). Полный стек в docker compose: оркестратор Plan-and-Execute, A2A-воркер в отдельном контейнере, RAG с цитатами, guardrail non-advice, AG-UI-стрим, React-UI.

## Карта документов

| Документ | Что в нём |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Зафиксированные архитектурные решения: слои, компоненты, источники данных, протоколы, топология |
| [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | Фазовый план (4 недели), принципы, ADR, риски, Definition of Done |
| [CONTRACTS.md](CONTRACTS.md) | Технические контракты: стек, DDL, словарь метрик, схемы инструментов, конвенции, бюджеты |
| [BACKLOG.md](BACKLOG.md) | Бэклог разработчика: задачи T-001…T-040 по приоритету, с ТЗ и критериями выполнения |
| [OWNER_QUESTIONS.md](OWNER_QUESTIONS.md) | Вопросы владельцу проекта (решения, блокирующие отдельные задачи) |

## Быстрый старт (MVP: полный стек + UI)

```bash
cp .env.example .env  # заполнить DEEPSEEK_API_KEY, POSTGRES_PASSWORD, POSTGRES_RO_PASSWORD, A2A_TOKEN
make demo             # compose up (6 сервисов) + ingest при пустой БД + смоук
# UI: http://localhost:3000
```

`make demo` поднимает postgres, qdrant, ollama (профиль local), оркестратор,
A2A-воркер и web (nginx); при пустой БД загружает 3 тикера из SEC EDGAR
(с диск-кэшем) вместе с эмбеддингами и прогоняет смоук: числовые вопросы,
нарративный вопрос с цитатами, протокол AG-UI, доступность UI.

Разовый вопрос вручную (debug-эндпоинт):

```bash
curl -N -X POST http://localhost:8000/api/chat \
     -H "Content-Type: application/json" \
     -d '{"question": "What was the revenue of AAPL in its latest fiscal year?"}'
```

Ответ стримится как SSE-поток трейс-событий (план, шаги, вызовы инструментов,
guardrail) и завершается `run_finished` с ответом, key_values и цитатами.
Фронт использует `POST /agui` (протокол AG-UI). Сценарий самокоррекции — см.
[demo/self_correction.md](demo/self_correction.md).

## Наблюдаемость и качество

- **Grafana** (T-034): дашборды на `http://localhost:3001` (анонимный viewer) —
  Operations (латентность, стоимость, local-vs-cloud, ошибки), Session
  drill-down по `run_id`, Quality (метрики eval по прогонам). Источник —
  таблицы `runs/steps/llm_calls/tool_calls/eval_*` через read-only роль
  `grafana_ro`.
- **Eval** (T-028/T-029/T-030): golden-набор из 41 кейса (`eval/golden/`),
  раннер `uv run python -m eval.run --profile ci|full`, гейт в CI
  (`.github/workflows/eval.yml`) с порогами `config/eval-thresholds.yaml`.
- **MCP-инструменты** (T-027): sql/rag/enrich — отдельные MCP-сервисы;
  воркер подключается как MCP-клиент (в lib-режиме для юнит-тестов).

## Известные ограничения

- **Одна вычислительная нода**: воркер общается по A2A, но живёт в том же
  compose; вторая нода (VPS) — T-031 (гейт G3, разблокирована).
- **faithfulness**: метрика groundedness временно `non_blocking` в
  eval-гейте, пока не закрыт T-041 (синтез не должен дополнять
  retrieved-контекст) — считается и репортится, но не роняет CI.
- **Цены (T-033)**: инструмент готов, но Stooq блокирует серверные клиенты
  бот-детекцией — нужен другой EOD-провайдер (см. Q-19). Основной анализ
  от этого не страдает (честная деградация).
- **Скорость на слабом CPU**: локальный тир (classify/extract/guard) на
  dev-машине медленный; мультишаговые вопросы занимают минуты. Целевое
  железо — домашняя нода (бенчмарк T-037).
- **Данные**: демо-набор EDGAR (10 тикеров × 3 года); RU-режим (MOEX,
  T-032) — цены загружаются, вопрос-по-ценам в доводке.

## Источники данных и лицензии

- **SEC EDGAR** — бесплатный доступ; запросы уходят с обязательным `User-Agent` и rate-limit согласно правилам SEC.
- **MOEX ISS** (RU-режим) — данные ИСС Московской биржи используются в этом проекте **только в ознакомительных/демонстрационных целях**; доступ — к бесплатным задержанным данным, без API-ключа. Коммерческое использование, перепродажа или иное извлечение прибыли из данных ISS требуют отдельного соглашения с Московской биржей. Клиент кэширует ответы (`data/cache/moex`) и держит паузу между запросами; ingest RU-источников выполняется с ноды с чистым доступом (см. [docs/moex-ingest.md](docs/moex-ingest.md)).
- Подробнее — §5 [ARCHITECTURE.md](ARCHITECTURE.md).
