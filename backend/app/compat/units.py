"""Unit registry — ~15-dimension table with SI conversion factors.

Design §7 — hand-rolled, auditable, explicit.  Unknown units fail closed
into an unresolved-reference finding (P2), never guessed.

Each dimension entry maps symbol → factor-to-SI-base.  Conversion between
two units in the same dimension:  value_in_b = value_in_a * factor_a / factor_b.

The registry is intentionally small: add rows as real parameters surface,
not speculatively.  The test suite (tests/compat/test_units.py) exercises
the table so extensions show up as failures before merge.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class UnitEntry:
    dimension: str
    symbol: str
    factor: float  # 1 <symbol> = factor <SI-base>


# fmt: off
_TABLE: list[UnitEntry] = [
    # voltage — SI base: V
    UnitEntry("voltage", "V",  1.0),
    UnitEntry("voltage", "mV", 1e-3),
    UnitEntry("voltage", "kV", 1e3),

    # current — SI base: A
    UnitEntry("current", "A",  1.0),
    UnitEntry("current", "mA", 1e-3),
    UnitEntry("current", "uA", 1e-6),
    UnitEntry("current", "µA", 1e-6),

    # power — SI base: W
    UnitEntry("power", "W",  1.0),
    UnitEntry("power", "mW", 1e-3),
    UnitEntry("power", "kW", 1e3),

    # frequency — SI base: Hz
    UnitEntry("frequency", "Hz",  1.0),
    UnitEntry("frequency", "kHz", 1e3),
    UnitEntry("frequency", "MHz", 1e6),
    UnitEntry("frequency", "GHz", 1e9),

    # data rate — SI base: bit/s
    UnitEntry("data_rate", "bps",  1.0),
    UnitEntry("data_rate", "kbps", 1e3),
    UnitEntry("data_rate", "Mbps", 1e6),
    UnitEntry("data_rate", "Gbps", 1e9),

    # mass — SI base: kg
    UnitEntry("mass", "kg", 1.0),
    UnitEntry("mass", "g",  1e-3),

    # temperature — SI base: °C (offset conversions not in scope for interval arithmetic)
    UnitEntry("temperature", "°C",  1.0),
    UnitEntry("temperature", "degC", 1.0),

    # pressure — SI base: Pa
    UnitEntry("pressure", "Pa",   1.0),
    UnitEntry("pressure", "kPa",  1e3),
    UnitEntry("pressure", "MPa",  1e6),
    UnitEntry("pressure", "bar",  1e5),

    # time — SI base: s
    UnitEntry("time", "s",   1.0),
    UnitEntry("time", "ms",  1e-3),
    UnitEntry("time", "us",  1e-6),
    UnitEntry("time", "µs",  1e-6),
    UnitEntry("time", "min", 60.0),
    UnitEntry("time", "h",   3600.0),

    # dimensionless — exact 1.0
    UnitEntry("dimensionless", "",  1.0),
    UnitEntry("dimensionless", "%", 1.0),
    UnitEntry("dimensionless", "ppm", 1e-6),

    # resistance — SI base: Ω
    UnitEntry("resistance", "Ω",   1.0),
    UnitEntry("resistance", "ohm", 1.0),
    UnitEntry("resistance", "kΩ",  1e3),
    UnitEntry("resistance", "MΩ",  1e6),

    # capacitance — SI base: F
    UnitEntry("capacitance", "F",  1.0),
    UnitEntry("capacitance", "mF", 1e-3),
    UnitEntry("capacitance", "uF", 1e-6),
    UnitEntry("capacitance", "µF", 1e-6),
    UnitEntry("capacitance", "nF", 1e-9),
    UnitEntry("capacitance", "pF", 1e-12),

    # inductance — SI base: H
    UnitEntry("inductance", "H",  1.0),
    UnitEntry("inductance", "mH", 1e-3),
    UnitEntry("inductance", "uH", 1e-6),
    UnitEntry("inductance", "µH", 1e-6),
    UnitEntry("inductance", "nH", 1e-9),

    # angle — SI base: deg
    UnitEntry("angle", "deg", 1.0),
    UnitEntry("angle", "°",   1.0),
    UnitEntry("angle", "rad", 57.29577951308232),  # 180/π
]
# fmt: on

# Build lookup maps once at import time.
_BY_SYMBOL: dict[str, UnitEntry] = {e.symbol: e for e in _TABLE}
_BY_DIMENSION: dict[str, list[UnitEntry]] = {}
for _e in _TABLE:
    _BY_DIMENSION.setdefault(_e.dimension, []).append(_e)


def lookup(symbol: str) -> UnitEntry | None:
    """Return the entry for `symbol`, or None if unknown."""
    return _BY_SYMBOL.get(symbol)


def same_dimension(a: str, b: str) -> bool:
    """True when both symbols exist and belong to the same physical dimension."""
    ea, eb = _BY_SYMBOL.get(a), _BY_SYMBOL.get(b)
    return ea is not None and eb is not None and ea.dimension == eb.dimension


def convert(value: float, from_symbol: str, to_symbol: str) -> float | None:
    """Convert `value` from `from_symbol` to `to_symbol`.

    Returns None when either symbol is unknown or they are in different
    dimensions — callers must treat None as an unresolved-reference finding.
    """
    if not same_dimension(from_symbol, to_symbol):
        return None
    ef = _BY_SYMBOL[from_symbol]
    et = _BY_SYMBOL[to_symbol]
    return value * ef.factor / et.factor


def known_dimensions() -> list[str]:
    return sorted(_BY_DIMENSION.keys())
