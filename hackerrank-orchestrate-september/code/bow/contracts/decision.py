"""The decision record.

Lives in ``contracts/`` because three separate packages consume it — ``engine/``
produces it, ``format/`` renders it, ``explain/`` writes prose from it — and
none of them may import the others.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from bow.contracts.enums import AffordabilityStatus, PaymentMethod
from bow.contracts.scalars import IsoDate, Money

__all__ = ["Decision"]


class Decision(BaseModel):
    """One request's answer, before formatting."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
    amount_safe_to_pay: Money
    affordability_status: AffordabilityStatus
    recommended_payment_method: PaymentMethod
    payments: tuple[tuple[IsoDate, Money], ...]
    earliest_date_for_full_payment: IsoDate | None
    spending_changes: tuple[str, ...]
    chosen_option_id: str | None = None
