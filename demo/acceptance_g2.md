# Протокол приёмки фазы 2 (гейт G2, T-026)

Дата: 2026-07-11. Машина: dev-ноутбук владельца (Windows 11, Docker Desktop,
WSL2). Полный лог смоука — вывод `scripts/smoke_test.py` ниже по тексту.

## Условия «чистой машины»

Второй машины/VM в распоряжении не было (отметить владельцу — критерий
формально требует вторую машину). Эмуляция: `docker compose down` + удаление
data-volumes (`platform_pgdata`, `platform_qdrant-data`) → `make demo` с нуля:
миграции на пустом Postgres, ingest+embed трёх тикеров из EDGAR (диск-кэш),
пустой Qdrant → 305 чанков. Volume `ollama-models` сохранён (кэш весов
qwen3.5:4b, 3.4 ГБ — на истинно чистой машине докачался бы `ollama-pull`-джобом
идемпотентно).

## `docker compose ps` — все сервисы healthy

```
platform-app-1      | Up (healthy)
platform-ollama-1   | Up (healthy)
platform-postgres-1 | Up (healthy)
platform-qdrant-1   | Up (healthy)
platform-web-1      | Up (healthy)
platform-worker-1   | Up (healthy)
```

## `make demo` / smoke (G2-версия) — зелёный

```
>> What was the revenue of AAPL in its most recent fiscal year?
   status=succeeded; answer='Apple reported revenue of **$416.161 billion** (USD) ...'
>> Compare the latest fiscal-year net income of AAPL and MSFT.
   status=succeeded; answer='Apple's ... **$112.01 billion** - Microsoft (...'
>> How did the revenue of NVDA change over the last 3 fiscal years?
   status=succeeded; answer='NVIDIA's revenue ... grew from **$60.9 billion in FY2024** to ...'
>> What are the main risks AAPL discloses in its latest 10-K?
   status=succeeded; citations=20
>> /agui protocol check
   agui OK
>> web UI OK at http://localhost:3000

SMOKE OK: numeric x3 + narrative + agui + web, all from real data
```

Ответ первого вопроса сверен смоуком с `latest_facts` в БД (точное значение).

## Пять пунктов приёмки фазы 2

1. **Многошаговый вопрос.** Смоук Q2 (сравнение net income двух компаний, план
   по компаниям) + Playwright-смоук T-024: «Compare the revenue dynamics of
   Apple and Microsoft… and name Apple's main risks» — план ≥3 шагов
   (2×SQL+RAG), живые статусы, ответ с цитатами (12.9 мин на dev-железе).
2. **Стрим в UI.** Playwright T-024 (стрим ответа и статусов наблюдался
   селекторами) + скриншоты `demo/screenshots/self_correction_*.png` — панель
   «ход анализа» с планом, tool-calls и мыслями.
3. **Лог роутера: локальное vs API.** `llm_calls` за приёмочный час:

   ```
   provider | task_class | count | avg_ms
   deepseek | reason     |    14 |   1562
   deepseek | extract    |     5 |   1178
   deepseek | route      |     5 |   1073
   deepseek | synthesize |     5 |   9844
   deepseek | guard      |     5 |   1041
   deepseek | plan       |     1 |   8706
   ollama   | extract    |    10 |  25001
   ollama   | guard      |    10 |  25001
   ollama   | route      |    10 |  25001
   ```

   Тиры и фолбэк видны: local-first классы (route/extract/guard) пробуют
   ollama; на dev-CPU под нагрузкой (эмбеддинг e5 на том же CPU) локальные
   вызовы упираются в таймаут 25с и штатно уходят в deepseek — ран не
   страдает. На целевой ноде (2×EPYC) распределение сместится к ollama;
   финальный выбор модели — бенчмарк T-037. Тёплая одиночная нагрузка на
   этом же железе: route→ollama за ~15с (протокол T-017).
4. **Guardrail.** В каждом смоук-ране есть TraceEvent `guardrail`; решения в
   `steps` (node='guardrail'): `{"action": "pass", "triggered": false}` для
   аналитических вопросов. Провокация «Стоит ли покупать акции Apple?» —
   live-проверка T-022: план переформулирован в анализ, ответ без
   рекомендаций, дисклеймер на каждом ответе.
5. **Самокоррекция.** `demo/self_correction.md` + скриншоты: воркерская
   цепочка (термин 'profit' → discovery → net_income $112B; стабильность 5/5)
   и оркестраторная (Tesla вне корпуса → no_data → plan_updated → честный
   partial). Live-тесты `tests/integration/test_self_correction_live.py`
   зелёные.

## Ограничения MVP

Зафиксированы в README («Известные ограничения MVP»): одна нода, eval вручную,
без мониторинга, инструменты в lib-режиме (MCP — T-027), медленный локальный
тир на dev-CPU, демо-набор данных.
