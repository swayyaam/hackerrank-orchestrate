"""Normalization behaviour, on hand-built fixtures. Nothing here reads ``dataset/``."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

import _fixtures as fx
from bow.contracts import (
    CashDateBasis,
    DropReason,
    KeepReason,
    PendingDuplicatePolicy,
)
from bow.data.normalize import (
    LifecycleCycleError,
    MissingCashDateError,
    NormalizationError,
    UnresolvableFX,
    cash_date,
    cash_treatment,
    collapse_lifecycles,
    convert_currency,
    drop_ignored,
    normalize_events,
)

# --- 1. lifecycle -------------------------------------------------------------


def test_three_link_chain_resolves_to_one_terminal():
    events = fx.chain(3)
    resolved = collapse_lifecycles(events)
    assert resolved.chains == (("c0", "c1", "c2"),)
    assert resolved.max_length == 3
    assert resolved.length_distribution == {"3": 1}
    assert resolved.terminals_of["c0"] == ("c2",)
    assert resolved.terminals_of["c2"] == ("c2",)
    assert resolved.parent_of == {"c1": "c0", "c2": "c1"}


def test_five_link_chain_resolves_to_one_terminal():
    resolved = collapse_lifecycles(fx.chain(5))
    assert resolved.chains == (("c0", "c1", "c2", "c3", "c4"),)
    assert resolved.max_length == 5
    assert resolved.terminals_of["c0"] == ("c4",)


def test_cycle_raises_rather_than_looping():
    events = [
        fx.event("a", linked_event_id="c"),
        fx.event("b", linked_event_id="a"),
        fx.event("c", linked_event_id="b"),
    ]
    with pytest.raises(LifecycleCycleError) as caught:
        collapse_lifecycles(events)
    assert set(caught.value.cycle) == {"a", "b", "c"}


def test_two_element_cycle_raises():
    events = [fx.event("a", linked_event_id="b"), fx.event("b", linked_event_id="a")]
    with pytest.raises(LifecycleCycleError):
        collapse_lifecycles(events)


def test_self_link_raises():
    with pytest.raises(LifecycleCycleError):
        collapse_lifecycles([fx.event("a", linked_event_id="a")])


def test_parent_with_two_children_yields_two_chains():
    events = [
        fx.event("root"),
        fx.event("kid_a", linked_event_id="root"),
        fx.event("kid_b", linked_event_id="root"),
    ]
    resolved = collapse_lifecycles(events)
    assert set(resolved.chains) == {("root", "kid_a"), ("root", "kid_b")}
    assert resolved.multi_child_parents == ("root",)
    assert set(resolved.terminals_of["root"]) == {"kid_a", "kid_b"}


def test_link_to_a_missing_event_is_reported_not_fatal():
    events = [fx.event("a", linked_event_id="ghost")]
    resolved = collapse_lifecycles(events)
    assert resolved.orphan_links == ("a",)
    assert resolved.parent_of == {}


def test_unlinked_events_form_no_chains():
    resolved = collapse_lifecycles([fx.event("a"), fx.event("b")])
    assert resolved.chains == ()
    assert resolved.length_distribution == {}


# --- 2. the six rules ---------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "direction", "expected"),
    [
        ("cancelled", "debit", DropReason.CANCELLED_OR_FAILED),
        ("failed", "debit", DropReason.CANCELLED_OR_FAILED),
        ("unrealized", "non_cash", DropReason.UNREALIZED_OR_NON_CASH),
        ("settled", "non_cash", DropReason.UNREALIZED_OR_NON_CASH),
        ("pending", "credit", DropReason.PENDING_CREDIT),
        ("pending", "debit", KeepReason.PENDING_DEBIT_RESERVED),
        ("scheduled", "debit", KeepReason.SCHEDULED_COMMITMENT),
        ("scheduled", "credit", KeepReason.SCHEDULED_COMMITMENT),
        ("settled", "debit", KeepReason.SETTLED),
        ("settled", "credit", KeepReason.SETTLED),
    ],
)
def test_one_fixture_per_ignore_rule(status, direction, expected):
    settlement = None if status == "unrealized" else "2026-01-10"
    outcome = cash_treatment(
        fx.event("e1", status=status, direction=direction, settlement_date=settlement)
    )
    assert outcome is expected


def test_the_six_rules_never_read_a_description():
    """Two events identical but for their description must be treated alike."""
    a = fx.event("a", description="Possible duplicate card charge", status="pending")
    b = fx.event("b", description="entirely unrelated prose", status="pending")
    assert cash_treatment(a) is cash_treatment(b)


def test_drop_ignored_reports_a_reason_for_every_drop():
    events = [
        fx.event("keep1", status="settled"),
        fx.event("keep2", status="scheduled"),
        fx.event("keep3", status="pending", direction="debit"),
        fx.event("drop1", status="cancelled"),
        fx.event("drop2", status="failed"),
        fx.event("drop3", status="pending", direction="credit"),
        fx.event("drop4", status="unrealized", direction="non_cash", settlement_date=None),
    ]
    result = drop_ignored(events)
    assert {k.event.event_id for k in result.kept} == {"keep1", "keep2", "keep3"}
    assert {d.event.event_id for d in result.dropped} == {"drop1", "drop2", "drop3", "drop4"}
    assert all(d.reason is not None for d in result.dropped)
    assert result.drop_counts == {
        "cancelled_or_failed": 2,
        "pending_credit": 1,
        "unrealized_or_non_cash": 1,
    }
    assert result.keep_counts == {
        "settled": 1,
        "scheduled_commitment": 1,
        "pending_debit_reserved": 1,
    }


# --- duplicates ---------------------------------------------------------------


def test_duplicate_differing_only_in_trailing_whitespace_is_dropped():
    first = fx.event("a", description="Card charge")
    second = fx.event("b", description="Card charge   ")
    result = drop_ignored([first, second])
    assert [k.event.event_id for k in result.kept] == ["a"]
    assert len(result.dropped) == 1
    assert result.dropped[0].reason is DropReason.EXACT_DUPLICATE
    assert "a" in result.dropped[0].detail


def test_duplicate_differing_in_interior_whitespace_is_dropped():
    result = drop_ignored(
        [fx.event("a", description="Card  charge"), fx.event("b", description="Card charge")]
    )
    assert len(result.dropped) == 1


def test_events_differing_in_a_real_field_are_not_duplicates():
    result = drop_ignored([fx.event("a", amount="100"), fx.event("b", amount="101")])
    assert len(result.kept) == 2
    assert result.dropped == ()


def test_two_genuine_same_day_purchases_are_not_treated_as_duplicates():
    """Same amount on the same day is normal; the id is what differs."""
    result = drop_ignored(
        [
            fx.event("a", category="groceries", amount="50"),
            fx.event("b", category="dining", amount="50"),
        ]
    )
    assert len(result.kept) == 2


# --- Q1 -----------------------------------------------------------------------


def _duplicate_pending_pair():
    return [
        fx.event("orig", status="settled", direction="debit", amount="100"),
        fx.event(
            "dup",
            status="pending",
            direction="debit",
            amount="100",
            linked_event_id="orig",
            event_date="2026-01-20",
            settlement_date="2026-01-22",
        ),
    ]


def test_q1_default_reserves_a_pending_duplicate_debit():
    result = drop_ignored(_duplicate_pending_pair())
    assert {k.event.event_id for k in result.kept} == {"orig", "dup"}


def test_q1_alternative_drops_a_pending_duplicate_debit():
    result = drop_ignored(
        _duplicate_pending_pair(),
        pending_duplicate_policy=PendingDuplicatePolicy.IGNORE_AS_DUPLICATE,
    )
    assert [k.event.event_id for k in result.kept] == ["orig"]
    assert result.dropped[0].reason is DropReason.LINKED_DUPLICATE_PENDING_DEBIT


def test_q1_alternative_keeps_an_unlinked_pending_debit():
    """Only a pending debit that restates a settled parent is a duplicate."""
    events = [fx.event("solo", status="pending", direction="debit", amount="100")]
    result = drop_ignored(
        events, pending_duplicate_policy=PendingDuplicatePolicy.IGNORE_AS_DUPLICATE
    )
    assert len(result.kept) == 1


def test_q1_alternative_keeps_a_pending_debit_of_a_different_amount():
    events = _duplicate_pending_pair()
    events[1] = events[1].model_copy(update={"amount": Decimal("999")})
    result = drop_ignored(
        events, pending_duplicate_policy=PendingDuplicatePolicy.IGNORE_AS_DUPLICATE
    )
    assert len(result.kept) == 2


# --- 3. currency --------------------------------------------------------------


def test_conversion_uses_the_exact_pair_on_the_exact_date():
    events = [fx.event("e1", currency=fx.FOREIGN, amount="10", settlement_date="2026-01-10")]
    result = convert_currency(events, [fx.rate(value="80")], [fx.profile()])
    assert result.converted == 1
    assert result.events[0].amount == Decimal("800")
    assert result.events[0].currency == fx.HOME


def test_home_currency_events_are_untouched():
    events = [fx.event("e1", currency=fx.HOME, amount="10")]
    result = convert_currency(events, [fx.rate()], [fx.profile()])
    assert result.converted == 0
    assert result.events[0] is events[0]


def test_missing_pair_raises_unresolvable_fx():
    events = [fx.event("e1", currency=fx.OTHER, amount="10")]
    with pytest.raises(UnresolvableFX) as caught:
        convert_currency(events, [fx.rate()], [fx.profile()])
    assert caught.value.from_currency == fx.OTHER
    assert caught.value.to_currency == fx.HOME
    assert not caught.value.reverse_exists


def test_reverse_only_pair_raises_and_says_so_rather_than_inverting():
    """The rate table is directionally asymmetric. Inverting would produce a
    plausible wrong number instead of an error."""
    events = [fx.event("e1", currency=fx.FOREIGN, amount="10")]
    reverse_only = [fx.rate(from_currency=fx.HOME, to_currency=fx.FOREIGN, value="0.0125")]
    with pytest.raises(UnresolvableFX) as caught:
        convert_currency(events, reverse_only, [fx.profile()])
    assert caught.value.reverse_exists
    assert "inverting it is not permitted" in str(caught.value)


def test_right_pair_wrong_date_raises_rather_than_using_a_nearby_rate():
    events = [fx.event("e1", currency=fx.FOREIGN, amount="10", settlement_date="2026-01-11")]
    with pytest.raises(UnresolvableFX) as caught:
        convert_currency(events, [fx.rate(rate_date="2026-01-10")], [fx.profile()])
    assert caught.value.on == date(2026, 1, 11)


def test_no_triangulation_through_a_third_currency():
    events = [fx.event("e1", currency=fx.OTHER, amount="10")]
    via_third = [
        fx.rate(from_currency=fx.OTHER, to_currency=fx.FOREIGN, value="1.1"),
        fx.rate(from_currency=fx.FOREIGN, to_currency=fx.HOME, value="80"),
    ]
    with pytest.raises(UnresolvableFX):
        convert_currency(events, via_third, [fx.profile()])


def test_conversion_records_the_rate_and_its_date_as_evidence():
    events = [fx.event("e1", currency=fx.FOREIGN, amount="10", settlement_date="2026-01-10")]
    result = convert_currency(events, [fx.rate(value="80")], [fx.profile()])
    entry = result.evidence[0]
    assert entry.normalized_value == Decimal("800")
    assert entry.provenance["rate"] == "80"
    assert entry.provenance["rate_date"] == "2026-01-10"
    assert entry.provenance["event_id"] == "e1"
    assert entry.provenance["cash_date_basis"] == "settlement_date"


def test_blank_amount_is_deferred_not_converted_and_never_zero():
    events = [fx.event("e1", currency=fx.FOREIGN, amount=None, settlement_date="2026-01-10")]
    result = convert_currency(events, [fx.rate()], [fx.profile()])
    assert result.converted == 0
    assert result.deferred == ("e1",)
    assert result.events[0].amount is None
    assert result.events[0].currency == fx.FOREIGN


def test_unknown_user_raises():
    events = [fx.event("e1", user_id="ghost", currency=fx.FOREIGN)]
    with pytest.raises(NormalizationError):
        convert_currency(events, [fx.rate()], [fx.profile()])


# --- Q16 ----------------------------------------------------------------------


def test_q16_default_keys_on_settlement_date():
    event = fx.event("e1", event_date="2026-01-05", settlement_date="2026-01-10")
    assert cash_date(event, CashDateBasis.SETTLEMENT_DATE) == date(2026, 1, 10)


def test_q16_alternative_keys_on_event_date():
    event = fx.event("e1", event_date="2026-01-05", settlement_date="2026-01-10")
    assert cash_date(event, CashDateBasis.EVENT_DATE) == date(2026, 1, 5)


def test_q16_changes_which_rate_the_conversion_finds():
    """The coupling recorded in the decision entry, demonstrated."""
    event = fx.event(
        "e1", currency=fx.FOREIGN, amount="10", event_date="2026-01-05",
        settlement_date="2026-01-10",
    )
    rates = [fx.rate(rate_date="2026-01-05", value="70"), fx.rate(rate_date="2026-01-10", value="80")]
    on_settlement = convert_currency([event], rates, [fx.profile()])
    on_event = convert_currency(
        [event], rates, [fx.profile()], basis=CashDateBasis.EVENT_DATE
    )
    assert on_settlement.events[0].amount == Decimal("800")
    assert on_event.events[0].amount == Decimal("700")


def test_blank_settlement_date_under_settlement_basis_raises():
    event = fx.event("e1", settlement_date=None)
    with pytest.raises(MissingCashDateError):
        cash_date(event, CashDateBasis.SETTLEMENT_DATE)


def test_blank_settlement_date_is_fine_under_event_basis():
    event = fx.event("e1", settlement_date=None)
    assert cash_date(event, CashDateBasis.EVENT_DATE) == date(2026, 1, 10)


# --- orchestration and warnings ----------------------------------------------


def test_dropping_runs_before_conversion_so_non_cash_never_needs_a_rate():
    """A non_cash row has no settlement date, so converting it would raise."""
    events = [
        fx.event(
            "valuation", status="unrealized", direction="non_cash",
            currency=fx.FOREIGN, settlement_date=None,
        )
    ]
    result = normalize_events(events, profiles=[fx.profile()], rates=[], images=[])
    assert result.events == ()
    assert result.drops.drop_counts == {"unrealized_or_non_cash": 1}


def test_blank_amount_warnings_count_missing_documents():
    events = [
        fx.event("has_image", amount=None),
        fx.event("no_image", amount=None),
    ]
    result = normalize_events(
        events, profiles=[fx.profile()], rates=[], images=[fx.image(event_id="has_image")]
    )
    assert result.report.warning("blank_amount_events").count == 2
    assert result.report.warning("blank_amount_without_image").count == 1


def test_flexible_event_outside_profile_lists_is_counted():
    events = [fx.event("e1", category="gym", flexibility="stoppable")]
    result = normalize_events(
        events, profiles=[fx.profile(stop=("streaming",))], rates=[], images=[]
    )
    assert result.report.warning("flexible_event_outside_profile_lists").count == 1


def test_fixed_event_in_a_changeable_category_is_informational_only():
    events = [fx.event("e1", category="dining", flexibility="fixed")]
    result = normalize_events(
        events, profiles=[fx.profile(reduce=("dining",))], rates=[], images=[]
    )
    assert result.report.warning("fixed_event_in_changeable_category").count == 1
    assert result.report.warning("flexible_event_outside_profile_lists").count == 0


def test_minimum_allowed_amount_not_below_amount_is_counted():
    events = [
        fx.event("e1", flexibility="reducible", amount="100", minimum_allowed_amount="100"),
        fx.event("e2", flexibility="reducible", amount="100", minimum_allowed_amount="50"),
    ]
    result = normalize_events(events, profiles=[fx.profile()], rates=[], images=[])
    assert result.report.warning("minimum_allowed_amount_not_below_amount").count == 1


def test_warnings_never_raise_even_when_every_one_fires():
    events = [
        fx.event("e1", flexibility="reducible", minimum_allowed_amount=None),
        fx.event("e2", flexibility="fixed", minimum_allowed_amount="10"),
        fx.event("e3", flexibility="stoppable", category="rent"),
        fx.event("e4", amount=None, settlement_date="2026-01-01", event_date="2026-01-05"),
    ]
    result = normalize_events(events, profiles=[fx.profile()], rates=[], images=[])
    assert result.report.triggered_warnings
    assert result.report.warning("settlement_date_before_event_date").count == 1


def test_long_chains_are_reported_not_assumed_away():
    result = normalize_events(fx.chain(4), profiles=[fx.profile()], rates=[], images=[])
    warning = result.report.warning("lifecycle_chain_length_distribution")
    assert warning.count == 1
    assert warning.detail == {"4": 1}
