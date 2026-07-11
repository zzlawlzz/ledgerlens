"""Golden case schema (T-028; ARCHITECTURE §3.9).

One case = one question against the FROZEN demo corpus (10 tickers x 3 years;
the data snapshot version is pinned in the header of cases.yaml). The
``expected`` shape depends on the category — the validator enforces it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

GOLDEN_DIR = Path(__file__).resolve().parent
CASES_PATH = GOLDEN_DIR / "cases.yaml"

Category = Literal["numeric_sql", "narrative_rag", "multi_step", "guardrail", "no_data"]


class NumericExpectation(BaseModel):
    """Either an absolute value with tolerance, or a ratio of two values."""

    model_config = ConfigDict(extra="forbid")

    value: float | None = None
    unit: str = "USD"
    tolerance_pct: float = 1.0
    ratio_of: list[float] | None = Field(default=None, min_length=2, max_length=2)

    @model_validator(mode="after")
    def _value_or_ratio(self) -> NumericExpectation:
        if (self.value is None) == (self.ratio_of is None):
            raise ValueError("exactly one of value/ratio_of is required")
        return self


class NarrativeExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    must_contain: list[str] = Field(min_length=1)  # case-insensitive substrings
    citation_required: bool = True


class MultiStepExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_plan_steps: int = 2
    key_numbers: list[float] = Field(default_factory=list)  # each must appear (6 sig. digits)
    must_contain: list[str] = Field(default_factory=list)
    citation_required: bool = False


class GuardrailExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refusal_style: bool = True  # no buy/sell/hold advice in the answer
    must_not_contain: list[str] = Field(default_factory=list)


class NoDataExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    must_admit_gap: bool = True  # answer explicitly states the data is absent
    must_not_invent: list[str] = Field(default_factory=list)  # forbidden invented figures


_EXPECTED_TYPES: dict[str, type[BaseModel]] = {
    "numeric_sql": NumericExpectation,
    "narrative_rag": NarrativeExpectation,
    "multi_step": MultiStepExpectation,
    "guardrail": GuardrailExpectation,
    "no_data": NoDataExpectation,
}


class GoldenCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9_]+$")
    category: Category
    question: str = Field(min_length=8)
    expected: dict[str, Any]
    tags: list[Literal["ci", "full"]] = Field(default_factory=lambda: ["full"])  # type: ignore[arg-type]
    difficulty: Literal["easy", "medium", "hard"] = "medium"

    @model_validator(mode="after")
    def _expected_matches_category(self) -> GoldenCase:
        _EXPECTED_TYPES[self.category].model_validate(self.expected)
        return self

    def typed_expected(self) -> BaseModel:
        return _EXPECTED_TYPES[self.category].model_validate(self.expected)


class GoldenSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corpus_snapshot: str  # e.g. "edgar-10tickers-3y @ 2026-07-11"
    cases: list[GoldenCase] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> GoldenSet:
        ids = [case.id for case in self.cases]
        duplicates = {i for i in ids if ids.count(i) > 1}
        if duplicates:
            raise ValueError(f"duplicate case ids: {sorted(duplicates)}")
        return self


def load_golden(path: Path | None = None) -> GoldenSet:
    raw = yaml.safe_load((path or CASES_PATH).read_text(encoding="utf-8"))
    return GoldenSet.model_validate(raw)
