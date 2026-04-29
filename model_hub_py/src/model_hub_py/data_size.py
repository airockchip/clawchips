from __future__ import annotations

import math
import re

# Parse human-readable memory sizes; matches device-side `available_bytes` using binary (1024-based) bytes.
_UNIT_BYTES: dict[str, int] = {
    "": 1,
    "B": 1,
    "K": 1024,
    "KB": 1024,
    "KIB": 1024,
    "M": 1024**2,
    "MB": 1024**2,
    "MIB": 1024**2,
    "G": 1024**3,
    "GB": 1024**3,
    "GIB": 1024**3,
    "T": 1024**4,
    "TB": 1024**4,
    "TIB": 1024**4,
    "P": 1024**5,
    "PB": 1024**5,
    "PIB": 1024**5,
}

_PATTERN = re.compile(
    r"^\s*(?P<num>\d+(?:\.\d+)?)\s*(?P<unit>[A-Za-z]*)\s*$"
)


def parse_memory_bytes(value: str) -> int:
    """Parse strings like ``2048MB``, ``2GB``, or ``512`` (plain number = bytes) into byte count (int)."""
    s = value.strip()
    if not s:
        msg = "device_memory_usage: empty value"
        raise ValueError(msg)

    m = _PATTERN.fullmatch(s)
    if m is None:
        msg = f"device_memory_usage: invalid size string: {value!r}"
        raise ValueError(msg)

    num_s = m.group("num")
    unit = m.group("unit").upper()

    n = float(num_s)
    if n <= 0 or math.isnan(n) or math.isinf(n):
        msg = f"device_memory_usage: value must be positive, got: {value!r}"
        raise ValueError(msg)

    if unit not in _UNIT_BYTES:
        msg = f"device_memory_usage: unknown unit {m.group('unit')!r} in {value!r}"
        raise ValueError(msg)

    mult = _UNIT_BYTES[unit]
    result = n * mult
    as_int = int(result)
    if as_int < 1:
        msg = f"device_memory_usage: rounds to zero: {value!r}"
        raise ValueError(msg)
    if abs(result - as_int) > 1e-6:
        msg = f"device_memory_usage: value must be a whole number of bytes, got: {value!r}"
        raise ValueError(msg)

    return as_int
