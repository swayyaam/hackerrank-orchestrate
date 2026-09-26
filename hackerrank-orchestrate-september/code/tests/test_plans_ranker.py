"""Plan enumeration and the six-key ranking, on hand-built fixtures."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

import _fixtures as fx
from bow.contracts import EvidenceLedger, PaymentMethod, PaymentOption, Request
from bow.engine.forecast import build_forecast
from bow.engine.plans import PlanCandidate, enumerate_plans, is_schedule_safe, option_schedule
from bow.engine.ranker import RANKING_KEYS, best_plan, sort_key

REQUEST_DATE = date(2026, 1, 5)
HORIZON = 90
EMPTY = EvidenceLedger(request_id="r1")


def request(**kwargs) -> Request:
    base = dict(
        request_id="r1", user_id="u1", request_date=REQUEST_DATE,
        request_type="purchase", requested_amount=Decimal("1000"),
        desired_completion_date=date(2026, 4, 1), allows_partial_payment=True,
        request_text="t",
    )
    base.update(kwargs)
    return Request(**base)


def option(option_id="payment_option_01", *, method="installments", amount="350",
           n=3, first=REQUEST_DATE, freq=30, fee="50", total="1050") -> PaymentOption:
    return PaymentOption(
        payment_option_id=option_id, request_id="r1", payment_method=method,
        payment_amount=amount, number_of_payments=n, first_payment_date=first,
        payment_frequency_days=freq, financing_fee=fee, total_payable_amount=total,
    )


def forecast(balance="5000"):
    profile = fx.profile().model_copy(update={"current_available_balance": Decimal(balance)})
    return build_forecast(EMPTY, profile, (), REQUEST_DATE, HORIZON)


ALL = ("full_payment", "partial_payment", "installments")


# --- option schedules ---------------------------------------------------------


def test_option_dates_derive_from_first_payment_plus_frequency():
    schedule = option_schedule(option(n=3, freq=30))
    assert [d for d, _ in schedule] == [
        REQUEST_DATE, REQUEST_DATE + timedelta(days=30), REQUEST_DATE + timedelta(days=60)
    ]
    assert all(a == Decimal("350") for _, a in schedule)


def test_a_single_payment_option_is_one_dated_payment():
    schedule = option_schedule(
        option(method="full_payment", n=1, freq=None, amount="1000", fee="0", total="1000")
    )
    assert schedule == ((REQUEST_DATE, Decimal("1000")),)


# --- enumeration --------------------------------------------------------------


def test_full_payment_today_when_safe_and_accepted():
    plans = enumerate_plans(
        request(), fx.profile(methods=ALL), (), forecast(),
        amount_safe=Decimal("1000"), earliest_full_payment=REQUEST_DATE,
    )
    assert any(p.method is PaymentMethod.FULL_PAYMENT for p in plans)


def test_full_payment_is_not_offered_when_the_user_rejects_it():
    plans = enumerate_plans(
        request(), fx.profile(methods=("installments",), max_installment_months=6),
        (), forecast(), amount_safe=Decimal("1000"), earliest_full_payment=REQUEST_DATE,
    )
    assert not any(p.method is PaymentMethod.FULL_PAYMENT for p in plans)


def test_installments_filtered_by_the_installment_cap():
    profile = fx.profile(methods=ALL, max_installment_months=2)
    plans = enumerate_plans(
        request(), profile, (option(n=3),), forecast(),
        amount_safe=Decimal("1000"), earliest_full_payment=REQUEST_DATE,
    )
    assert not any(p.method is PaymentMethod.INSTALLMENTS for p in plans)


def test_installments_offered_within_the_cap():
    profile = fx.profile(methods=ALL, max_installment_months=6)
    plans = enumerate_plans(
        request(), profile, (option(n=3),), forecast(),
        amount_safe=Decimal("1000"), earliest_full_payment=REQUEST_DATE,
    )
    chosen = [p for p in plans if p.method is PaymentMethod.INSTALLMENTS]
    assert len(chosen) == 1
    assert chosen[0].option_id == "payment_option_01"
    assert chosen[0].total_payable == Decimal("1050")


def test_an_unsafe_schedule_is_not_offered():
    profile = fx.profile(methods=ALL, max_installment_months=6)
    plans = enumerate_plans(
        request(), profile, (option(n=3),), forecast(balance="200"),
        amount_safe=Decimal("0"), earliest_full_payment=None,
    )
    assert not any(p.method is PaymentMethod.INSTALLMENTS for p in plans)


@pytest.mark.parametrize(
    ("kwargs", "safe", "earliest"),
    [
        ({"allows_partial_payment": False}, Decimal("400"), date(2026, 2, 1)),
        ({}, Decimal("0"), date(2026, 2, 1)),
        ({}, Decimal("1000"), date(2026, 2, 1)),
        ({}, Decimal("400"), None),
        ({"desired_completion_date": date(2026, 1, 10)}, Decimal("400"), date(2026, 2, 1)),
    ],
    ids=["request forbids", "safe is zero", "safe is full", "no earliest", "past deadline"],
)
def test_partial_payment_needs_all_five_conditions(kwargs, safe, earliest):
    plans = enumerate_plans(
        request(**kwargs), fx.profile(methods=ALL), (), forecast(),
        amount_safe=safe, earliest_full_payment=earliest,
    )
    assert not any(p.method is PaymentMethod.PARTIAL_PAYMENT for p in plans)


def test_partial_payment_offered_when_all_five_hold():
    plans = enumerate_plans(
        request(), fx.profile(methods=ALL), (), forecast(),
        amount_safe=Decimal("400"), earliest_full_payment=date(2026, 2, 1),
    )
    partial = [p for p in plans if p.method is PaymentMethod.PARTIAL_PAYMENT]
    assert len(partial) == 1
    assert partial[0].payments[0] == (REQUEST_DATE, Decimal("400"))
    assert partial[0].payments[1] == (date(2026, 2, 1), Decimal("600"))
    assert sum(a for _, a in partial[0].payments) == Decimal("1000")


def test_wait_offered_when_full_payment_becomes_safe_later():
    plans = enumerate_plans(
        request(), fx.profile(methods=ALL), (), forecast(),
        amount_safe=Decimal("400"), earliest_full_payment=date(2026, 2, 1),
    )
    assert any(p.method is PaymentMethod.WAIT for p in plans)


def test_wait_not_offered_when_it_is_already_safe_today():
    plans = enumerate_plans(
        request(), fx.profile(methods=ALL), (), forecast(),
        amount_safe=Decimal("1000"), earliest_full_payment=REQUEST_DATE,
    )
    assert not any(p.method is PaymentMethod.WAIT for p in plans)


# --- schedule safety ----------------------------------------------------------


def test_a_schedule_is_checked_cumulatively_not_just_at_the_trough():
    """Two payments each individually affordable can be unaffordable together."""
    f = forecast(balance="1000")
    minimum = Decimal("100")
    assert is_schedule_safe(f, ((REQUEST_DATE, Decimal("800")),), minimum)
    assert not is_schedule_safe(
        f, ((REQUEST_DATE, Decimal("800")), (REQUEST_DATE + timedelta(days=1), Decimal("800"))),
        minimum,
    )


def test_a_schedule_running_past_the_horizon_is_not_safe():
    f = forecast()
    assert not is_schedule_safe(
        f, ((REQUEST_DATE + timedelta(days=HORIZON + 1), Decimal("10")),), Decimal("0")
    )


# --- the six ranking keys -----------------------------------------------------


def candidate(**kwargs) -> PlanCandidate:
    base = dict(
        method=PaymentMethod.INSTALLMENTS,
        payments=((REQUEST_DATE, Decimal("500")), (date(2026, 2, 4), Decimal("500"))),
        total_payable=Decimal("1000"), completes_by_deadline=True,
        requires_spending_changes=False, option_id="payment_option_05",
    )
    base.update(kwargs)
    return PlanCandidate(**base)


def test_there_are_exactly_six_ranking_keys():
    assert len(RANKING_KEYS) == 6
    assert len(sort_key(candidate())) == 6


def test_rule_1_completing_by_the_deadline_wins():
    on_time, late = candidate(), candidate(completes_by_deadline=False)
    assert best_plan((late, on_time)) is on_time


def test_rule_2_needing_no_spending_change_wins():
    clean, dirty = candidate(), candidate(requires_spending_changes=True)
    assert best_plan((dirty, clean)) is clean


def test_rule_3_paying_less_wins():
    cheap, dear = candidate(total_payable=Decimal("900")), candidate()
    assert best_plan((dear, cheap)) is cheap


def test_rule_4_starting_earlier_wins():
    early = candidate()
    late = candidate(payments=((date(2026, 2, 1), Decimal("1000")),))
    assert best_plan((late, early)) is early


def test_rule_5_fewer_payments_wins():
    few = candidate(payments=((REQUEST_DATE, Decimal("1000")),))
    many = candidate()
    assert best_plan((many, few)) is few


def test_rule_6_lowest_option_id_breaks_the_final_tie():
    low = candidate(option_id="payment_option_01")
    high = candidate(option_id="payment_option_99")
    assert best_plan((high, low)) is low


def test_earlier_rules_outrank_later_ones():
    """A cheaper plan that misses the deadline still loses to a dearer one that
    makes it: rule 1 outranks rule 3."""
    cheap_late = candidate(total_payable=Decimal("1"), completes_by_deadline=False)
    dear_on_time = candidate(total_payable=Decimal("99999"))
    assert best_plan((cheap_late, dear_on_time)) is dear_on_time


def test_ranking_is_deterministic_regardless_of_input_order():
    plans = (
        candidate(option_id="payment_option_03"),
        candidate(option_id="payment_option_01"),
        candidate(option_id="payment_option_02"),
    )
    assert best_plan(plans).option_id == "payment_option_01"
    assert best_plan(tuple(reversed(plans))).option_id == "payment_option_01"


def test_no_candidates_yields_no_plan():
    assert best_plan(()) is None


# --- rule 6: "lowest payment_option_id" ---------------------------------------


def test_option_ids_order_naturally_not_lexicographically():
    """A numeric suffix that is not fixed-width must still compare as a number.

    Sorting ids as plain strings puts a three-digit suffix before a two-digit
    one, which is the opposite of "lowest". The dataset's own id width changes
    partway through its range, so this is a live case, not a hypothetical.
    """
    from bow.engine.ranker import option_rank

    ids = ["x_9", "x_10", "x_100", "x_2"]
    assert sorted(ids, key=option_rank) == ["x_2", "x_9", "x_10", "x_100"]
    assert sorted(ids) != sorted(ids, key=option_rank)


def test_a_plan_without_an_option_id_ranks_after_every_plan_with_one():
    from bow.engine.ranker import option_rank

    assert option_rank(None) > option_rank("a_1")
    assert option_rank(None) > option_rank("z_999999")
    assert option_rank(None) == option_rank(None)


def test_option_rank_handles_ids_with_no_digits_and_mixed_shapes():
    from bow.engine.ranker import option_rank

    assert sorted(["b", "a", "a_2", "a_10"], key=option_rank) == [
        "a", "a_2", "a_10", "b",
    ]
