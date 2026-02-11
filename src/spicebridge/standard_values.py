"""E-series standard component values and engineering notation formatting."""

from __future__ import annotations

import math

# Normalized single-decade values (1.0 to <10.0)
E12: tuple[float, ...] = (1.0, 1.2, 1.5, 1.8, 2.2, 2.7, 3.3, 3.9, 4.7, 5.6, 6.8, 8.2)

E24: tuple[float, ...] = (
    1.0,
    1.1,
    1.2,
    1.3,
    1.5,
    1.6,
    1.8,
    2.0,
    2.2,
    2.4,
    2.7,
    3.0,
    3.3,
    3.6,
    3.9,
    4.3,
    4.7,
    5.1,
    5.6,
    6.2,
    6.8,
    7.5,
    8.2,
    9.1,
)

E96: tuple[float, ...] = (
    1.00,
    1.02,
    1.05,
    1.07,
    1.10,
    1.13,
    1.15,
    1.18,
    1.21,
    1.24,
    1.27,
    1.30,
    1.33,
    1.37,
    1.40,
    1.43,
    1.47,
    1.50,
    1.54,
    1.58,
    1.62,
    1.65,
    1.69,
    1.74,
    1.78,
    1.82,
    1.87,
    1.91,
    1.96,
    2.00,
    2.05,
    2.10,
    2.15,
    2.21,
    2.26,
    2.32,
    2.37,
    2.43,
    2.49,
    2.55,
    2.61,
    2.67,
    2.74,
    2.80,
    2.87,
    2.94,
    3.01,
    3.09,
    3.16,
    3.24,
    3.32,
    3.40,
    3.48,
    3.57,
    3.65,
    3.74,
    3.83,
    3.92,
    4.02,
    4.12,
    4.22,
    4.32,
    4.42,
    4.53,
    4.64,
    4.75,
    4.87,
    4.99,
    5.11,
    5.23,
    5.36,
    5.49,
    5.62,
    5.76,
    5.90,
    6.04,
    6.19,
    6.34,
    6.49,
    6.65,
    6.81,
    6.98,
    7.15,
    7.32,
    7.50,
    7.68,
    7.87,
    8.06,
    8.25,
    8.45,
    8.66,
    8.87,
    9.09,
    9.31,
    9.53,
    9.76,
)

_SERIES = {"E12": E12, "E24": E24, "E96": E96}


def snap_to_standard(value: float, series: str = "E96") -> float:
    """Snap a value to the nearest standard E-series value.

    Uses logarithmic distance since E-series values are geometrically spaced.
    """
    if value <= 0:
        raise ValueError(f"Value must be positive, got {value}")
    if series not in _SERIES:
        valid = sorted(_SERIES)
        raise ValueError(f"Unknown series '{series}', expected one of {valid}")

    s = _SERIES[series]
    decade = math.floor(math.log10(value))
    normalized = value / 10**decade  # [1.0, 10.0)

    log_norm = math.log10(normalized)
    best = s[0]
    best_dist = abs(log_norm - math.log10(s[0]))

    for candidate in s[1:]:
        dist = abs(log_norm - math.log10(candidate))
        if dist < best_dist:
            best = candidate
            best_dist = dist

    # Check first value of next decade (10.0 normalized)
    dist_next = abs(log_norm - math.log10(10.0))
    if dist_next < best_dist:
        best = s[0]
        decade += 1

    return best * 10**decade


# Engineering notation prefixes
_PREFIXES = [
    (1e12, "T"),
    (1e9, "G"),
    (1e6, "M"),
    (1e3, "k"),
    (1.0, ""),
    (1e-3, "m"),
    (1e-6, "u"),
    (1e-9, "n"),
    (1e-12, "p"),
    (1e-15, "f"),
]


def format_engineering(value: float) -> str:
    """Format a value using engineering notation with SI prefixes.

    Examples: 10000.0 -> "10k", 15.9e-9 -> "15.9n", 4.7e-6 -> "4.7u", 100.0 -> "100"
    """
    if value == 0:
        return "0"

    abs_val = abs(value)
    sign = "-" if value < 0 else ""

    for threshold, prefix in _PREFIXES:
        if abs_val >= threshold:
            mantissa = abs_val / threshold
            # Format with up to 3 significant digits, strip trailing zeros
            if mantissa == int(mantissa):
                formatted = str(int(mantissa))
            else:
                formatted = f"{mantissa:.3g}"
            return f"{sign}{formatted}{prefix}"

    # Fallback for extremely small values
    return f"{value:.3g}"


def parse_spice_value(s: str) -> float:
    """Convert a SPICE value string (e.g., '1k', '100n') to a float."""
    suffixes = {
        "t": 1e12,
        "g": 1e9,
        "meg": 1e6,
        "k": 1e3,
        "m": 1e-3,
        "u": 1e-6,
        "n": 1e-9,
        "p": 1e-12,
        "f": 1e-15,
    }
    s = s.strip().lower()
    for suffix, mult in sorted(suffixes.items(), key=lambda x: -len(x[0])):
        if s.endswith(suffix):
            return float(s[: -len(suffix)]) * mult
    return float(s)
