"""Deterministic explanation text.

**No model call.** The sentence is built from the decision and the profile,
following the shapes the reconnaissance extracted from the published sample
explanations.

This is not a placeholder to be deleted. When the model-written explanation and
its grounding verifier arrive, this stays as the permanent fallback: a
verifier that rejects a generated sentence needs something to fall back to, and
falling back to an empty cell loses a scored column.

Every number here is formatted by ``bow.format`` and every number comes from the
decision, never from a fresh computation.
"""

from __future__ import annotations

from decimal import Decimal

from bow.contracts.dataset import FinancialProfile, Request
from bow.contracts.enums import PaymentMethod
from bow.contracts.decision import Decision
from bow.format import format_explanation_amount, format_explanation_date

__all__ = ["explain"]


def explain(decision: Decision, request: Request, profile: FinancialProfile) -> str:
    """One or two sentences, chosen by the recommended method."""
    currency = profile.home_currency
    money = lambda value: format_explanation_amount(currency, value)  # noqa: E731
    floor = money(profile.minimum_balance_to_keep)
    method = decision.recommended_payment_method
    payments = decision.payments

    if method is PaymentMethod.FULL_PAYMENT:
        amount = money(payments[0][1])
        if decision.spending_changes:
            return (
                f"Make the listed spending changes, then pay {amount} today. "
                f"This leaves at least {floor} available."
            )
        return (
            f"Pay {amount} today. This leaves at least {floor} available "
            f"over the next 90 days."
        )

    if method is PaymentMethod.INSTALLMENTS:
        return (
            f"Use {len(payments)} installments of {money(payments[0][1])}, "
            f"starting {format_explanation_date(payments[0][0])}. "
            f"This leaves at least {floor} available."
        )

    if method is PaymentMethod.PARTIAL_PAYMENT:
        return (
            f"Pay {money(payments[0][1])} today and the remaining "
            f"{money(payments[1][1])} on {format_explanation_date(payments[1][0])}. "
            f"This completes the full request and keeps the {floor} minimum protected."
        )

    if method is PaymentMethod.WAIT:
        return (
            f"Pay {money(payments[0][1])} in full on "
            f"{format_explanation_date(payments[0][0])}. Paying earlier would take "
            f"the balance below the {floor} minimum."
        )

    # not_recommended. Two shapes in the published samples: the second is used
    # where the user's only accepted method is partial payment, so there is
    # money available today but no route that completes the request.
    only_partial = profile.payment_methods_user_will_consider == (
        PaymentMethod.PARTIAL_PAYMENT,
    )
    if only_partial and decision.amount_safe_to_pay > Decimal(0):
        return (
            f"Do not proceed with the {money(request.requested_amount)} request. "
            f"Although {money(decision.amount_safe_to_pay)} is available today, the "
            f"full amount cannot be completed safely within 90 days."
        )
    return (
        f"Do not make this payment by "
        f"{format_explanation_date(request.desired_completion_date)}. None of the "
        f"available options keeps the {floor} minimum protected."
    )
