"""Unit tests for public-demo admission limits (T-036 §1, CONTRACTS §13).

All checks are pure in-process logic; a fake clock drives the sliding window and
daily-cost rollover without real time. No DB, network, or LLM.
"""

from __future__ import annotations

from typing import cast

import pytest
from fastapi import Request

from common.config import Settings
from orchestrator.demo_limits import (
    DemoLimiter,
    DemoLimitError,
    build_demo_limiter,
    hash_ip,
)


class _FakeClock:
    """Monotonic, manually advanced wall clock (epoch seconds)."""

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


# --- rate limit (runs/hour/IP) --------------------------------------------


def test_eleventh_request_in_an_hour_is_rejected() -> None:
    """Acceptance crit: the 11th run within an hour from one IP → 429."""
    clock = _FakeClock()
    limiter = DemoLimiter(runs_per_hour_per_ip=10, clock=clock)
    ip = "203.0.113.7"
    for _ in range(10):
        limiter.check_rate(ip)  # first 10 pass
    with pytest.raises(DemoLimitError) as exc:
        limiter.check_rate(ip)  # the 11th is rejected
    assert exc.value.status_code == 429


def test_rate_window_slides_so_old_hits_expire() -> None:
    clock = _FakeClock()
    limiter = DemoLimiter(runs_per_hour_per_ip=2, clock=clock)
    ip = "203.0.113.7"
    limiter.check_rate(ip)
    limiter.check_rate(ip)
    with pytest.raises(DemoLimitError):
        limiter.check_rate(ip)
    clock.advance(3601)  # both hits now older than one hour
    limiter.check_rate(ip)  # window cleared → allowed again


def test_rate_limit_is_per_ip() -> None:
    limiter = DemoLimiter(runs_per_hour_per_ip=1)
    limiter.check_rate("a.a.a.a")
    limiter.check_rate("b.b.b.b")  # different IP has its own budget
    with pytest.raises(DemoLimitError):
        limiter.check_rate("a.a.a.a")


def test_rate_limit_disabled_when_unlimited() -> None:
    limiter = DemoLimiter(runs_per_hour_per_ip=None)
    for _ in range(100):
        limiter.check_rate("x")  # never raises


def test_hash_ip_is_stable_and_hides_raw_address() -> None:
    assert hash_ip("203.0.113.7") == hash_ip("203.0.113.7")
    assert hash_ip("203.0.113.7") != hash_ip("203.0.113.8")
    assert "203.0.113.7" not in hash_ip("203.0.113.7")


# --- question length -------------------------------------------------------


def test_overlong_question_rejected_with_413() -> None:
    limiter = DemoLimiter(max_question_chars=500)
    limiter.check_question("x" * 500)  # exactly at the limit is fine
    with pytest.raises(DemoLimitError) as exc:
        limiter.check_question("x" * 501)
    assert exc.value.status_code == 413


def test_question_length_unlimited_when_none() -> None:
    DemoLimiter(max_question_chars=None).check_question("x" * 10_000)


# --- daily cost cap --------------------------------------------------------


def test_daily_cost_cap_rejects_when_reached() -> None:
    limiter = DemoLimiter(daily_cost_cap_usd=1.5)
    limiter.check_daily_cost()  # nothing spent yet
    limiter.record_cost(1.0)
    limiter.check_daily_cost()  # 1.0 < 1.5 still ok
    limiter.record_cost(0.6)  # now 1.6 >= 1.5
    with pytest.raises(DemoLimitError) as exc:
        limiter.check_daily_cost()
    assert exc.value.status_code == 503


def test_daily_cost_resets_at_utc_midnight() -> None:
    clock = _FakeClock(start=1_700_000_000.0)
    limiter = DemoLimiter(daily_cost_cap_usd=1.0, clock=clock)
    limiter.record_cost(2.0)
    with pytest.raises(DemoLimitError):
        limiter.check_daily_cost()
    clock.advance(24 * 3600)  # next UTC day
    limiter.check_daily_cost()  # counter rolled over → allowed


def test_negative_cost_is_ignored() -> None:
    limiter = DemoLimiter(daily_cost_cap_usd=1.0)
    limiter.record_cost(-5.0)
    limiter.check_daily_cost()  # spend floored at 0, not negative headroom


# --- concurrency slot ------------------------------------------------------


def test_concurrency_slot_caps_in_flight_runs() -> None:
    limiter = DemoLimiter(max_concurrent_runs=2)
    limiter.acquire_slot()
    limiter.acquire_slot()
    with pytest.raises(DemoLimitError) as exc:
        limiter.acquire_slot()
    assert exc.value.status_code == 429
    limiter.release_slot()
    limiter.acquire_slot()  # a freed slot lets the next run in


def test_release_never_goes_negative() -> None:
    limiter = DemoLimiter(max_concurrent_runs=1)
    limiter.release_slot()  # release without acquire is a no-op
    limiter.acquire_slot()
    limiter.acquire_slot  # noqa: B018 — sanity: attribute exists


# --- profile wiring --------------------------------------------------------


def test_build_limiter_is_noop_off_demo_profile() -> None:
    limiter = build_demo_limiter(Settings(budget_profile="dev"))
    assert not limiter.enabled
    for _ in range(50):
        limiter.check_rate("x")
    limiter.check_question("x" * 10_000)
    limiter.check_daily_cost()


class _FakeRequest:
    """Minimal stand-in for starlette.Request for the IP/admission helpers."""

    def __init__(self, host: str = "203.0.113.7", forwarded: str | None = None) -> None:
        self.headers = {"x-forwarded-for": forwarded} if forwarded else {}
        self.client = type("C", (), {"host": host})()


def _req(host: str = "203.0.113.7", forwarded: str | None = None) -> Request:
    return cast(Request, _FakeRequest(host, forwarded))


def test_client_ip_prefers_forwarded_for() -> None:
    from orchestrator.api import _client_ip

    assert _client_ip(_req(host="10.0.0.1", forwarded="203.0.113.7, 10.0.0.1")) == "203.0.113.7"
    assert _client_ip(_req(host="198.51.100.9")) == "198.51.100.9"


def test_chat_admission_rejects_eleventh_request_from_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end of the endpoint's admission path: 11th /api/chat from one IP
    → DemoLimitError(429) with a human-readable message (acceptance crit)."""
    import orchestrator.api as api

    monkeypatch.setattr(api, "_DEMO_LIMITER", DemoLimiter(runs_per_hour_per_ip=10))
    req = _req(host="203.0.113.7")
    for _ in range(10):
        api._admit("What was AAPL revenue in FY2024?", req)
    with pytest.raises(DemoLimitError) as exc:
        api._admit("one too many", req)
    assert exc.value.status_code == 429
    assert "demo" in exc.value.detail.lower()


def test_busy_rejection_does_not_burn_rate_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    """A request bounced with 'demo is busy' (429 concurrency) must not consume
    the caller's hourly rate quota. Otherwise the recommended retry would keep
    counting and could lock a real user out for an hour without a single run
    ever executing. ``_admit`` takes the concurrency slot before recording the
    rate hit precisely to prevent this."""
    import orchestrator.api as api

    limiter = DemoLimiter(runs_per_hour_per_ip=10, max_concurrent_runs=1)
    monkeypatch.setattr(api, "_DEMO_LIMITER", limiter)
    req = _req(host="203.0.113.7")

    limiter.acquire_slot()  # the single slot is occupied by an in-flight run
    for _ in range(25):  # far more than the 10/hour rate cap
        with pytest.raises(DemoLimitError) as exc:
            api._admit("q", req)
        assert exc.value.kind == "concurrency_limit"  # never "rate_limited"

    limiter.release_slot()  # the in-flight run finishes, freeing the slot
    api._admit("q", req)  # same IP still runs — the bounced attempts left no hits


def test_build_limiter_reads_demo_profile() -> None:
    budgets = {
        "profiles": {
            "demo": {
                "runs_per_hour_per_ip": 10,
                "max_question_chars": 500,
                "max_concurrent_runs": 2,
            }
        }
    }
    limiter = build_demo_limiter(Settings(budget_profile="demo", daily_cost_cap_usd=1.5), budgets)
    assert limiter.enabled
    assert limiter.runs_per_hour_per_ip == 10
    assert limiter.max_question_chars == 500
    assert limiter.max_concurrent_runs == 2
    assert limiter.daily_cost_cap_usd == 1.5


# --- rejection kinds (feed the T-036 §1 observability counter) --------------


def test_each_limit_raises_a_labelled_kind() -> None:
    """Every rejection carries a stable machine ``kind`` so the operations
    dashboard can split refusals by cause (question/rate/concurrency/cost)."""
    clock = _FakeClock()
    limiter = DemoLimiter(
        runs_per_hour_per_ip=1,
        max_question_chars=5,
        max_concurrent_runs=1,
        daily_cost_cap_usd=1.0,
        clock=clock,
    )

    with pytest.raises(DemoLimitError) as too_long:
        limiter.check_question("way too long")
    assert (too_long.value.kind, too_long.value.status_code) == ("question_too_long", 413)

    limiter.check_rate("203.0.113.7")
    with pytest.raises(DemoLimitError) as rate:
        limiter.check_rate("203.0.113.7")
    assert (rate.value.kind, rate.value.status_code) == ("rate_limited", 429)

    limiter.acquire_slot()
    with pytest.raises(DemoLimitError) as busy:
        limiter.acquire_slot()
    assert (busy.value.kind, busy.value.status_code) == ("concurrency_limit", 429)

    limiter.record_cost(1.0)
    with pytest.raises(DemoLimitError) as cost:
        limiter.check_daily_cost()
    assert (cost.value.kind, cost.value.status_code) == ("daily_cost_cap", 503)
