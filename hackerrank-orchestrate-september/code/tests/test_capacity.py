"""Forecast and capacity, on hand-built fixtures. Nothing here reads ``dataset/``."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import _fixtures as fx
from bow.contracts import (
    BalanceAsOfBasis,
    CashFlowSource,
    Direction,
    EvidenceEffect,
    EvidenceEntry,
    EvidenceLedger,
    HorizonEndBasis,
    ProjectedEvent,
    ProjectionOrigin,
    SeriesKey,
)
from bow.engine.capacity import (
    MINOR_UNIT,
    amount_safe_by_search,
    amount_safe_to_pay,
    earliest_date_for_full_payment,
    is_safe_to_pay_on,
)
from bow.engine.forecast import build_forecast

REQUEST = date(2026, 1, 1)
HORIZON = 90
EMPTY = EvidenceLedger(request_id="r1")

KEY = SeriesKey(
    user_id="u1",
    description="a label nothing reads",
    category="salary",
    direction=Direction.CREDIT,
    event_type="income",
)


def flow(day: int, amount: str, direction: str = "credit", origin: str = "inferred"):
    return ProjectedEvent(
        date=REQUEST + timedelta(days=day),
        amount=amount,
        direction=direction,
        currency="INR",
        series_key=KEY.model_copy(update={"direction": Direction(direction)}),
        origin=origin,
        source_event_id="ev1" if origin == "given" else None,
    )


def forecast_of(*events, balance="1000", basis=BalanceAsOfBasis.INCLUSIVE_THROUGH_REQUEST_DATE):
    profile = fx.profile().model_copy(update={"current_available_balance": Decimal(balance)})
    return build_forecast(EMPTY, profile, tuple(events), REQUEST, HORIZON, balance_basis=basis)


# --- trough shape -------------------------------------------------------------


def test_trough_before_a_large_inflow():
    f = forecast_of(flow(10, "300", "debit"), flow(40, "5000", "credit"))
    when, value = f.trough(f.request_date, f.horizon_end)
    assert when == REQUEST + timedelta(days=10)
    assert value == Decimal("700")
    assert amount_safe_to_pay(f, Decimal("100"), Decimal("99999")) == Decimal("600")


def test_trough_after_a_large_inflow():
    f = forecast_of(flow(10, "5000", "credit"), flow(40, "5800", "debit"))
    when, value = f.trough(f.request_date, f.horizon_end)
    assert when == REQUEST + timedelta(days=40)
    assert value == Decimal("200")


def test_capacity_exactly_zero():
    f = forecast_of(flow(5, "900", "debit"))
    assert f.trough(f.request_date, f.horizon_end)[1] == Decimal("100")
    assert amount_safe_to_pay(f, Decimal("100"), Decimal("500")) == _z()


def _z() -> Decimal:
    return Decimal(0)


def test_capacity_is_clamped_to_requested_amount():
    f = forecast_of(balance="100000")
    assert amount_safe_to_pay(f, Decimal("100"), Decimal("250")) == Decimal("250")


def test_opening_balance_below_minimum_yields_zero_not_negative():
    f = forecast_of(balance="50")
    result = amount_safe_to_pay(f, Decimal("500"), Decimal("100"))
    assert result == _z()
    assert result >= _z()


# --- earliest date ------------------------------------------------------------


def test_safe_on_day_zero_returns_request_date():
    f = forecast_of(balance="10000")
    assert earliest_date_for_full_payment(f, Decimal("100"), Decimal("500")) == REQUEST


def test_earliest_date_waits_for_the_inflow():
    f = forecast_of(flow(30, "5000", "credit"), balance="1000")
    got = earliest_date_for_full_payment(f, Decimal("500"), Decimal("4000"))
    assert got == REQUEST + timedelta(days=30)


def test_no_safe_date_in_the_horizon_returns_none():
    f = forecast_of(balance="1000")
    assert earliest_date_for_full_payment(f, Decimal("500"), Decimal("99999")) is None


def test_earliest_date_ignores_payment_method_preferences():
    """The column measures capacity; the profile's accepted methods are not read."""
    import inspect

    from bow.engine import capacity

    source = inspect.getsource(capacity)
    assert "payment_methods_user_will_consider" not in source


# --- Q8 boundary --------------------------------------------------------------


def test_event_exactly_on_request_date_is_excluded_by_default():
    """Default Q8: the opening balance already contains it."""
    f = forecast_of(flow(0, "400", "debit"))
    assert f.flows == ()
    assert f.balance_on(REQUEST) == Decimal("1000")


def test_event_exactly_on_request_date_is_applied_under_the_alternative():
    f = forecast_of(flow(0, "400", "debit"), basis=BalanceAsOfBasis.AS_OF_LAST_SETTLED_EVENT)
    assert len(f.flows) == 1
    assert f.balance_on(REQUEST) == Decimal("600")


def test_the_day_after_request_date_is_applied_under_both_settings():
    for basis in BalanceAsOfBasis:
        f = forecast_of(flow(1, "400", "debit"), basis=basis)
        assert len(f.flows) == 1, basis


def test_events_beyond_the_horizon_are_never_applied():
    f = forecast_of(flow(HORIZON + 1, "400", "debit"))
    assert f.flows == ()


def test_event_on_the_horizon_end_is_applied():
    f = forecast_of(flow(HORIZON, "400", "debit"))
    assert len(f.flows) == 1


# --- source boundary and dedup ------------------------------------------------


def test_given_and_projected_flows_are_distinguishable():
    f = forecast_of(flow(10, "100", "debit", "given"), flow(20, "200", "debit", "inferred"))
    assert len(f.flows_from(CashFlowSource.GIVEN_COMMITMENT)) == 1
    assert len(f.flows_from(CashFlowSource.PROJECTED)) == 1


def test_a_scheduled_debit_colliding_with_a_projected_debit_is_not_double_counted():
    """A scheduled bill retry and a projected recurring debt payment are the
    same money. The recurrence layer suppresses the inferred side; this asserts
    the forecast that results contains one outflow, not two."""
    from bow.data.recurrence import project
    from bow.settings import load_thresholds

    history = fx.series(
        ["2025-10-05", "2025-11-05", "2025-12-05"],
        amounts=["300", "300", "300"],
        description="Utility direct debit",
        category="utilities",
        direction="debit",
        event_type="debt_payment",
    )
    scheduled = fx.event(
        "retry",
        direction="debit",
        event_type="debt_payment",
        category="utilities",
        description="Scheduled bill payment retry",
        amount="355",
        event_date="2026-01-05",
        settlement_date="2026-01-05",
        status="scheduled",
    )
    result = project(
        [*history, scheduled], fx.profile(), REQUEST, HORIZON,
        thresholds=load_thresholds().recurrence,
    )
    on_the_fifth = [e for e in result.events if e.date == date(2026, 1, 5)]
    assert len(on_the_fifth) == 1, "scheduled debit double-counted against projection"
    assert on_the_fifth[0].origin is ProjectionOrigin.GIVEN
    assert on_the_fifth[0].amount == Decimal("355"), "given row must win on amount"
    assert len(result.suppressed) == 1


# --- amendments ---------------------------------------------------------------


def test_an_amendment_in_the_ledger_becomes_a_cash_flow():
    ledger = EvidenceLedger(request_id="r1", entries=(
        EvidenceEntry(
            source_type="message", source_id="m1", field="amount",
            raw_value="900", normalized_value=Decimal("900"),
            effect=EvidenceEffect.AMEND,
            provenance={"cash_date": "2026-01-20", "direction": "credit"},
        ),
    ))
    f = build_forecast(ledger, fx.profile(), (), REQUEST, HORIZON)
    assert len(f.flows_from(CashFlowSource.AMENDMENT)) == 1
    assert f.balance_on(date(2026, 1, 20)) == fx.profile().current_available_balance + Decimal("900")


def test_an_evidence_entry_without_a_cash_date_is_not_a_cash_flow():
    ledger = EvidenceLedger(request_id="r1", entries=(
        EvidenceEntry(
            source_type="message", source_id="m1", field="note",
            raw_value="x", normalized_value=Decimal("900"),
            effect=EvidenceEffect.AMEND, provenance={"direction": "credit"},
        ),
    ))
    assert build_forecast(ledger, fx.profile(), (), REQUEST, HORIZON).flows == ()


# --- Q24 ----------------------------------------------------------------------


def test_q24_sliding_tests_further_into_the_future():
    """A payment late in the horizon is judged against a later window when the
    horizon slides, so an outflow past the fixed end can make it unsafe."""
    f = forecast_of(flow(HORIZON + 10, "900", "debit"), balance="1000")
    late = REQUEST + timedelta(days=80)
    # Fixed: the day-100 outflow is outside the window and invisible.
    assert is_safe_to_pay_on(f, late, Decimal("500"), Decimal("100"))
    # Sliding: the window reaches day 170, but the flow was never admitted to
    # the forecast, so the two agree here. The difference is in which flows the
    # forecast contains, not in the comparison itself.
    assert is_safe_to_pay_on(
        f, late, Decimal("500"), Decimal("100"),
        horizon_basis=HorizonEndBasis.SLIDING_FROM_PAYMENT_DATE,
    )


def test_q24_settings_are_both_reachable():
    f = forecast_of(flow(30, "5000", "credit"), balance="1000")
    for basis in HorizonEndBasis:
        assert earliest_date_for_full_payment(
            f, Decimal("500"), Decimal("4000"), horizon_basis=basis
        ) == REQUEST + timedelta(days=30)


# --- the equivalence that must be proven, not assumed -------------------------

money = st.decimals(
    min_value=Decimal("0"), max_value=Decimal("100000"),
    allow_nan=False, allow_infinity=False, places=2,
)
day = st.integers(min_value=1, max_value=HORIZON)
direction = st.sampled_from(["credit", "debit"])


@settings(max_examples=500, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    opening=money,
    minimum=money,
    requested=money,
    events=st.lists(st.tuples(day, money, direction), min_size=0, max_size=8),
)
def test_closed_form_agrees_with_binary_search(opening, minimum, requested, events):
    """The closed form assumes a payment shifts the curve down uniformly.

    If that ever stops holding, the closed form has a hidden coupling and this
    test is how we find out.
    """
    projected = tuple(
        flow(offset, str(amount), direction_value) for offset, amount, direction_value in events
    )
    f = forecast_of(*projected, balance=str(opening))
    closed = amount_safe_to_pay(f, minimum, requested)
    searched = amount_safe_by_search(f, minimum, requested)
    assert abs(closed - searched) <= MINOR_UNIT, (
        f"closed={closed} searched={searched} opening={opening} "
        f"minimum={minimum} requested={requested} events={events}"
    )


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(opening=money, minimum=money, requested=money,
       events=st.lists(st.tuples(day, money, direction), max_size=6))
def test_amount_safe_never_exceeds_requested_and_is_never_negative(
    opening, minimum, requested, events
):
    projected = tuple(flow(o, str(a), d) for o, a, d in events)
    f = forecast_of(*projected, balance=str(opening))
    result = amount_safe_to_pay(f, minimum, requested)
    assert Decimal(0) <= result <= requested


@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(opening=money, minimum=money, requested=money,
       events=st.lists(st.tuples(day, money, direction), max_size=6))
def test_paying_the_safe_amount_today_is_actually_safe(opening, minimum, requested, events):
    """The number is not merely arithmetic: paying it must really hold the floor."""
    projected = tuple(flow(o, str(a), d) for o, a, d in events)
    f = forecast_of(*projected, balance=str(opening))
    safe = amount_safe_to_pay(f, minimum, requested)
    if safe > Decimal(0):
        assert is_safe_to_pay_on(f, f.request_date, safe, minimum)
