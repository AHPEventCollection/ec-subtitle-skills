from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache


_ASS_TAG = re.compile(r"\{[^}]*\}")
_kakasi = None


def normalize_text(text: str) -> str:
    stripped = _ASS_TAG.sub("", text)
    stripped = stripped.replace("\\N", "").replace("\\n", "").replace("\n", "")
    normalized = unicodedata.normalize("NFKC", stripped).lower()
    return "".join(
        char
        for char in normalized
        if not char.isspace() and unicodedata.category(char)[0] not in {"P", "S"}
    )


@lru_cache(maxsize=8192)
def phonetic_key(text: str) -> str:
    global _kakasi
    normalized = normalize_text(text)
    if not normalized:
        return ""
    try:
        if _kakasi is None:
            import pykakasi

            _kakasi = pykakasi.kakasi()
        return "".join(item["hira"] for item in _kakasi.convert(normalized))
    except ImportError:
        return normalized


def _ratio(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left in right or right in left:
        return min(len(left), len(right)) / max(len(left), len(right))
    return SequenceMatcher(None, left, right, autojunk=False).ratio()


def similarity(left: str, right: str) -> float:
    raw = _ratio(normalize_text(left), normalize_text(right))
    folded = _ratio(phonetic_key(left), phonetic_key(right))
    return max(raw, folded)
