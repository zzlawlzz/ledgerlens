"""Alert dispatch for monitoring layer B (T-035).

The alert channel is centralized here rather than in an n8n Telegram node so
that every alert passes the same guardrail + budget gate on the API side and so
the whole pipeline is testable without secrets: when ``TELEGRAM_BOT_TOKEN`` is
unset the alert is a dry-run — logged, not sent — which is exactly what CI and
the secretless demo use (T-035 ТЗ item 4).
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from common.config import Settings, get_settings
from common.logging import get_logger

_log = get_logger(node="monitoring")

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
_SEND_TIMEOUT_S = 15.0


@dataclass
class AlertResult:
    delivered: bool  # True only when actually sent to a channel
    channel: str  # 'telegram' | 'dry-run'
    detail: str = ""


def format_alert(*, company: str, event_type: str, summary: str, source_url: str) -> str:
    """One plain-text alert body. Kept provider-neutral (Telegram sends as text)."""
    header = f"\U0001f4e2 {company} — {event_type}"
    parts = [header, "", summary.strip()]
    if source_url:
        parts += ["", f"Source: {source_url}"]
    return "\n".join(parts)


async def send_alert(
    text: str,
    *,
    settings: Settings | None = None,
    client: httpx.AsyncClient | None = None,
) -> AlertResult:
    """Send ``text`` to Telegram, or dry-run to the log when no token is set."""
    settings = settings or get_settings()
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    if not token or not chat_id:
        _log.info("alert_dry_run", reason="no_telegram_credentials", preview=text[:200])
        return AlertResult(delivered=False, channel="dry-run", detail="no telegram credentials")

    url = TELEGRAM_API.format(token=token)
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    owns_client = client is None
    # Route through the alert proxy when configured (api.telegram.org is blocked
    # from RU IPs); a passed-in client (tests) is used as-is.
    client = client or httpx.AsyncClient(
        timeout=_SEND_TIMEOUT_S, proxy=settings.telegram_proxy_url or None
    )
    try:
        response = await client.post(url, json=payload)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        # An alert failure must not crash the monitoring tick — report it honestly.
        _log.warning("alert_send_failed", error=str(exc))
        return AlertResult(delivered=False, channel="telegram", detail=str(exc))
    finally:
        if owns_client:
            await client.aclose()
    _log.info("alert_sent", channel="telegram", chars=len(text))
    return AlertResult(delivered=True, channel="telegram")
