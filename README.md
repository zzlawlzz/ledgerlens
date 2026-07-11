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

## Известные ограничения MVP

- **Одна вычислительная нода**: воркер общается по A2A, но живёт в том же
  compose; вторая нода — T-031.
- **Eval вручную**: golden-набор и eval-гейт в CI появляются в T-028/T-029.
- **Нет мониторинга**: Grafana/алерты — T-032; пока — JSON-логи сервисов
  и таблицы runs/steps/llm_calls/tool_calls в Postgres.
- **Инструменты в lib-режиме**: MCP-серверы инструментов — T-027.
- **Скорость на слабом CPU**: локальный тир (classify/extract/guard) на
  dev-машине медленный; мультишаговые вопросы занимают минуты. Целевое
  железо — домашняя нода (бенчмарк T-037).
- **Данные**: демо-набор EDGAR (5 тикеров × 3 года); RU-режим (MOEX) — фаза 3.

## Источники данных и лицензии

- **SEC EDGAR** — бесплатный доступ; запросы уходят с обязательным `User-Agent` и rate-limit согласно правилам SEC.
- **MOEX ISS** (RU-режим) — данные предоставляются только для ознакомления; использование здесь — демонстрационное/некоммерческое.
- Подробнее — §5 [ARCHITECTURE.md](ARCHITECTURE.md).
