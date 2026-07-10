"""Web i18n contract (T-024): no hardcoded UI strings outside the dictionary.

Heuristic grep over JSX: text nodes in components must not contain word
literals — every user-visible string goes through ``t(...)`` (src/i18n.ts).
Symbols/punctuation and interpolations are fine.
"""

from __future__ import annotations

import re
from pathlib import Path

WEB_SRC = Path(__file__).resolve().parents[2] / "web" / "src"

# JSX text node made of words (latin or cyrillic) — no code punctuation, one
# line: `>Ask<` is an offender, `>(detectLang);` or generics are not.
_TEXT_NODE = re.compile(r">\s*[A-Za-zА-Яа-яЁё][A-Za-z А-Яа-яЁё'!?.,:-]{2,}\s*<")
# Attributes that render text directly to the user.
_TEXT_ATTR = re.compile(r"\b(placeholder|title|aria-label)\s*=\s*\"[^\"]*[A-Za-zА-Яа-яЁё]{3,}")


def _component_files() -> list[Path]:
    files = list((WEB_SRC / "components").glob("*.tsx"))
    files.append(WEB_SRC / "App.tsx")
    assert files, "web components not found"
    return files


def test_no_hardcoded_text_nodes_in_components() -> None:
    offenders: list[str] = []
    for path in _component_files():
        source = path.read_text(encoding="utf-8")
        for match in _TEXT_NODE.finditer(source):
            offenders.append(f"{path.name}: {match.group(0)!r}")
        for match in _TEXT_ATTR.finditer(source):
            offenders.append(f"{path.name}: {match.group(0)!r}")
    assert not offenders, "hardcoded UI strings (must go through i18n t()):\n" + "\n".join(
        offenders
    )


def test_dictionary_locales_are_symmetric() -> None:
    source = (WEB_SRC / "i18n.ts").read_text(encoding="utf-8")
    en_keys = set(re.findall(r"^\s{4}(\w+):", source.split("en: {")[1].split("ru: {")[0], re.M))
    ru_keys = set(re.findall(r"^\s{4}(\w+):", source.split("ru: {")[1], re.M))
    assert en_keys and en_keys == ru_keys, f"locale keys diverge: {en_keys ^ ru_keys}"
