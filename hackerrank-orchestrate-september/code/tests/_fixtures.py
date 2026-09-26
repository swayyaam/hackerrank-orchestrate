"""Hand-built fixtures. Nothing here reads ``dataset/``.

Builders take only what a test cares about and default the rest, so a test
reads as the one fact it is asserting.
"""

from __future__ import annotations

from bow.contracts import (
    ExchangeRate,
    FinancialEvent,
    FinancialProfile,
    ImageLink,
)
from bow.settings import RecurrenceThresholds

HOME = "INR"
FOREIGN = "USD"
OTHER = "EUR"


def profile(
    user_id: str = "u1",
    *,
    home_currency: str = HOME,
    reduce: tuple[str, ...] = ("dining",),
    stop: tuple[str, ...] = ("streaming",),
    protect: tuple[str, ...] = ("rent",),
    methods: tuple[str, ...] = ("full_payment",),
    max_installment_months: int | None = None,
) -> FinancialProfile:
    return FinancialProfile(
        user_id=user_id,
        home_currency=home_currency,
        current_available_balance="1000",
        minimum_balance_to_keep="100",
        financial_priorities=("emergency_savings",),
        expense_categories_to_protect=protect,
        expense_categories_user_is_willing_to_reduce=reduce,
        expense_categories_user_is_willing_to_stop=stop,
        payment_methods_user_will_consider=methods,
        max_installment_months=max_installment_months,
    )


def event(
    event_id: str,
    *,
    user_id: str = "u1",
    event_type: str = "expense",
    description: str = "a description that no rule may read",
    category: str = "groceries",
    direction: str = "debit",
    amount: str | None = "100",
    currency: str = HOME,
    event_date: str = "2026-01-10",
    settlement_date: str | None = "2026-01-10",
    status: str = "settled",
    linked_event_id: str | None = None,
    flexibility: str = "fixed",
    minimum_allowed_amount: str | None = None,
) -> FinancialEvent:
    return FinancialEvent(
        event_id=event_id,
        user_id=user_id,
        event_type=event_type,
        description=description,
        category=category,
        direction=direction,
        amount=amount,
        currency=currency,
        event_date=event_date,
        settlement_date=settlement_date,
        status=status,
        linked_event_id=linked_event_id,
        flexibility=flexibility,
        minimum_allowed_amount=minimum_allowed_amount,
    )


def chain(length: int, *, prefix: str = "c", **kwargs) -> list[FinancialEvent]:
    """A lifecycle chain of ``length`` events, each linked to the previous."""
    events = []
    for index in range(length):
        events.append(
            event(
                f"{prefix}{index}",
                linked_event_id=f"{prefix}{index - 1}" if index else None,
                **kwargs,
            )
        )
    return events


def rate(
    *,
    from_currency: str = FOREIGN,
    to_currency: str = HOME,
    rate_date: str = "2026-01-10",
    value: str = "80",
) -> ExchangeRate:
    return ExchangeRate(
        rate_date=rate_date,
        from_currency=from_currency,
        to_currency=to_currency,
        rate=value,
    )


def image(image_id: str = "img1", *, event_id: str = "e1", user_id: str = "u1") -> ImageLink:
    return ImageLink(
        image_id=image_id, user_id=user_id, request_id="r1", related_event_id=event_id
    )


def thresholds(
    *,
    monthly_gap_min_days: int = 26,
    monthly_gap_max_days: int = 32,
    day_of_month_tolerance_days: int = 2,
    fixed_interval_tolerance_days: int = 2,
    min_occurrences: int = 2,
    staleness_period_multiple: str = "1.5",
    amount_median_divergence_fraction: str = "0.25",
    max_period_days: int = 400,
    continuation_period_multiple: str = "2.0",
) -> RecurrenceThresholds:
    """Thresholds for a test. Explicit so each test states what it depends on.

    These mirror config/thresholds.yaml; test_recurrence.py asserts they agree,
    so a config change that breaks an assumption shows up here rather than
    silently diverging.
    """
    return RecurrenceThresholds(
        monthly_gap_min_days=monthly_gap_min_days,
        monthly_gap_max_days=monthly_gap_max_days,
        day_of_month_tolerance_days=day_of_month_tolerance_days,
        fixed_interval_tolerance_days=fixed_interval_tolerance_days,
        min_occurrences=min_occurrences,
        staleness_period_multiple=staleness_period_multiple,
        amount_median_divergence_fraction=amount_median_divergence_fraction,
        max_period_days=max_period_days,
        continuation_period_multiple=continuation_period_multiple,
    )


def series(dates, *, amounts=None, prefix="s", **kwargs):
    """A settled series: one event per date, optionally one amount per date."""
    if amounts is None:
        amounts = ["100"] * len(dates)
    return [
        event(
            f"{prefix}{index}",
            event_date=when,
            settlement_date=when,
            amount=amount,
            status="settled",
            **kwargs,
        )
        for index, (when, amount) in enumerate(zip(dates, amounts))
    ]
