"""Non-advice guardrail (T-022; CONTRACTS §12, ARCHITECTURE §1.3).

Two-stage check of the synthesized answer:
(a) regex patterns (RU+EN, config/guardrail_patterns.yaml) — deterministic
    and free;
(b) LLM classifier (task_class=guard, local-first) — catches paraphrases the
    regexes miss.

Policy: advice found -> one re-synthesis with an explicit prohibition and the
offending spans; still advice -> template refusal built from the collected
facts (no LLM). Every answer gets the analytics disclaimer appended.
"""

from __future__ import annotations

import re
from functools import cache
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from common.config import load_yaml_config
from common.logging import get_logger

MAX_SPANS = 8
_CYRILLIC = re.compile("[а-яА-ЯёЁ]")

DISCLAIMER_EN = "This is financial analytics, not investment advice."
DISCLAIMER_RU = "Это финансовая аналитика, а не инвестиционная рекомендация."

REFUSAL_RU = (
    "Я аналитический инструмент и не даю инвестиционных рекомендаций "
    "(покупать/продавать/держать, целевые цены, распределение капитала). "
    "Вместо этого — факты по вашему вопросу:\n\n{facts}\n\n"
    "Решение остаётся за вами; при необходимости могу сравнить метрики, "
    "динамику и раскрытые риски компаний."
)
REFUSAL_EN = (
    "I am an analytics tool and do not give investment advice "
    "(buy/sell/hold calls, price targets, capital allocation). "
    "Here are the facts relevant to your question instead:\n\n{facts}\n\n"
    "The decision is yours; I can compare metrics, trends and disclosed "
    "risks if that helps."
)


class GuardVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    advice: bool
    spans: list[str] = Field(default_factory=list)


@cache
def _compiled_patterns() -> list[re.Pattern[str]]:
    raw = load_yaml_config("guardrail_patterns")["patterns"]
    return [re.compile(pattern) for pattern in raw]


def find_advice_spans(text: str) -> list[str]:
    """Stage (a): regex hits as exact text fragments (deduplicated)."""
    spans: list[str] = []
    for pattern in _compiled_patterns():
        for match in pattern.finditer(text):
            fragment = match.group(0).strip()
            if fragment and fragment not in spans:
                spans.append(fragment)
            if len(spans) >= MAX_SPANS:
                return spans
    return spans


def is_russian(text: str) -> bool:
    return bool(_CYRILLIC.search(text))


def disclaimer_for(question: str) -> str:
    return DISCLAIMER_RU if is_russian(question) else DISCLAIMER_EN


def build_refusal(question: str, facts_digest: str) -> str:
    template = REFUSAL_RU if is_russian(question) else REFUSAL_EN
    facts = facts_digest.strip() or (
        "— (данных для ответа недостаточно)" if is_russian(question) else "— (no data collected)"
    )
    return template.format(facts=facts)


async def check_advice(text: str, router: Any) -> GuardVerdict:
    """Two-stage verdict; the LLM stage degrades gracefully to regex-only."""
    spans = find_advice_spans(text)
    from pathlib import Path

    prompt_path = Path(__file__).resolve().parent.parent / "prompts" / "guard.md"
    prompt = prompt_path.read_text(encoding="utf-8")
    prompt = prompt.partition("---")[2].partition("---")[2].strip()
    try:
        llm_verdict = await router.chat_structured(
            "guard",
            [("system", prompt), ("user", text[:6000])],
            GuardVerdict,
        )
    except Exception as exc:  # noqa: BLE001 — guard LLM down: regex stage still stands
        get_logger(node="guardrail").warning("guard_llm_failed", error=str(exc)[:200])
        llm_verdict = GuardVerdict(advice=False)
    combined = list(spans)
    for span in llm_verdict.spans[:MAX_SPANS]:
        if span not in combined:
            combined.append(span)
    return GuardVerdict(advice=bool(spans) or llm_verdict.advice, spans=combined[:MAX_SPANS])
