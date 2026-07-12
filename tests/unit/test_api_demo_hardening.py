"""Demo-profile hardening of the HTTP surface (T-036 §3).

The interactive API schema and Swagger/ReDoc explorers are debug affordances.
They must be switched off when ``BUDGET_PROFILE=demo`` and stay on in dev. The
gate is decided at module import (``orchestrator.api`` computes ``_DEMO`` once),
so each profile is checked in a fresh subprocess to avoid import/lru_cache
carryover between cases.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

_PROBE = "import orchestrator.api as a;print(a.app.docs_url, a.app.redoc_url, a.app.openapi_url)"


def _probe(profile: str) -> tuple[str, str, str]:
    """Import the app under the given profile and report its doc URLs."""
    env = {
        "PATH": __import__("os").environ.get("PATH", ""),
        "BUDGET_PROFILE": profile,
        # Settings pulls POSTGRES_PASSWORD from the environment; a placeholder
        # keeps import from failing without touching any real database.
        "POSTGRES_PASSWORD": "test",
        "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
    }
    out = subprocess.check_output(
        [sys.executable, "-c", _PROBE],
        cwd=str(_REPO_ROOT),
        env=env,
        text=True,
    )
    docs, redoc, openapi = out.strip().split()
    return docs, redoc, openapi


def test_demo_profile_disables_docs_and_schema() -> None:
    docs, redoc, openapi = _probe("demo")
    assert docs == "None"
    assert redoc == "None"
    assert openapi == "None"


def test_dev_profile_keeps_docs_and_schema() -> None:
    docs, redoc, openapi = _probe("dev")
    assert docs == "/docs"
    assert redoc == "/redoc"
    assert openapi == "/openapi.json"
