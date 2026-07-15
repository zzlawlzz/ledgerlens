"""Public-demo request limits (T-036 §1, CONTRACTS §13).

Active only when ``BUDGET_PROFILE=demo``. A public stranger must not be able to
exhaust the owner's LLM budget or overload the single home node. Four limits,
all read from the ``demo`` budget profile / settings so config is the single
source of truth:

* ``runs_per_hour_per_ip`` — sliding-window rate limit, keyed by a hash of the
  client IP (privacy: we key by identity, never store raw addresses).
* ``max_question_chars`` — reject overlong prompts before any LLM spend.
* ``max_concurrent_runs`` — global cap on in-flight runs (protects the node).
* ``daily_cost_cap_usd`` — system-wide daily spend cap; over it, a polite refusal.

All limits are **in-process**: the demo runs as one orchestrator on one node, so
a shared backend (Redis / slowapi storage) would add a dependency for no gain.
This mirrors the existing in-process caps for layer B (``DailyBudget``) and the
monitor concurrency ``Semaphore``. (The ТЗ names slowapi; on a single process it
would still be in-memory, and an explicit limiter lets the budget-profile value
stay the one source of truth — noted in the BACKLOG footnote.)

In any non-``demo`` profile the limiter is a no-op, so dev/tests are unaffected.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import UTC, date, datetime
from time import time as _wall_time
from typing import Any

from common.config import Settings, load_yaml_config

_HOUR_S = 3600.0


class DemoLimitError(Exception):
    """A demo limit was hit. ``status_code``/``detail`` map to a polite HTTP
    refusal (see the exception handler in ``orchestrator.api``). ``kind`` is a
    stable machine label used for the observability counter (T-036 §1)."""

    def __init__(self, status_code: int, detail: str, kind: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.kind = kind


def hash_ip(ip: str) -> str:
    """Stable short digest of a client IP. Used only to bucket rate limits by
    caller identity without persisting the raw address; not a security control."""
    return hashlib.sha256(ip.encode("utf-8")).hexdigest()[:16]


class DemoLimiter:
    """In-process enforcement of the four demo limits. Thread-safety is not
    needed: FastAPI runs these checks on the single asyncio event loop, and each
    method returns before awaiting."""

    def __init__(
        self,
        *,
        runs_per_hour_per_ip: int | None = None,
        max_question_chars: int | None = None,
        max_concurrent_runs: int | None = None,
        daily_cost_cap_usd: float | None = None,
        clock: Callable[[], float] = _wall_time,
    ) -> None:
        self.runs_per_hour_per_ip = runs_per_hour_per_ip
        self.max_question_chars = max_question_chars
        self.max_concurrent_runs = max_concurrent_runs
        self.daily_cost_cap_usd = daily_cost_cap_usd
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._active = 0
        self._cost_day: date | None = None
        self._cost_spent = 0.0

    @property
    def enabled(self) -> bool:
        """True if at least one limit is set (i.e. we are in the demo profile)."""
        return any(
            v is not None
            for v in (
                self.runs_per_hour_per_ip,
                self.max_question_chars,
                self.max_concurrent_runs,
                self.daily_cost_cap_usd,
            )
        )

    # --- admission checks (raise DemoLimitError to reject) ------------------

    def check_question(self, question: str) -> None:
        limit = self.max_question_chars
        if limit is not None and len(question) > limit:
            raise DemoLimitError(
                413,
                f"Question too long for the public demo ({len(question)} chars, "
                f"limit {limit}). Please shorten it.",
                kind="question_too_long",
            )

    def check_rate(self, ip: str) -> None:
        """Sliding 1-hour window per hashed IP. Prunes expired hits, then either
        records the new hit or rejects with a 429 and a retry hint."""
        limit = self.runs_per_hour_per_ip
        if limit is None:
            return
        key = hash_ip(ip)
        now = self._clock()
        window = self._hits[key]
        cutoff = now - _HOUR_S
        while window and window[0] <= cutoff:
            window.popleft()
        if len(window) >= limit:
            retry_after = int(window[0] + _HOUR_S - now) + 1
            raise DemoLimitError(
                429,
                f"Rate limit reached for the public demo ({limit} runs/hour). "
                f"Please try again in about {max(retry_after // 60, 1)} min.",
                kind="rate_limited",
            )
        window.append(now)

    def check_daily_cost(self) -> None:
        """Reject once the system-wide daily spend cap is reached. Costs are
        recorded by ``record_cost`` after each run finalizes."""
        cap = self.daily_cost_cap_usd
        if cap is None:
            return
        self._roll_cost_day()
        if self._cost_spent >= cap:
            raise DemoLimitError(
                503,
                "The public demo has reached its daily budget. It resets at "
                "00:00 UTC — please come back then. Thanks for trying it!",
                kind="daily_cost_cap",
            )

    def acquire_slot(self) -> None:
        """Take a global concurrency slot or reject. Pair every successful call
        with exactly one ``release_slot`` (done in the run's finally block)."""
        limit = self.max_concurrent_runs
        if limit is None:
            self._active += 1
            return
        if self._active >= limit:
            raise DemoLimitError(
                429,
                "The public demo is busy handling other requests right now. "
                "Please try again in a few seconds.",
                kind="concurrency_limit",
            )
        self._active += 1

    def release_slot(self) -> None:
        if self._active > 0:
            self._active -= 1

    # --- accounting --------------------------------------------------------

    def record_cost(self, cost_usd: float) -> None:
        self._roll_cost_day()
        self._cost_spent += max(cost_usd, 0.0)

    def daily_report(self) -> tuple[float | None, float | None]:
        """(spent_today, cap) for owner notifications; (None, None) off demo."""
        if not self.enabled:
            return None, None
        self._roll_cost_day()
        return self._cost_spent, self.daily_cost_cap_usd

    def _roll_cost_day(self) -> None:
        today = datetime.fromtimestamp(self._clock(), UTC).date()
        if today != self._cost_day:
            self._cost_day, self._cost_spent = today, 0.0


def build_demo_limiter(settings: Settings, budgets: dict[str, Any] | None = None) -> DemoLimiter:
    """Construct the limiter for the active budget profile. Off the ``demo``
    profile every limit is ``None`` (no-op), so dev and tests are untouched."""
    if settings.budget_profile != "demo":
        return DemoLimiter()
    profiles = (budgets or load_yaml_config("budgets")).get("profiles", {})
    demo = profiles.get("demo", {})
    return DemoLimiter(
        runs_per_hour_per_ip=demo.get("runs_per_hour_per_ip"),
        max_question_chars=demo.get("max_question_chars", 500),
        max_concurrent_runs=demo.get("max_concurrent_runs", 2),
        daily_cost_cap_usd=settings.daily_cost_cap_usd,
    )
