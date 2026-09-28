"""Parameter name normalisation (fold).

Design §6 — fold = NFKC + lowercase + collapse runs of non-alphanumerics to
underscore + strip leading/trailing underscores.  The same fold runs at
dictionary write time and at parameter write time so the comparison is
always between two identically-folded strings.

Examples
    fold('BUS_V')         → 'bus_v'
    fold('Bus voltage')   → 'bus_voltage'
    fold('Vbus (28V)')    → 'vbus_28v'
    fold('  PWR__IN  ')   → 'pwr_in'
"""

import re
import unicodedata


_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def fold(name: str) -> str:
    """Return the canonical folded form of a parameter name."""
    normalised = unicodedata.normalize("NFKC", name).lower()
    collapsed = _NON_ALNUM.sub("_", normalised)
    return collapsed.strip("_")
