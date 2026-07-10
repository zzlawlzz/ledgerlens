"""Non-advice guardrail tests (T-022): >=15 advice phrases, >=10 legit ones.

The advice set must be caught by the REGEX stage alone (deterministic);
the legit set must pass the full two-stage check with a fake guard LLM.
These phrases seed the golden set in T-028.
"""

from __future__ import annotations

from typing import Any

import pytest

from orchestrator.guardrail import (
    GuardVerdict,
    build_refusal,
    check_advice,
    disclaimer_for,
    find_advice_spans,
    is_russian,
)

ADVICE_PHRASES = [
    # RU
    "Вам стоит купить акции Apple прямо сейчас.",
    "Рекомендую продать NVIDIA до отчёта.",
    "Покупайте на просадке, это шанс.",
    "Советую держать Microsoft до конца года.",
    "Лучше продать весь пакет сегодня.",
    "Пора покупать: мультипликаторы привлекательные.",
    "Стоит инвестировать в этот сектор половину депозита.",
    "Целевая цена по акции — 250 долларов.",
    "Держать до таргета 300.",
    "Выделите 20% портфеля под золото.",
    # EN
    "You should buy Apple stock immediately.",
    "I recommend to sell before earnings.",
    "Hold the stock until the split.",
    "Strong buy rating on NVDA.",
    "Our price target is $250.",
    "It is a great time to buy the dip.",
    "Allocate 15% of your portfolio to bonds.",
    "Go long on semiconductors.",
]

LEGIT_PHRASES = [
    # RU — аналитика без советов
    "Выручка Apple выросла на 6.4% год к году [AAPL 10-K 2025-09-27].",
    "Компания продаёт устройства и сервисы в 175 странах.",
    "В отчёте раскрыты риски цепочки поставок и концентрации производства.",
    "Маржинальность NVIDIA выше, чем у AMD, на 12 п.п.",
    "Свободный денежный поток сократился из-за роста капзатрат.",
    # EN — analytics without advice
    "Revenue grew 65.5% year over year, driven by data center demand.",
    "The company repurchased $90 billion of its own shares in FY2025.",
    "Management discusses tariff risks in the 10-K risk factors section.",
    "Gross margin declined 2 percentage points due to component costs.",
    "Apple's fiscal 2025 revenue was $416.161 billion [AAPL 10-K 2025-09-27].",
    "The filing notes that the company sells products through retail and online stores.",
]


class FakeGuardRouter:
    """Guard LLM stub: configurable verdict, records calls."""

    def __init__(self, advice: bool = False, spans: list[str] | None = None) -> None:
        self.verdict = GuardVerdict(advice=advice, spans=spans or [])
        self.calls = 0

    async def chat_structured(self, task_class: str, messages: Any, schema: Any) -> Any:
        assert task_class == "guard"
        self.calls += 1
        return self.verdict


@pytest.mark.parametrize("phrase", ADVICE_PHRASES)
def test_advice_phrases_caught_by_regex(phrase: str) -> None:
    assert find_advice_spans(phrase), f"regex must catch: {phrase!r}"


@pytest.mark.parametrize("phrase", LEGIT_PHRASES)
async def test_legit_phrases_pass_two_stage(phrase: str) -> None:
    verdict = await check_advice(phrase, FakeGuardRouter(advice=False))
    assert verdict.advice is False, f"false positive on: {phrase!r}"


async def test_llm_stage_catches_paraphrase_missed_by_regex() -> None:
    paraphrase = "Adding this name to a portfolio at current levels seems wise."
    assert not find_advice_spans(paraphrase)  # regex alone misses it
    router = FakeGuardRouter(advice=True, spans=["seems wise"])
    verdict = await check_advice(paraphrase, router)
    assert verdict.advice is True
    assert "seems wise" in verdict.spans


async def test_guard_llm_failure_degrades_to_regex() -> None:
    class BrokenRouter:
        async def chat_structured(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("guard tier down")

    caught = await check_advice("You should buy the stock.", BrokenRouter())
    assert caught.advice is True  # regex stage still works
    passed = await check_advice("Revenue grew 10%.", BrokenRouter())
    assert passed.advice is False


def test_language_helpers_and_refusal() -> None:
    assert is_russian("Стоит ли покупать акции?") is True
    assert is_russian("Should I buy?") is False
    assert "аналитика" in disclaimer_for("Какая выручка у Apple?")
    assert "analytics" in disclaimer_for("What is Apple revenue?")
    refusal = build_refusal("Стоит ли покупать Apple?", "- выручка: 416.2 млрд USD")
    assert "не даю инвестиционных рекомендаций" in refusal
    assert "416.2" in refusal
    empty = build_refusal("Should I buy?", "")
    assert "no data collected" in empty
