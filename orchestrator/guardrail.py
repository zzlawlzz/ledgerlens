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


ADVICE_QUESTION_REFUSAL_RU = (
    "Я аналитический инструмент по публичной отчётности компаний и не даю "
    "инвестиционных рекомендаций — что покупать, продавать или держать, куда "
    "вложить деньги или как «разбогатеть». Такие решения зависят от ваших "
    "целей и допустимого риска и остаются за вами.\n\n"
    "Зато я умею работать с фактами: выручка, прибыль и денежный поток компании "
    "по годам, динамика и сравнение метрик, раскрытые в 10-K риски — с цитатами "
    "на первоисточник (SEC/MOEX). Например: «Сравни динамику выручки Apple и "
    "Microsoft за 3 года» или «Какие риски цепочки поставок Apple раскрывает в 10-K»."
)
ADVICE_QUESTION_REFUSAL_EN = (
    "I am an analytics tool over public company filings and I don't give "
    "investment advice — what to purchase, offload or keep, where to put your "
    "money, or how to build wealth. Those choices depend on your goals and risk "
    "tolerance and stay with you.\n\n"
    "What I can do is the facts: a company's revenue, profit and cash flow over "
    "the years, trends and metric comparisons, disclosed 10-K risks — with "
    'citations to the primary source (SEC/MOEX). For example: "Compare Apple '
    'and Microsoft revenue over 3 years" or "What supply-chain risks does '
    'Apple disclose in its 10-K".'
)


@cache
def _compiled_patterns() -> list[re.Pattern[str]]:
    raw = load_yaml_config("guardrail_patterns")["patterns"]
    return [re.compile(pattern) for pattern in raw]


@cache
def _compiled_question_patterns() -> list[re.Pattern[str]]:
    raw = load_yaml_config("guardrail_patterns").get("question_patterns", [])
    return [re.compile(pattern) for pattern in raw]


def is_advice_question(question: str) -> bool:
    """Input-side check: does the QUESTION itself seek investment advice?

    A cheap, deterministic regex gate (no LLM) run at intake so an open-ended
    advice request ("what should I invest in to get rich?") is declined up front
    instead of driving an expensive, doomed research plan. Scoped to open-ended
    asks; advice about a named company is left to the pipeline. Advice that
    slips through is still caught downstream by ``check_advice`` on the answer.
    """
    return any(pattern.search(question) for pattern in _compiled_question_patterns())


def build_advice_question_refusal(question: str) -> str:
    """Non-advice reply for an advice-seeking question, with the disclaimer."""
    body = ADVICE_QUESTION_REFUSAL_RU if is_russian(question) else ADVICE_QUESTION_REFUSAL_EN
    return f"{body}\n\n_{disclaimer_for(question)}_"


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
