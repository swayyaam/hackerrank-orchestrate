"""Spending changes: target selection, verb selection, search and the hard cap."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from bow.contracts import Cadence, Direction, Flexibility, NotProjectedReason, SeriesClassification, SeriesKey
from bow.contracts.projection import ProjectedEvent
from bow.engine.changes import (
    ChangeCandidate,
    ChangePermission,
    apply_changes,
    candidate_changes,
    search_changes,
)

REQUEST = date(2026, 4, 1)

#: The default fixture category, authorised for both verbs. AGENTS.md 6.2 makes
#: permission a precondition, so every call has to state one; this is the
#: "the profile allows it" case, and the tests below cover the others.
ALLOWED = ChangePermission(
    protected=frozenset(),
    willing_to_reduce=frozenset({"streaming"}),
    willing_to_stop=frozenset({"streaming"}),
)


def permit(*, protect=(), reduce=(), stop=()) -> ChangePermission:
    return ChangePermission(
        protected=frozenset(protect),
        willing_to_reduce=frozenset(reduce),
        willing_to_stop=frozenset(stop),
    )


def series(label, flexibility, *, amount="100", floor=None, event_ids=("e1", "e2"),
           category="streaming", direction="debit"):
    return SeriesClassification(
        key=SeriesKey(user_id="u1", description=label, category=category,
                      direction=Direction(direction), event_type="subscription"),
        cadence=Cadence.MONTHLY_ANCHORED, occurrences=len(event_ids),
        occurrence_dates=(date(2026, 2, 1), date(2026, 3, 1))[: len(event_ids)],
        occurrence_event_ids=event_ids,
        gaps_days=(28,), first_occurrence=date(2026, 2, 1),
        last_occurrence=date(2026, 3, 1), age_days=31, currency="EUR",
        amounts=(Decimal(amount),) * len(event_ids),
        projected_amount=Decimal(amount), median_amount=Decimal(amount),
        projectable=True, not_projected_reason=None,
        flexibility=Flexibility(flexibility),
        minimum_allowed_amount=Decimal(floor) if floor else None,
    )


# --- target selection ---------------------------------------------------------


def test_the_target_is_the_most_recent_occurrence():
    """Q11: the latest occurrence, which is the last id in series order."""
    candidates = candidate_changes(
        [series("plan", "stoppable", event_ids=("old", "newest"))], permitted=ALLOWED
    )
    assert candidates[0].event_id == "newest"


def test_the_reduction_amount_is_the_events_own_floor_not_a_shortfall():
    candidates = candidate_changes([series("plan", "reducible", amount="47", floor="23.50")], permitted=ALLOWED)
    assert candidates[0].new_amount == Decimal("23.50")
    assert candidates[0].verb == "reduce_to"
    assert candidates[0].render() == "reduce_to:e2:23.50"


# --- verb selection -----------------------------------------------------------


def test_stoppable_is_stopped():
    candidates = candidate_changes([series("plan", "stoppable")], permitted=ALLOWED)
    assert candidates[0].verb == "stop"
    assert candidates[0].render() == "stop:e2"


def test_reducible_is_reduced():
    candidates = candidate_changes([series("plan", "reducible", floor="40")], permitted=ALLOWED)
    assert candidates[0].verb == "reduce_to"


def test_q29_default_reduces_a_both_series():
    candidates = candidate_changes(
        [series("plan", "reducible_or_stoppable", amount="47", floor="23.50")],
        permitted=ALLOWED,
        prefer_reduce=True,
    )
    assert candidates[0].verb == "reduce_to"
    assert candidates[0].new_amount == Decimal("23.50")


def test_q29_alternative_stops_a_both_series():
    candidates = candidate_changes(
        [series("plan", "reducible_or_stoppable", amount="47", floor="23.50")],
        permitted=ALLOWED,
        prefer_reduce=False,
    )
    assert candidates[0].verb == "stop"


def test_a_fixed_series_is_never_a_candidate():
    assert candidate_changes([series("plan", "fixed")], permitted=ALLOWED) == ()


def test_a_reducible_series_with_no_floor_is_not_a_candidate():
    assert candidate_changes([series("plan", "reducible", floor=None)], permitted=ALLOWED) == ()


def test_a_floor_at_or_above_the_amount_is_not_a_candidate():
    assert candidate_changes([series("plan", "reducible", amount="40", floor="40")], permitted=ALLOWED) == ()


def test_a_credit_series_is_never_a_candidate():
    assert candidate_changes([series("pay", "stoppable", direction="credit")], permitted=ALLOWED) == ()


# --- applying ------------------------------------------------------------------


def _projected(key, amount, n=3):
    return tuple(
        ProjectedEvent(date=date(2026, 5, k + 1), amount=Decimal(amount),
                       direction="debit", currency="EUR", series_key=key,
                       origin="inferred")
        for k in range(n)
    )


def test_stopping_removes_every_future_occurrence():
    target = series("plan", "stoppable")
    events = _projected(target.key, "100")
    candidate = candidate_changes([target], permitted=ALLOWED)[0]
    assert apply_changes(events, [candidate]) == ()


def test_reducing_rewrites_every_future_occurrence_to_the_floor():
    target = series("plan", "reducible", amount="100", floor="40")
    events = _projected(target.key, "100")
    candidate = candidate_changes([target], permitted=ALLOWED)[0]
    changed = apply_changes(events, [candidate])
    assert len(changed) == 3
    assert all(e.amount == Decimal("40") for e in changed)


def test_an_unrelated_series_is_untouched():
    target = series("plan", "stoppable")
    other = series("other", "fixed", category="rent")
    events = _projected(target.key, "100") + _projected(other.key, "500")
    candidate = candidate_changes([target], permitted=ALLOWED)[0]
    remaining = apply_changes(events, [candidate])
    assert len(remaining) == 3
    assert all(e.series_key == other.key for e in remaining)


# --- search and the cap --------------------------------------------------------


def _pool(n):
    return tuple(
        ChangeCandidate(
            event_id=f"e{i}",
            series_key=SeriesKey(user_id="u", description=f"s{i}", category="streaming",
                                 direction=Direction.DEBIT, event_type="subscription"),
            verb="stop", current_amount=Decimal("10"), new_amount=Decimal("0"),
        )
        for i in range(n)
    )


def test_search_returns_the_smallest_set_that_works():
    pool = _pool(4)
    found = search_changes(pool, lambda sel: len(sel) >= 2, max_changes=3)
    assert found is not None
    assert len(found.changes) == 2


def test_search_prefers_the_smaller_total_reduction_within_a_size():
    small = ChangeCandidate(
        event_id="small",
        series_key=SeriesKey(user_id="u", description="a", category="streaming",
                             direction=Direction.DEBIT, event_type="subscription"),
        verb="reduce_to", current_amount=Decimal("10"), new_amount=Decimal("9"),
    )
    large = ChangeCandidate(
        event_id="large",
        series_key=SeriesKey(user_id="u", description="b", category="streaming",
                             direction=Direction.DEBIT, event_type="subscription"),
        verb="reduce_to", current_amount=Decimal("100"), new_amount=Decimal("0"),
    )
    found = search_changes((large, small), lambda sel: True, max_changes=3)
    assert found.changes[0].event_id == "small"


def test_a_plan_needing_four_changes_is_rejected_not_truncated():
    """The cap is hard. Three of four changes does not make the plan safe."""
    pool = _pool(5)
    found = search_changes(pool, lambda sel: len(sel) >= 4, max_changes=3)
    assert found is None, "a four-change requirement must be rejected"


def test_no_event_is_ever_both_stopped_and_reduced():
    duplicate = ChangeCandidate(
        event_id="same",
        series_key=SeriesKey(user_id="u", description="a", category="streaming",
                             direction=Direction.DEBIT, event_type="subscription"),
        verb="reduce_to", current_amount=Decimal("10"), new_amount=Decimal("5"),
    )
    other = duplicate.model_copy(update={"verb": "stop", "new_amount": Decimal("0")})
    found = search_changes((duplicate, other), lambda sel: True, max_changes=3)
    assert len(found.changes) == 1


def test_an_empty_pool_yields_nothing():
    assert search_changes((), lambda sel: True, max_changes=3) is None


def test_nothing_safe_at_any_size_yields_nothing():
    assert search_changes(_pool(3), lambda sel: False, max_changes=3) is None


# --- against the published ground truth ---------------------------------------


@pytest.mark.dataset
def test_the_module_names_the_same_target_the_ground_truth_does():
    """request_06's published answer is `stop:event_476`. The enumeration must
    produce exactly that candidate from typed fields alone."""
    from bow import paths
    from bow.data.loaders import load_dataset
    from bow.data.normalize import normalize_events
    from bow.data.recurrence import project
    from bow.settings import load_thresholds

    dataset = load_dataset(paths.require_dataset_dir()).dataset
    sample = next(s for s in dataset.sample_requests if s.request_id == "request_06")
    profiles = {p.user_id: p for p in dataset.profiles}
    normalized = normalize_events(
        dataset.events, profiles=dataset.profiles,
        rates=dataset.exchange_rates, images=dataset.images,
    ).events
    owned = [e for e in normalized if e.user_id == sample.user_id]
    projection = project(
        owned, profiles[sample.user_id], sample.request_date, 90,
        thresholds=load_thresholds().recurrence,
    )
    rendered = {
        c.render()
        for c in candidate_changes(
            projection.series,
            permitted=ChangePermission.from_profile(profiles[sample.user_id]),
        )
    }
    assert sample.spending_changes_needed in rendered, (
        f"expected {sample.spending_changes_needed!r} among {sorted(rendered)}"
    )


# --- what the profile authorises (AGENTS.md 6.2) ------------------------------


def test_a_protected_category_is_never_changed_however_flexible_the_event():
    """Protection vetoes both verbs, even when the event says it is flexible."""
    for flexibility in ("stoppable", "reducible", "reducible_or_stoppable"):
        assert candidate_changes(
            [series("plan", flexibility, amount="47", floor="23.50")],
            permitted=permit(protect=("streaming",), reduce=("streaming",),
                             stop=("streaming",)),
        ) == (), flexibility


def test_a_category_outside_the_profile_lists_is_never_changed():
    """Flexibility alone is not permission; the category must be listed."""
    assert candidate_changes(
        [series("plan", "stoppable")], permitted=permit(reduce=("streaming",))
    ) == ()
    assert candidate_changes(
        [series("plan", "reducible", floor="40")],
        permitted=permit(stop=("streaming",)),
    ) == ()


def test_willingness_to_reduce_is_not_willingness_to_stop():
    """The two lists are read separately, not as one 'changeable' set."""
    candidates = candidate_changes(
        [series("plan", "reducible_or_stoppable", amount="47", floor="23.50")],
        permitted=permit(reduce=("streaming",)),
    )
    assert [c.verb for c in candidates] == ["reduce_to"]


def test_a_both_series_falls_back_to_the_permitted_verb():
    """Q29 states a preference; permission decides what is actually possible."""
    candidates = candidate_changes(
        [series("plan", "reducible_or_stoppable", amount="47", floor="23.50")],
        permitted=permit(stop=("streaming",)),
        prefer_reduce=True,
    )
    assert [c.verb for c in candidates] == ["stop"]


def test_permission_is_required_and_has_no_default():
    """A default would be a silent authorisation to change someone's spending."""
    with pytest.raises(TypeError):
        candidate_changes([series("plan", "stoppable")])


def test_permission_built_from_a_profile_uses_the_profiles_own_lists():
    from bow.contracts.dataset import FinancialProfile

    profile = FinancialProfile(
        user_id="u1", home_currency="EUR", current_available_balance=Decimal("1"),
        minimum_balance_to_keep=Decimal("0"), financial_priorities=(),
        expense_categories_to_protect=("rent",),
        expense_categories_user_is_willing_to_reduce=("groceries",),
        expense_categories_user_is_willing_to_stop=("streaming",),
        payment_methods_user_will_consider=(),
    )
    permission = ChangePermission.from_profile(profile)
    assert permission.allows("streaming", "stop")
    assert not permission.allows("streaming", "reduce_to")
    assert permission.allows("groceries", "reduce_to")
    assert not permission.allows("rent", "stop")
