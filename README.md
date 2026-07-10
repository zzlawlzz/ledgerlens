# LedgerLens — Multi-Agent Financial Analysis Platform

[![CI](https://github.com/zzlawlzz/ledgerlens/actions/workflows/ci.yml/badge.svg)](https://github.com/zzlawlzz/ledgerlens/actions/workflows/ci.yml)

Self-hostable мультиагентная платформа финансового анализа: вопрос на естественном языке → многошаговый план → агенты (LangGraph, ReAct) работают с фактами отчётности (SQL) и нарративом (RAG) → ответ с числами, динамикой и цитатами на первоисточник, со стримом хода рассуждения в UI (AG-UI). Инструменты — MCP-серверы, межагентное взаимодействие — A2A (в т.ч. между нодами), tiered-роутинг LLM (локальный CPU-инференс + cloud API), eval в CI, наблюдаемость в Grafana.

> ⚠️ Система предоставляет аналитику по публичной отчётности и **не даёт инвестиционных рекомендаций**.

**Статус:** пре-реализация. Архитектура и бэклог зафиксированы, код не начат.

## Карта документов

| Документ | Что в нём |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Зафиксированные архитектурные решения: слои, компоненты, источники данных, протоколы, топология |
| [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md) | Фазовый план (4 недели), принципы, ADR, риски, Definition of Done |
| [CONTRACTS.md](CONTRACTS.md) | Технические контракты: стек, DDL, словарь метрик, схемы инструментов, конвенции, бюджеты |
| [BACKLOG.md](BACKLOG.md) | Бэклог разработчика: задачи T-001…T-040 по приоритету, с ТЗ и критериями выполнения |
| [OWNER_QUESTIONS.md](OWNER_QUESTIONS.md) | Вопросы владельцу проекта (решения, блокирующие отдельные задачи) |

## Быстрый старт (фаза 1: вопрос → ответ на данных EDGAR)

```bash
cp .env.example .env        # заполнить DEEPSEEK_API_KEY, POSTGRES_PASSWORD, POSTGRES_RO_PASSWORD
docker compose up -d --wait # postgres + app (миграции применяются на старте)
make demo-ingest            # загрузка 5 тикеров × 3 года из SEC EDGAR (кэшируется)
make smoke                  # 3 канонических вопроса через /api/chat
```

Разовый вопрос вручную:

```bash
curl -N -X POST http://localhost:8000/api/chat \
     -H "Content-Type: application/json" \
     -d '{"question": "What was the revenue of AAPL in its latest fiscal year?"}'
```

Ответ стримится как SSE-поток трейс-событий (план, вызовы инструментов, мысли агента) и завершается `run_finished` с ответом.

## Источники данных и лицензии

- **SEC EDGAR** — бесплатный доступ; запросы уходят с обязательным `User-Agent` и rate-limit согласно правилам SEC.
- **MOEX ISS** (RU-режим) — данные предоставляются только для ознакомления; использование здесь — демонстрационное/некоммерческое.
- Подробнее — §5 [ARCHITECTURE.md](ARCHITECTURE.md).
