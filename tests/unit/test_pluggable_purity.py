"""Pluggable purity check (T-015, gate G1 criterion).

No pipeline/runtime code may import the EDGAR adapter directly — sources are
reachable only through the adapter registry (``adapters.base.get_adapter``).
The registry itself (``adapters/__init__``), fixture-recording scripts and
tests are the sanctioned exceptions.
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Packages that must stay source-agnostic.
PURE_PACKAGES = ("ingestion", "orchestrator", "tools", "workers", "common", "model_router", "rag")

_EDGAR_IMPORT = re.compile(r"^\s*(from|import)\s+adapters\.edgar", re.MULTILINE)


def test_no_direct_edgar_imports_outside_adapter_registry() -> None:
    offenders: list[str] = []
    for package in PURE_PACKAGES:
        for path in (PROJECT_ROOT / package).rglob("*.py"):
            if _EDGAR_IMPORT.search(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(PROJECT_ROOT)))
    assert not offenders, (
        f"direct adapters.edgar imports break pluggability: {offenders}; "
        "use adapters.base.get_adapter() instead"
    )
