"""Unit tests for the parameter-name normalisation fold (app.compat.normalize)."""

import pytest

from app.compat.normalize import fold


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("BUS_V", "bus_v"),
        ("Bus voltage", "bus_voltage"),
        ("Vbus (28V)", "vbus_28v"),
        ("  PWR__IN  ", "pwr_in"),
        ("bus_voltage", "bus_voltage"),
        ("BUS-V", "bus_v"),
        ("V_bus", "v_bus"),
        ("28V_nominal", "28v_nominal"),
        ("µA_range", "a_range"),  # µ (U+00B5) → μ (U+03BC) via NFKC, stripped as non-ASCII
        ("already_folded", "already_folded"),
        ("MIXED   SPACES", "mixed_spaces"),
        ("trailing___", "trailing"),
        ("___leading", "leading"),
    ],
)
def test_fold(raw: str, expected: str) -> None:
    assert fold(raw) == expected


def test_fold_nfkc() -> None:
    # NFKC: 'ＡＢＣ' (fullwidth) → 'ABC' → 'abc'
    assert fold("ＡＢＣ") == "abc"


def test_fold_empty() -> None:
    assert fold("") == ""


def test_fold_only_separators() -> None:
    assert fold("---") == ""
