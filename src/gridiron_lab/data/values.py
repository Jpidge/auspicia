"""Scalar parsing for NFLverse columns and CSV fixtures.

Blank strings, ``NaN``, and ``None`` are missing values. They are not zeros.
"""

from __future__ import annotations

import math

_MISSING = frozenset({"", "nan", "none", "nat", "null"})


def as_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in _MISSING:
        return None
    return text


def as_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return None
        return int(value)
    text = as_text(value)
    if text is None:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    if not math.isfinite(number) or not number.is_integer():
        return None
    return int(number)


def as_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    text = as_text(value)
    if text is None:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def as_flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value == 1
    text = as_text(value)
    return text is not None and text.lower() in {"1", "true", "t", "yes"}
