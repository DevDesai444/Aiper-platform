"""Table-driven tests for the unit registry (app.compat.units)."""

import pytest

from app.compat.units import convert, known_dimensions, lookup, same_dimension


@pytest.mark.parametrize(
    "symbol",
    ["V", "mV", "kV", "A", "mA", "uA", "W", "mW", "kW", "Hz", "kHz", "MHz", "GHz",
     "bps", "kbps", "Mbps", "Gbps", "kg", "g", "°C", "Pa", "kPa", "bar",
     "s", "ms", "us", "min", "h", "", "%", "Ω", "ohm", "F", "nF", "pF", "H", "mH",
     "deg", "°"],
)
def test_known_symbols_in_registry(symbol: str) -> None:
    assert lookup(symbol) is not None, f"'{symbol}' not in registry"


def test_unknown_symbol_returns_none() -> None:
    assert lookup("zorblax") is None
    assert lookup("XY/s²") is None


@pytest.mark.parametrize(
    "a, b",
    [
        ("V", "mV"),
        ("V", "kV"),
        ("A", "mA"),
        ("W", "kW"),
        ("Hz", "MHz"),
        ("kg", "g"),
        ("s", "ms"),
        ("Ω", "ohm"),
        ("F", "pF"),
    ],
)
def test_same_dimension(a: str, b: str) -> None:
    assert same_dimension(a, b)
    assert same_dimension(b, a)


@pytest.mark.parametrize(
    "a, b",
    [
        ("V", "A"),
        ("Hz", "V"),
        ("kg", "s"),
        ("Ω", "F"),
    ],
)
def test_different_dimension(a: str, b: str) -> None:
    assert not same_dimension(a, b)


def test_same_dimension_unknown() -> None:
    assert not same_dimension("V", "zorblax")
    assert not same_dimension("zorblax", "V")


@pytest.mark.parametrize(
    "value, frm, to, expected",
    [
        (1.0, "V", "mV", 1000.0),
        (1000.0, "mV", "V", 1.0),
        (1.0, "kV", "V", 1000.0),
        (28.0, "V", "V", 28.0),
        (1.0, "A", "mA", 1000.0),
        (1.0, "kg", "g", 1000.0),
        (1.0, "kHz", "Hz", 1000.0),
        (1.0, "s", "ms", 1000.0),
        (60.0, "s", "min", 1.0),
    ],
)
def test_convert(value: float, frm: str, to: str, expected: float) -> None:
    result = convert(value, frm, to)
    assert result is not None
    assert abs(result - expected) < 1e-9


def test_convert_cross_dimension_returns_none() -> None:
    assert convert(1.0, "V", "A") is None


def test_convert_unknown_symbol_returns_none() -> None:
    assert convert(1.0, "zorblax", "V") is None
    assert convert(1.0, "V", "zorblax") is None


def test_known_dimensions_not_empty() -> None:
    dims = known_dimensions()
    assert "voltage" in dims
    assert "current" in dims
    assert "data_rate" in dims
