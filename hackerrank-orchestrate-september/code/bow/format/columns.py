"""Assembling the eight output columns from a decision.

Pure. The column order is the spec's and is not negotiable.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from bow.format.amounts import (
    format_iso_date,
    format_minimal_amount,
    format_plan_amount,
)

__all__ = ["COLUMNS", "NO_PLAN", "NO_CHANGES", "format_payment_plan", "format_spending_changes"]

#: Exactly the spec's order. Changing it breaks the submission.
COLUMNS = (
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
)

NO_PLAN = "none"
NO_CHANGES = "none"
_PLAN_SEPARATOR = "|"
_FIELD_SEPARATOR = ":"


def format_payment_plan(payments: tuple[tuple[date, Decimal], ...]) -> str:
    """``YYYY-MM-DD:amount`` entries in chronological order, joined by ``|``."""
    if not payments:
        return NO_PLAN
    return _PLAN_SEPARATOR.join(
        f"{format_iso_date(when)}{_FIELD_SEPARATOR}{format_plan_amount(amount)}"
        for when, amount in payments
    )


def format_spending_changes(changes: tuple[str, ...]) -> str:
    """``stop:<event_id>`` and ``reduce_to:<event_id>:<amount>``, joined by ``|``."""
    return _PLAN_SEPARATOR.join(changes) if changes else NO_CHANGES
