"""Enumerating structurally valid payment plans.

Pure. Every candidate is checked against the forecast before it is offered, so
nothing downstream has to re-verify safety.

Four shapes, and only four:

``full_payment``   the whole amount on ``request_date``
``installments``   exactly a supplied option's schedule, never a synthesised one
``partial_payment`` two payments; unlike installments this need not match an option
``wait``           the whole amount on the first date it becomes safe
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from bow.contracts.dataset import FinancialProfile, PaymentOption, Request
from bow.contracts.enums import HorizonEndBasis, PaymentMethod
from bow.contracts.scalars import IsoDate, Money
from bow.engine.capacity import is_safe_to_pay_on
from bow.engine.forecast import Forecast

__all__ = ["PlanCandidate", "enumerate_plans", "is_schedule_safe", "option_schedule"]

_ZERO = Decimal()


class PlanCandidate(BaseModel):
    """One structurally valid way to pay, already checked for safety."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    method: PaymentMethod
    payments: tuple[tuple[IsoDate, Money], ...]
    option_id: str | None = None
    total_payable: Money
    requires_spending_changes: bool = False
    completes_by_deadline: bool

    @property
    def first_payment_date(self) -> date:
        return self.payments[0][0]

    @property
    def last_payment_date(self) -> date:
        return self.payments[-1][0]

    @property
    def payment_count(self) -> int:
        return len(self.payments)


def option_schedule(option: PaymentOption) -> tuple[tuple[date, Decimal], ...]:
    """A supplied option's dates, derived from its own frequency.

    Installment plans must match a supplied option exactly, so the dates come
    from ``first_payment_date + k * payment_frequency_days`` and never from
    anything this module invents.
    """
    frequency = option.payment_frequency_days or 0
    return tuple(
        (
            option.first_payment_date + timedelta(days=frequency * index),
            option.payment_amount,
        )
        for index in range(option.number_of_payments)
    )


def is_schedule_safe(
    forecast: Forecast,
    payments: tuple[tuple[date, Decimal], ...],
    minimum_balance: Decimal,
) -> bool:
    """Does this whole schedule hold the floor at every point in the horizon?

    A multi-payment plan lowers the curve in steps, so it is not enough to
    check the trough once: each payment must leave the balance above the floor
    from its own date onward, cumulatively.
    """
    if not payments:
        return True
    if payments[-1][0] > forecast.horizon_end:
        return False

    checkpoints = sorted(
        {forecast.request_date, forecast.horizon_end}
        | {flow.date for flow in forecast.flows}
        | {when for when, _ in payments}
    )
    for moment in checkpoints:
        if moment < forecast.request_date or moment > forecast.horizon_end:
            continue
        paid = sum((amount for when, amount in payments if when <= moment), _ZERO)
        if forecast.balance_on(moment) - paid < minimum_balance:
            return False
    return True


def _accepts(profile: FinancialProfile, method: PaymentMethod) -> bool:
    return method in profile.payment_methods_user_will_consider


def _within_installment_cap(profile: FinancialProfile, option: PaymentOption) -> bool:
    """``Q2_installment_cap_basis`` = payment_count.

    A blank ``max_installment_months`` means the user will not consider
    installments at all, which the loader already cross-checks against
    ``payment_methods_user_will_consider``.
    """
    if profile.max_installment_months is None:
        return False
    return option.number_of_payments <= profile.max_installment_months


def enumerate_plans(
    request: Request,
    profile: FinancialProfile,
    options: tuple[PaymentOption, ...],
    forecast: Forecast,
    *,
    amount_safe: Decimal,
    earliest_full_payment: date | None,
    horizon_basis: HorizonEndBasis = HorizonEndBasis.FIXED_FROM_REQUEST_DATE,
) -> tuple[PlanCandidate, ...]:
    """Every plan that is both structurally valid and safe.

    Filtered by the user's accepted methods, by the installment cap, and by the
    forecast. A candidate that survives all three is genuinely payable.
    """
    minimum = profile.minimum_balance_to_keep
    candidates: list[PlanCandidate] = []

    # --- full payment today ---------------------------------------------------
    if _accepts(profile, PaymentMethod.FULL_PAYMENT) and is_safe_to_pay_on(
        forecast, request.request_date, request.requested_amount, minimum,
        horizon_basis=horizon_basis,
    ):
        candidates.append(
            PlanCandidate(
                method=PaymentMethod.FULL_PAYMENT,
                payments=((request.request_date, request.requested_amount),),
                option_id=next(
                    (
                        o.payment_option_id
                        for o in sorted(options, key=lambda x: x.payment_option_id)
                        if o.payment_method is PaymentMethod.FULL_PAYMENT
                    ),
                    None,
                ),
                total_payable=request.requested_amount,
                completes_by_deadline=request.request_date
                <= request.desired_completion_date,
            )
        )

    # --- one candidate per supplied option ------------------------------------
    for option in sorted(options, key=lambda o: o.payment_option_id):
        if option.payment_method is PaymentMethod.FULL_PAYMENT:
            continue  # already covered, and always dated request_date
        if not _accepts(profile, PaymentMethod.INSTALLMENTS):
            continue
        if not _within_installment_cap(profile, option):
            continue
        schedule = option_schedule(option)
        if not is_schedule_safe(forecast, schedule, minimum):
            continue
        candidates.append(
            PlanCandidate(
                method=PaymentMethod.INSTALLMENTS,
                payments=schedule,
                option_id=option.payment_option_id,
                total_payable=option.total_payable_amount,
                completes_by_deadline=schedule[-1][0] <= request.desired_completion_date,
            )
        )

    # --- partial payment ------------------------------------------------------
    # All five spec conditions, checked explicitly and in the spec's own terms.
    if (
        request.allows_partial_payment
        and _accepts(profile, PaymentMethod.PARTIAL_PAYMENT)
        and _ZERO < amount_safe < request.requested_amount
        and earliest_full_payment is not None
        and earliest_full_payment <= request.desired_completion_date
    ):
        remainder = request.requested_amount - amount_safe
        schedule = (
            (request.request_date, amount_safe),
            (earliest_full_payment, remainder),
        )
        if is_schedule_safe(forecast, schedule, minimum):
            candidates.append(
                PlanCandidate(
                    method=PaymentMethod.PARTIAL_PAYMENT,
                    payments=schedule,
                    total_payable=request.requested_amount,
                    completes_by_deadline=True,
                )
            )

    # --- wait -----------------------------------------------------------------
    if (
        _accepts(profile, PaymentMethod.FULL_PAYMENT)
        and earliest_full_payment is not None
        and earliest_full_payment > request.request_date
    ):
        candidates.append(
            PlanCandidate(
                method=PaymentMethod.WAIT,
                payments=((earliest_full_payment, request.requested_amount),),
                total_payable=request.requested_amount,
                completes_by_deadline=earliest_full_payment
                <= request.desired_completion_date,
            )
        )

    return tuple(candidates)
