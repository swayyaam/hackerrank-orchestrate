"""The three distinct number formats the output uses.

Named ``amounts`` rather than ``numbers`` because ``numbers`` is a standard
library module and a package module by that name shadows it.

They are genuinely different, and the same value is spelled differently
depending on which column it lands in. That is not a quirk to normalise away —
it is what the published ground truth does, and matching it is free accuracy.

=========================  ============================  =====================
column                     rule                          example
=========================  ============================  =====================
``amount_safe_to_pay``     minimal, trailing zeros gone  ``603.3``
``payment_plan`` amount    integer bare, else exactly 2  ``620.40`` / ``25256``
explanation amount         ``CUR #,##0[.00]``            ``EUR 620.40``
=========================  ============================  =====================

The first two disagree on the same number: ``Decimal("620.40")`` is ``620.4`` in
``amount_safe_to_pay`` and ``620.40`` in ``payment_plan``.

Pure. No I/O, no clock, no randomness, and no import outside ``bow.contracts``.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

__all__ = [
    "format_explanation_amount",
    "format_explanation_date",
    "format_iso_date",
    "format_minimal_amount",
    "format_plan_amount",
    "quantize_money",
]

_TWO_PLACES = Decimal("0.01")


def quantize_money(value: Decimal) -> Decimal:
    """Round to the currency minor unit, half away from zero.

    **This is the quantization boundary.** Money is carried at full precision
    through normalization, FX and the engine, and is rounded exactly once, here,
    on its way out. Without it an exchange-rate multiplication reaches the
    output column as 356.154888888888888888888892.
    """
    return value.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def _plain(value: Decimal) -> str:
    """Fixed-point text, never scientific notation."""
    return f"{value:f}"


def format_minimal_amount(value: Decimal) -> str:
    """``amount_safe_to_pay``: minimal representation, trailing zeros stripped.

    The ground truth carries ``603.3`` and ``17229139.2`` at one decimal place,
    so this column is not padded to two. It is also not normalised through
    ``Decimal.normalize``, which would turn ``25256`` into ``2.5256E+4``.
    """
    text = _plain(quantize_money(value))
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def format_plan_amount(value: Decimal) -> str:
    """``payment_plan`` and ``reduce_to`` amounts: bare if integral, else 2 dp.

    The ground truth writes ``25256`` and ``620.40`` in the same column, so the
    rule is conditional on the value rather than uniform.
    """
    rounded = quantize_money(value)
    if rounded == rounded.to_integral_value():
        return _plain(rounded.to_integral_value())
    return _plain(rounded)


def format_explanation_amount(currency: str, value: Decimal) -> str:
    """Explanation prose: ``CUR`` then comma-grouped digits, 2 dp if non-integral.

    ``ZAR 25,256`` and ``IDR 15,952,906.67`` and ``USD 23.50``.
    """
    rounded = quantize_money(value)
    if rounded == rounded.to_integral_value():
        return f"{currency} {int(rounded):,}"
    return f"{currency} {rounded:,.2f}"


#: Month names, spelled out. Not locale-dependent: the ground truth is English.
_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)


def format_explanation_date(when: date) -> str:
    """Explanation prose: ``D Month YYYY``, no leading zero on the day."""
    return f"{when.day} {_MONTHS[when.month - 1]} {when.year}"


def format_iso_date(when: date | None) -> str:
    """Output columns: ``YYYY-MM-DD``, or empty when there is no date."""
    return when.isoformat() if when is not None else ""
