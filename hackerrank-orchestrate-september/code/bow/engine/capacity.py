"""How much can safely be paid, and when the full amount becomes safe.

Pure, like the rest of ``engine/``. Consumes a :class:`Forecast` and nothing
else.

The closed form
---------------
A payment made on ``request_date`` shifts the whole balance curve down by the
same amount for every later date. So the largest safe payment is exactly the
headroom at the lowest point of the curve::

    amount_safe = clamp(trough(request_date, horizon_end) - minimum_balance,
                        0, requested_amount)

That identity is the reason a search is unnecessary. It is also an assumption
worth proving rather than believing, so :func:`amount_safe_by_search`
re-simulates each candidate and a property test asserts the two agree. If they
ever diverge, the closed form has acquired a coupling — a payment that changes
the curve's shape rather than its level — and that is worth knowing loudly.

``Q10_amount_safe_basis`` is read here. Under the default, ``standalone_today``,
this is spare cash above the floor across the horizon with no further
request-related outflow assumed; the figure is independent of whichever plan is
eventually recommended.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from bow.contracts.enums import (
    AmountSafeBasis,
    CashFlowSource,
    Direction,
    HorizonEndBasis,
)
from bow.engine.forecast import CashFlow, Forecast

__all__ = [
    "MINOR_UNIT",
    "amount_safe_by_search",
    "amount_safe_to_pay",
    "earliest_date_for_full_payment",
    "is_safe_to_pay_on",
]

_ZERO = Decimal()
_ONE_DAY = timedelta(days=1)

#: Smallest representable money increment. The binary search terminates here.
MINOR_UNIT = Decimal("0.01")


def _clamp(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(value, high))


def amount_safe_to_pay(
    forecast: Forecast,
    minimum_balance: Decimal,
    requested_amount: Decimal,
    *,
    basis: AmountSafeBasis = AmountSafeBasis.STANDALONE_TODAY,
) -> Decimal:
    """The largest amount payable on ``request_date`` that never breaks the floor.

    Closed form. Never negative: an opening balance already below the minimum
    yields zero, not a negative number, because you cannot pay a negative
    amount toward a purchase.
    """
    if basis is not AmountSafeBasis.STANDALONE_TODAY:
        raise NotImplementedError(
            "Q10_amount_safe_basis=first_leg_of_plan is not implemented; the "
            "figure would become plan-dependent, which the spec's wording "
            "excludes"
        )
    _, trough = forecast.trough(forecast.request_date, forecast.horizon_end)
    return _clamp(trough - minimum_balance, _ZERO, requested_amount)


def _window_end(forecast: Forecast, payment_date: date, basis: HorizonEndBasis) -> date:
    """Where the safety window ends for a payment made on ``payment_date``."""
    if basis is HorizonEndBasis.SLIDING_FROM_PAYMENT_DATE:
        span = forecast.horizon_end - forecast.request_date
        return payment_date + span
    return forecast.horizon_end


def is_safe_to_pay_on(
    forecast: Forecast,
    payment_date: date,
    amount: Decimal,
    minimum_balance: Decimal,
    *,
    horizon_basis: HorizonEndBasis = HorizonEndBasis.FIXED_FROM_REQUEST_DATE,
) -> bool:
    """Would paying ``amount`` on ``payment_date`` hold the floor afterwards?

    The payment lowers the curve from ``payment_date`` onward, so only the
    window from that date to the horizon end can be broken by it.
    """
    end = _window_end(forecast, payment_date, horizon_basis)
    if end < payment_date:
        return False
    _, trough = forecast.trough(payment_date, end)
    return trough - amount >= minimum_balance


def amount_safe_by_search(
    forecast: Forecast,
    minimum_balance: Decimal,
    requested_amount: Decimal,
    *,
    horizon_basis: HorizonEndBasis = HorizonEndBasis.FIXED_FROM_REQUEST_DATE,
) -> Decimal:
    """The same figure, found by binary search over re-simulated candidates.

    Exists to falsify the closed form, not to be used in production. Searches
    down to :data:`MINOR_UNIT`.
    """
    if not is_safe_to_pay_on(
        forecast, forecast.request_date, _ZERO, minimum_balance,
        horizon_basis=horizon_basis,
    ):
        return _ZERO
    if is_safe_to_pay_on(
        forecast, forecast.request_date, requested_amount, minimum_balance,
        horizon_basis=horizon_basis,
    ):
        return requested_amount

    low, high = _ZERO, requested_amount
    while high - low > MINOR_UNIT:
        middle = (low + high) / Decimal(2)
        middle = middle.quantize(MINOR_UNIT)
        if middle <= low or middle >= high:
            break
        if is_safe_to_pay_on(
            forecast, forecast.request_date, middle, minimum_balance,
            horizon_basis=horizon_basis,
        ):
            low = middle
        else:
            high = middle
    return low.quantize(MINOR_UNIT)


def earliest_date_for_full_payment(
    forecast: Forecast,
    minimum_balance: Decimal,
    requested_amount: Decimal,
    *,
    horizon_basis: HorizonEndBasis = HorizonEndBasis.FIXED_FROM_REQUEST_DATE,
) -> date | None:
    """First date in the horizon on which the full amount is safe.

    Computed without any spending change and independently of which payment
    methods the user will consider, exactly as the spec defines the column. It
    measures capacity, not preference, so it can name a date even when the
    recommendation ends up being something else entirely.

    ``None`` when the full amount never becomes safe inside the horizon.
    """
    candidate = forecast.request_date
    while candidate <= forecast.horizon_end:
        if is_safe_to_pay_on(
            forecast, candidate, requested_amount, minimum_balance,
            horizon_basis=horizon_basis,
        ):
            return candidate
        candidate += _ONE_DAY
    return None


def deduplicated_commitment_count(forecast: Forecast) -> dict[str, int]:
    """Given commitments in the curve, by direction.

    Reported so that a scheduled outflow silently replacing a projected one
    stays visible rather than becoming an unexplained change in the trough.
    """
    counts: dict[str, int] = {}
    for flow in forecast.flows_from(CashFlowSource.GIVEN_COMMITMENT):
        key = flow.direction.value
        counts[key] = counts.get(key, 0) + 1
    return counts
