"""Per-request demo notifications to the owner's Telegram (T-044).

Fires once per finished public-demo run so the owner can watch traffic and spend
in real time — each message carries the question, status, this run's cost/tokens/
latency, and the day's running total against the cost cap.

Off the ``demo`` budget profile, without ``DEMO_NOTIFY_TELEGRAM``, or without
Telegram credentials it is a no-op. Delivery goes through :func:`send_alert`,
which routes via ``TELEGRAM_PROXY_URL`` — an SSH-SOCKS egress on the FI node,
because api.telegram.org is blocked from RU IPs — and never raises. A failed
notification must never affect the run, so every path here is best-effort.
"""

from __future__ import annotations

from common.config import Settings, get_settings
from common.logging import get_logger
from orchestrator.alerting import send_alert

_log = get_logger(node="monitoring")

_STATUS_EMOJI = {"succeeded": "✅", "partial": "\U0001f7e1", "failed": "❌"}
_QUESTION_PREVIEW_CHARS = 160


def format_run_notification(
    *,
    question: str,
    mode: str,
    status: str,
    partial: bool,
    cost_usd: float,
    tokens_in: int,
    tokens_out: int,
    latency_ms: int,
    daily_spent: float | None,
    daily_cap: float | None,
) -> str:
    """One plain-text Telegram body for a finished demo run."""
    label = "partial" if (partial and status == "succeeded") else status
    emoji = _STATUS_EMOJI.get(label, "•")
    preview = " ".join(question.split())
    if len(preview) > _QUESTION_PREVIEW_CHARS:
        preview = preview[: _QUESTION_PREVIEW_CHARS - 1] + "…"
    lines = [
        f"{emoji} demo [{mode}] — {label}",
        f"«{preview}»",
        f"cost ${cost_usd:.4f} · tokens {tokens_in}+{tokens_out} · {latency_ms / 1000:.1f}s",
    ]
    if daily_spent is not None:
        cap = f" / ${daily_cap:.2f}" if daily_cap else ""
        lines.append(f"today: ${daily_spent:.4f}{cap}")
    return "\n".join(lines)


def notifications_enabled(settings: Settings) -> bool:
    """True only on the demo profile with the flag on and Telegram configured."""
    return (
        settings.budget_profile == "demo"
        and settings.demo_notify_telegram
        and bool(settings.telegram_bot_token)
        and bool(settings.telegram_chat_id)
    )


async def notify_run_finished(
    *,
    question: str,
    mode: str,
    status: str,
    partial: bool,
    cost_usd: float,
    tokens_in: int,
    tokens_out: int,
    latency_ms: int,
    daily_spent: float | None = None,
    daily_cap: float | None = None,
    settings: Settings | None = None,
) -> None:
    """Send a per-run demo notification; no-op when disabled, never raises."""
    settings = settings or get_settings()
    if not notifications_enabled(settings):
        return
    text = format_run_notification(
        question=question,
        mode=mode,
        status=status,
        partial=partial,
        cost_usd=cost_usd,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=latency_ms,
        daily_spent=daily_spent,
        daily_cap=daily_cap,
    )
    try:
        await send_alert(text, settings=settings)
    except Exception as exc:  # noqa: BLE001 — a notification must never break a run
        _log.warning("demo_notify_failed", error=str(exc))
