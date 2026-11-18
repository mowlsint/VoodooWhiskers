"""Normalize AIS hull dimensions into a canonical length in metres."""
from __future__ import annotations

import math
from collections.abc import Mapping

MAX_LENGTH_M = 1000.0
DIRECT_LENGTH_KEYS = (
    "length_m", "vessel_length_m", "ship_length_m", "loa_m",
    "length_meters", "length_metres", "lengthMeters", "lengthMetres",
    "loaMeters", "loaMetres",
)
DIMENSION_A_KEYS = ("A", "a", "dimension_a", "DimensionA", "to_bow", "toBow", "bow")
DIMENSION_B_KEYS = ("B", "b", "dimension_b", "DimensionB", "to_stern", "toStern", "stern")
DIMENSION_KEYS = ("Dimension", "dimension", "dimensions")


def _positive_m(value: object) -> float | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isfinite(number) and 0 < number <= MAX_LENGTH_M:
        return number
    return None


def _first(mapping: Mapping, keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key in mapping:
            number = _positive_m(mapping[key])
            if number is not None:
                return number
    return None


def length_meters(*records: object) -> float | None:
    """Return explicit metre length or AIS dimension A+B; unknown units stay unknown."""
    for record in records:
        if not isinstance(record, Mapping):
            continue
        direct = _first(record, DIRECT_LENGTH_KEYS)
        if direct is not None:
            return round(direct, 1)
        nested = [record[key] for key in DIMENSION_KEYS if key in record]
        for dimensions in [*nested, record]:
            if not isinstance(dimensions, Mapping):
                continue
            a = _first(dimensions, DIMENSION_A_KEYS)
            b = _first(dimensions, DIMENSION_B_KEYS)
            if a is not None and b is not None and a + b <= MAX_LENGTH_M:
                return round(a + b, 1)
    return None
