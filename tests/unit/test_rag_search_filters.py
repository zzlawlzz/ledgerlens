"""Unit tests for rag_search pure parts (T-019): filters, validation, fusion."""

from __future__ import annotations

from qdrant_client import models

from tools.rag.core import _build_filter, _fusion_query, _sigmoid, _validate_date


def test_empty_filters_still_exclude_meta_point() -> None:
    built = _build_filter(None)
    assert built.must is None
    must_not = built.must_not
    assert isinstance(must_not, list) and len(must_not) == 1


def test_all_filters_map_to_conditions() -> None:
    built = _build_filter(
        {
            "tickers": ["aapl", "MSFT"],
            "form_types": ["10-K"],
            "sections": ["risk_factors"],
            "period_from": "2023-01-01",
            "period_to": "2025-12-31",
        }
    )
    must = built.must
    assert isinstance(must, list) and len(must) == 4
    ticker_condition = must[0]
    assert isinstance(ticker_condition, models.FieldCondition)
    assert ticker_condition.match is not None
    assert ticker_condition.match.any == ["AAPL", "MSFT"]  # type: ignore[union-attr]


def test_partial_period_filter() -> None:
    built = _build_filter({"period_from": "2024-01-01"})
    must = built.must
    assert isinstance(must, list) and len(must) == 1


def test_date_validation() -> None:
    assert _validate_date(None, "period_from") is None
    assert _validate_date("2024-09-28", "period_from") is None
    problem = _validate_date("september", "period_from")
    assert problem is not None and "ISO date" in problem


def test_fusion_query_matches_installed_client() -> None:
    fusion = _fusion_query()
    expected = models.RrfQuery if hasattr(models, "RrfQuery") else models.FusionQuery
    assert isinstance(fusion, expected)


def test_sigmoid_maps_logits_to_unit_interval() -> None:
    assert 0.0 < _sigmoid(-10) < 0.01
    assert abs(_sigmoid(0) - 0.5) < 1e-9
    assert 0.99 < _sigmoid(10) < 1.0
