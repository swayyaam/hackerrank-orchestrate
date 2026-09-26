"""Scalar types used across the contracts.

Money is ``Decimal`` and is built from the raw string the CSV supplied. A
``float`` is refused outright rather than converted, because by the time a
money value has been through binary floating point the damage is already done
and silent.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from pydantic import BeforeValidator, Field

__all__ = ["CurrencyCode", "Money", "IsoDate", "AwareDateTime", "parse_money"]

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CURRENCY = re.compile(r"^[A-Z]{3}$")


class MoneyError(ValueError):
    """A money value could not be built safely."""


def parse_money(value: Any) -> Decimal:
    """Build a ``Decimal`` without ever passing through ``float``."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise MoneyError("money must not be built from a bool")
    if isinstance(value, float):
        raise MoneyError(
            "money must not be built from a float; pass the raw string instead"
        )
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise MoneyError("money must not be built from an empty string")
        try:
            return Decimal(text)
        except InvalidOperation as exc:
            raise MoneyError(f"not a decimal number: {value!r}") from exc
    raise MoneyError(f"unsupported money input type: {type(value).__name__}")


def _parse_iso_date(value: Any) -> Any:
    """Accept only ``YYYY-MM-DD``. No ordinals, no timestamps, no leniency."""
    if isinstance(value, datetime):
        raise ValueError("expected a calendar date, got a datetime")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not _ISO_DATE.match(text):
            raise ValueError(f"expected YYYY-MM-DD, got {value!r}")
        return date.fromisoformat(text)
    raise ValueError(f"unsupported date input type: {type(value).__name__}")


def _parse_aware_datetime(value: Any) -> Any:
    """Accept an ISO-8601 instant that carries a timezone."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    else:
        raise ValueError(f"unsupported datetime input type: {type(value).__name__}")
    if parsed.tzinfo is None:
        raise ValueError("datetime must be timezone aware")
    return parsed


Money = Annotated[Decimal, BeforeValidator(parse_money)]
IsoDate = Annotated[date, BeforeValidator(_parse_iso_date)]
AwareDateTime = Annotated[datetime, BeforeValidator(_parse_aware_datetime)]
CurrencyCode = Annotated[str, Field(pattern=_CURRENCY.pattern)]
