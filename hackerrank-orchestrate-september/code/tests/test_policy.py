"""Policy post-conditions. One test per condition, each asserting it RAISES.

A malformed row that reaches the output file is worse than a crash, because the
crash is visible. Every condition below is enforced inside ``decide`` and every
one of them is checked here by constructing a decision that breaks it.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

import _fixtures as fx
from bow.contracts import AffordabilityStatus, PaymentMethod, Request
from bow.engine.plans import PlanCandidate
from bow.engine.policy import Decision, PolicyViolation, decide

REQUEST_DATE = date(2026, 1, 5)
LATER = date(2026, 2, 20)


def request(**kwargs) -> Request:
    base = dict(
        request_id="r1", user_id="u1", request_date=REQUEST_DATE,
        request_type="purchase", requested_amount=Decimal("1000"),
        desired_completion_date=date(2026, 3, 1), allows_partial_payment=True,
        request_text="text",
    )
    base.update(kwargs)
    return Request(**base)


def plan(method, payments, **kwargs) -> PlanCandidate:
    return PlanCandidate(
        method=method, payments=payments,
        total_payable=sum(a for _, a in payments),
        completes_by_deadline=kwargs.pop("completes", True), **kwargs,
    )


ALL_METHODS = ("full_payment", "partial_payment", "installments")


# --- the happy paths, so the raises below mean something ----------------------


def test_full_payment_today_is_affordable_now():
    result = decide(
        request(), fx.profile(methods=ALL_METHODS),
        plan(PaymentMethod.FULL_PAYMENT, ((REQUEST_DATE, Decimal("1000")),)),
        amount_safe=Decimal("1000"), earliest_full_payment=REQUEST_DATE,
    )
    assert result.affordability_status is AffordabilityStatus.AFFORDABLE_NOW


def test_no_plan_is_not_affordable_and_not_recommended():
    result = decide(
        request(), fx.profile(methods=ALL_METHODS), None,
        amount_safe=Decimal("40"), earliest_full_payment=None,
    )
    assert result.affordability_status is AffordabilityStatus.NOT_AFFORDABLE
    assert result.recommended_payment_method is PaymentMethod.NOT_RECOMMENDED
    assert result.payments == ()


def test_wait_is_affordable_later():
    result = decide(
        request(), fx.profile(methods=ALL_METHODS),
        plan(PaymentMethod.WAIT, ((LATER, Decimal("1000")),)),
        amount_safe=Decimal("40"), earliest_full_payment=LATER,
    )
    assert result.affordability_status is AffordabilityStatus.AFFORDABLE_LATER


def test_full_payment_with_a_spending_change_is_affordable_with_plan():
    result = decide(
        request(), fx.profile(methods=ALL_METHODS),
        plan(PaymentMethod.FULL_PAYMENT, ((REQUEST_DATE, Decimal("1000")),)),
        amount_safe=Decimal("900"), earliest_full_payment=LATER,
        spending_changes=("stop:e1",),
    )
    assert result.affordability_status is AffordabilityStatus.AFFORDABLE_WITH_PLAN


# --- one test per post-condition, each must RAISE -----------------------------


def test_raises_when_amount_safe_exceeds_requested():
    with pytest.raises(PolicyViolation, match="amount_safe_to_pay"):
        decide(
            request(), fx.profile(methods=ALL_METHODS), None,
            amount_safe=Decimal("99999"), earliest_full_payment=None,
        )


def test_raises_when_amount_safe_is_negative():
    with pytest.raises(PolicyViolation, match="amount_safe_to_pay"):
        decide(
            request(), fx.profile(methods=ALL_METHODS), None,
            amount_safe=Decimal("-1"), earliest_full_payment=None,
        )


def test_raises_when_a_method_the_user_rejects_is_recommended():
    with pytest.raises(PolicyViolation, match="payment_methods_user_will_consider"):
        decide(
            request(), fx.profile(methods=("installments",)),
            plan(PaymentMethod.FULL_PAYMENT, ((REQUEST_DATE, Decimal("1000")),)),
            amount_safe=Decimal("1000"), earliest_full_payment=REQUEST_DATE,
        )


def test_raises_when_wait_is_used_by_a_user_who_rejects_full_payment():
    with pytest.raises(PolicyViolation, match="wait requires"):
        decide(
            request(), fx.profile(methods=("installments",)),
            plan(PaymentMethod.WAIT, ((LATER, Decimal("1000")),)),
            amount_safe=Decimal("40"), earliest_full_payment=LATER,
        )


def test_raises_when_wait_does_not_pay_on_the_earliest_date():
    with pytest.raises(PolicyViolation, match="earliest_date_for_full_payment"):
        decide(
            request(), fx.profile(methods=ALL_METHODS),
            plan(PaymentMethod.WAIT, ((date(2026, 2, 1), Decimal("1000")),)),
            amount_safe=Decimal("40"), earliest_full_payment=LATER,
        )


def test_raises_when_affordable_now_has_a_later_earliest_date():
    with pytest.raises(PolicyViolation, match="affordable_now requires earliest"):
        decide(
            request(), fx.profile(methods=ALL_METHODS),
            plan(PaymentMethod.FULL_PAYMENT, ((REQUEST_DATE, Decimal("1000")),)),
            amount_safe=Decimal("1000"), earliest_full_payment=LATER,
        )


def test_raises_when_partial_payment_has_the_wrong_number_of_payments():
    with pytest.raises(PolicyViolation, match="exactly two payments"):
        decide(
            request(), fx.profile(methods=ALL_METHODS),
            plan(PaymentMethod.PARTIAL_PAYMENT, ((REQUEST_DATE, Decimal("1000")),)),
            amount_safe=Decimal("400"), earliest_full_payment=LATER,
        )


def test_raises_when_partial_payments_do_not_sum_to_the_requested_amount():
    with pytest.raises(PolicyViolation, match="sum to requested_amount"):
        decide(
            request(), fx.profile(methods=ALL_METHODS),
            plan(PaymentMethod.PARTIAL_PAYMENT,
                 ((REQUEST_DATE, Decimal("400")), (LATER, Decimal("500")))),
            amount_safe=Decimal("400"), earliest_full_payment=LATER,
        )


def test_raises_when_partial_payment_is_used_on_a_request_that_forbids_it():
    with pytest.raises(PolicyViolation, match="allow partial payment"):
        decide(
            request(allows_partial_payment=False), fx.profile(methods=ALL_METHODS),
            plan(PaymentMethod.PARTIAL_PAYMENT,
                 ((REQUEST_DATE, Decimal("400")), (LATER, Decimal("600")))),
            amount_safe=Decimal("400"), earliest_full_payment=LATER,
        )


def test_raises_when_partial_payment_completes_after_the_deadline():
    late = date(2026, 6, 1)
    with pytest.raises(PolicyViolation, match="desired_completion_date"):
        decide(
            request(), fx.profile(methods=ALL_METHODS),
            plan(PaymentMethod.PARTIAL_PAYMENT,
                 ((REQUEST_DATE, Decimal("400")), (late, Decimal("600")))),
            amount_safe=Decimal("400"), earliest_full_payment=late,
        )


def test_raises_when_payments_are_out_of_order():
    with pytest.raises(PolicyViolation, match="chronological"):
        decide(
            request(), fx.profile(methods=ALL_METHODS),
            plan(PaymentMethod.INSTALLMENTS,
                 ((LATER, Decimal("500")), (REQUEST_DATE, Decimal("500")))),
            amount_safe=Decimal("400"), earliest_full_payment=REQUEST_DATE,
        )


def test_raises_when_a_payment_falls_before_the_request_date():
    with pytest.raises(PolicyViolation, match="before request_date"):
        decide(
            request(), fx.profile(methods=ALL_METHODS),
            plan(PaymentMethod.INSTALLMENTS,
                 ((REQUEST_DATE - timedelta(days=1), Decimal("1000")),)),
            amount_safe=Decimal("400"), earliest_full_payment=REQUEST_DATE,
        )


def test_raises_when_the_same_event_is_both_stopped_and_reduced():
    with pytest.raises(PolicyViolation, match="both stopped and reduced"):
        decide(
            request(), fx.profile(methods=ALL_METHODS),
            plan(PaymentMethod.FULL_PAYMENT, ((REQUEST_DATE, Decimal("1000")),)),
            amount_safe=Decimal("1000"), earliest_full_payment=REQUEST_DATE,
            spending_changes=("stop:e1", "reduce_to:e1:5"),
        )


def test_raises_on_more_than_three_spending_changes():
    with pytest.raises(PolicyViolation, match="at most three"):
        decide(
            request(), fx.profile(methods=ALL_METHODS),
            plan(PaymentMethod.FULL_PAYMENT, ((REQUEST_DATE, Decimal("1000")),)),
            amount_safe=Decimal("1000"), earliest_full_payment=REQUEST_DATE,
            spending_changes=("stop:e1", "stop:e2", "stop:e3", "stop:e4"),
        )


def test_raises_when_affordable_now_carries_a_spending_change():
    """A change means the money was not there without it, so not affordable_now."""
    with pytest.raises(PolicyViolation):
        decide.__wrapped__ if False else None
        from bow.engine.policy import _enforce

        _enforce(
            Decision(
                request_id="r1", amount_safe_to_pay=Decimal("1000"),
                affordability_status=AffordabilityStatus.AFFORDABLE_NOW,
                recommended_payment_method=PaymentMethod.FULL_PAYMENT,
                payments=((REQUEST_DATE, Decimal("1000")),),
                earliest_date_for_full_payment=REQUEST_DATE,
                spending_changes=("stop:e1",),
            ),
            request(), fx.profile(methods=ALL_METHODS),
        )


def test_raises_when_a_recommended_method_has_no_plan():
    from bow.engine.policy import _enforce

    with pytest.raises(PolicyViolation, match="must carry a payment plan"):
        _enforce(
            Decision(
                request_id="r1", amount_safe_to_pay=Decimal("0"),
                affordability_status=AffordabilityStatus.AFFORDABLE_NOW,
                recommended_payment_method=PaymentMethod.FULL_PAYMENT,
                payments=(), earliest_date_for_full_payment=REQUEST_DATE,
                spending_changes=(),
            ),
            request(), fx.profile(methods=ALL_METHODS),
        )


def test_raises_when_not_recommended_carries_a_plan():
    from bow.engine.policy import _enforce

    with pytest.raises(PolicyViolation, match="payment_plan = none"):
        _enforce(
            Decision(
                request_id="r1", amount_safe_to_pay=Decimal("0"),
                affordability_status=AffordabilityStatus.NOT_AFFORDABLE,
                recommended_payment_method=PaymentMethod.NOT_RECOMMENDED,
                payments=((REQUEST_DATE, Decimal("1000")),),
                earliest_date_for_full_payment=None, spending_changes=(),
            ),
            request(), fx.profile(methods=ALL_METHODS),
        )
