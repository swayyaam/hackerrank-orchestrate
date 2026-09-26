"""Turning a chosen plan into a decision, and refusing to emit an invalid one.

Every post-condition the specification states is enforced **inside**
:func:`decide`, and a violation raises rather than returning. A malformed row
that reaches the output file is worse than a crash: the crash is visible.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from bow.contracts.dataset import FinancialProfile, Request
from bow.contracts.decision import Decision
from bow.contracts.enums import AffordabilityStatus, PaymentMethod
from bow.engine.plans import PlanCandidate

__all__ = ["Decision", "PolicyViolation", "decide"]

_ZERO = Decimal()


class PolicyViolation(RuntimeError):
    """A decision broke a stated post-condition. Never returned, always raised."""


_STATUS_FOR_METHOD = {
    PaymentMethod.PARTIAL_PAYMENT: AffordabilityStatus.AFFORDABLE_WITH_PLAN,
    PaymentMethod.INSTALLMENTS: AffordabilityStatus.AFFORDABLE_WITH_PLAN,
    PaymentMethod.WAIT: AffordabilityStatus.AFFORDABLE_LATER,
    PaymentMethod.NOT_RECOMMENDED: AffordabilityStatus.NOT_AFFORDABLE,
}


def _status_for(plan: PlanCandidate | None, changes: tuple[str, ...]) -> AffordabilityStatus:
    """The status table.

    ``full_payment`` is the only method whose status depends on something else:
    paid today with no spending change it is ``affordable_now``; paid today only
    because a spending change made room, it is ``affordable_with_plan``.
    """
    if plan is None:
        return AffordabilityStatus.NOT_AFFORDABLE
    if plan.method is PaymentMethod.FULL_PAYMENT:
        return (
            AffordabilityStatus.AFFORDABLE_WITH_PLAN
            if changes
            else AffordabilityStatus.AFFORDABLE_NOW
        )
    return _STATUS_FOR_METHOD[plan.method]


def decide(
    request: Request,
    profile: FinancialProfile,
    plan: PlanCandidate | None,
    *,
    amount_safe: Decimal,
    earliest_full_payment: date | None,
    spending_changes: tuple[str, ...] = (),
) -> Decision:
    """Assemble the decision and enforce every post-condition."""
    method = plan.method if plan is not None else PaymentMethod.NOT_RECOMMENDED
    status = _status_for(plan, spending_changes)
    payments = plan.payments if plan is not None else ()

    decision = Decision(
        request_id=request.request_id,
        amount_safe_to_pay=amount_safe,
        affordability_status=status,
        recommended_payment_method=method,
        payments=payments,
        earliest_date_for_full_payment=earliest_full_payment,
        spending_changes=spending_changes,
        chosen_option_id=plan.option_id if plan is not None else None,
    )
    _enforce(decision, request, profile)
    return decision


def _enforce(decision: Decision, request: Request, profile: FinancialProfile) -> None:
    """Every stated post-condition. Raises on the first violation."""

    def require(condition: bool, rule: str) -> None:
        if not condition:
            raise PolicyViolation(f"{decision.request_id}: {rule}")

    status = decision.affordability_status
    method = decision.recommended_payment_method
    payments = decision.payments
    earliest = decision.earliest_date_for_full_payment

    require(
        _ZERO <= decision.amount_safe_to_pay <= request.requested_amount,
        "0 <= amount_safe_to_pay <= requested_amount",
    )

    if method is PaymentMethod.NOT_RECOMMENDED:
        require(not payments, "not_recommended must have payment_plan = none")
        require(
            status is AffordabilityStatus.NOT_AFFORDABLE,
            "not_recommended must pair with not_affordable",
        )
    else:
        require(payments != (), "a recommended method must carry a payment plan")

    if method in (
        PaymentMethod.FULL_PAYMENT,
        PaymentMethod.PARTIAL_PAYMENT,
        PaymentMethod.INSTALLMENTS,
    ):
        require(
            method in profile.payment_methods_user_will_consider,
            f"{method.value} is not in payment_methods_user_will_consider",
        )

    if method is PaymentMethod.WAIT:
        require(
            PaymentMethod.FULL_PAYMENT in profile.payment_methods_user_will_consider,
            "wait requires the user to accept full_payment",
        )
        require(
            status is AffordabilityStatus.AFFORDABLE_LATER,
            "wait must pair with affordable_later",
        )
        require(earliest is not None, "wait requires an earliest date")
        require(
            payments[0][0] == earliest,
            "a wait plan pays on earliest_date_for_full_payment",
        )

    if status is AffordabilityStatus.AFFORDABLE_NOW:
        require(
            earliest == request.request_date,
            "affordable_now requires earliest_date_for_full_payment == request_date",
        )
        require(
            method is PaymentMethod.FULL_PAYMENT,
            "affordable_now requires full_payment",
        )
        require(
            not decision.spending_changes,
            "affordable_now cannot require a spending change",
        )

    if method is PaymentMethod.PARTIAL_PAYMENT:
        require(
            status is AffordabilityStatus.AFFORDABLE_WITH_PLAN,
            "partial_payment requires affordable_with_plan",
        )
        require(request.allows_partial_payment, "the request must allow partial payment")
        require(len(payments) == 2, "partial_payment must have exactly two payments")
        require(
            payments[0][0] == request.request_date,
            "the first partial payment falls on request_date",
        )
        require(
            payments[1][0] == earliest,
            "the second partial payment falls on earliest_date_for_full_payment",
        )
        require(
            sum(amount for _, amount in payments) == request.requested_amount,
            "the two partial payments must sum to requested_amount",
        )
        require(
            _ZERO < decision.amount_safe_to_pay < request.requested_amount,
            "partial_payment requires 0 < amount_safe_to_pay < requested_amount",
        )
        require(
            earliest is not None and earliest <= request.desired_completion_date,
            "partial_payment must complete by desired_completion_date",
        )

    if payments:
        dates = [when for when, _ in payments]
        require(dates == sorted(dates), "payments must be chronological")
        require(
            dates[0] >= request.request_date,
            "no payment may fall before request_date",
        )

    require(len(decision.spending_changes) <= 3, "at most three spending changes")
    targets = [c.split(":")[1] for c in decision.spending_changes if ":" in c]
    require(
        len(set(targets)) == len(targets),
        "an event cannot be both stopped and reduced",
    )
