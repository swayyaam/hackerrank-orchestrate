"""Cadence detection and projection, on hand-built fixtures.

Recurrence has no ground truth, so every rule is pinned by a fixture that
states the exact shape it is meant to recognise.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

import _fixtures as fx
from bow.contracts import Direction
from bow.data.recurrence import (
    Cadence,
    NotProjectedReason,
    ProjectionOrigin,
    aggregate_warnings,
    classify_series,
    group_series,
    project,
)
from bow.settings import load_thresholds

HORIZON = 90


def classify(dates, *, request_date, thresholds=None, amounts=None, use_median=False, **kwargs):
    events = fx.series(dates, amounts=amounts, **kwargs)
    grouped = group_series(events)
    assert len(grouped) == 1, "fixture should form exactly one series"
    key, occurrences = next(iter(grouped.items()))
    return classify_series(
        key,
        occurrences,
        request_date=date.fromisoformat(request_date),
        thresholds=thresholds or fx.thresholds(),
        use_median_amount=use_median,
    )


def project_one(events, *, request_date, thresholds=None, **kwargs):
    return project(
        events,
        fx.profile(),
        date.fromisoformat(request_date),
        HORIZON,
        thresholds=thresholds or fx.thresholds(),
        **kwargs,
    )


# --- the fixtures the brief names --------------------------------------------


def test_clean_monthly_series_anchored_on_the_fifteenth():
    result = classify(
        ["2026-01-15", "2026-02-15", "2026-03-15", "2026-04-15"],
        request_date="2026-04-20",
    )
    assert result.cadence is Cadence.MONTHLY_ANCHORED
    assert result.anchor_day == 15
    assert result.projectable
    assert result.gaps_days == (31, 28, 31)


def test_monthly_series_projects_by_anchor_not_by_adding_thirty_days():
    """Adding 30 days repeatedly drifts off the anchor within a quarter."""
    events = fx.series(["2026-01-15", "2026-02-15", "2026-03-15"])
    result = project_one(events, request_date="2026-03-20")
    days = [e.date for e in result.events]
    assert days == [date(2026, 4, 15), date(2026, 5, 15), date(2026, 6, 15)]
    assert all(d.day == 15 for d in days), "anchor drifted"


def test_series_anchored_on_the_thirty_first_clamps_across_february():
    events = fx.series(["2025-12-31", "2026-01-31"])
    result = project_one(events, request_date="2026-02-01")
    days = [e.date for e in result.events]
    # February clamps to the 28th, and March returns to the 31st rather than
    # staying clamped.
    assert days[0] == date(2026, 2, 28)
    assert days[1] == date(2026, 3, 31)
    assert days[2] == date(2026, 4, 30)


def test_leap_february_clamps_to_the_twenty_ninth():
    events = fx.series(["2023-12-31", "2024-01-31"])
    result = project_one(events, request_date="2024-02-01")
    assert result.events[0].date == date(2024, 2, 29)


def test_series_with_one_skipped_month_is_not_monthly_anchored():
    """A 59-day gap is outside the monthly band, so the series is irregular."""
    result = classify(
        ["2026-01-15", "2026-02-15", "2026-04-15"], request_date="2026-04-20"
    )
    assert result.cadence is Cadence.IRREGULAR
    assert not result.projectable
    assert result.not_projected_reason is NotProjectedReason.IRREGULAR_GAPS


def test_gaps_of_twenty_eight_thirty_and_thirty_one_are_monthly_when_anchored():
    result = classify(
        ["2026-01-31", "2026-02-28", "2026-03-30", "2026-04-30"],
        request_date="2026-05-01",
        thresholds=fx.thresholds(day_of_month_tolerance_days=3),
    )
    assert result.gaps_days == (28, 30, 31)
    assert result.cadence is Cadence.MONTHLY_ANCHORED


def test_a_twenty_eight_day_series_with_a_drifting_day_is_fixed_interval():
    """28-day gaps sit inside the monthly band but the anchor will not hold."""
    result = classify(
        ["2026-01-05", "2026-02-02", "2026-03-02", "2026-03-30"],
        request_date="2026-04-01",
    )
    assert result.cadence is Cadence.FIXED_INTERVAL
    assert result.period_days == 28
    assert result.anchor_unstable


def test_fixed_interval_series_projects_by_adding_the_period():
    events = fx.series(["2026-01-01", "2026-01-08", "2026-01-15"])
    result = project_one(events, request_date="2026-01-16")
    days = [e.date for e in result.events]
    assert days[:3] == [date(2026, 1, 22), date(2026, 1, 29), date(2026, 2, 5)]


def test_irregular_series_is_not_projected():
    events = fx.series(["2026-01-01", "2026-01-09", "2026-02-20", "2026-02-25"])
    result = project_one(events, request_date="2026-03-01")
    assert result.events == ()
    assert result.series[0].cadence is Cadence.IRREGULAR


def test_single_occurrence_series_is_not_projected():
    events = fx.series(["2026-01-15"])
    result = project_one(events, request_date="2026-02-01")
    assert result.events == ()
    assert result.series[0].cadence is Cadence.SINGLE_OCCURRENCE
    assert result.series[0].not_projected_reason is NotProjectedReason.SINGLE_OCCURRENCE


# --- Q18 ----------------------------------------------------------------------


def test_two_occurrence_series_projects_at_the_q18_default():
    result = classify(["2026-01-15", "2026-02-15"], request_date="2026-02-20")
    assert result.occurrences == 2
    assert result.projectable


def test_two_occurrence_series_is_silenced_at_the_q18_alternative():
    result = classify(
        ["2026-01-15", "2026-02-15"],
        request_date="2026-02-20",
        thresholds=fx.thresholds(min_occurrences=3),
    )
    assert not result.projectable
    assert result.not_projected_reason is NotProjectedReason.BELOW_MIN_OCCURRENCES


# --- Q19 ----------------------------------------------------------------------


def test_stale_series_just_inside_the_cutoff_still_projects():
    # period 31, multiple 1.5 -> anything up to 46 days old still projects.
    result = classify(
        ["2026-01-15", "2026-02-15"], request_date="2026-03-30"
    )
    assert result.age_days == 43
    assert result.projectable


def test_stale_series_just_outside_the_cutoff_is_excluded():
    result = classify(["2026-01-15", "2026-02-15"], request_date="2026-04-05")
    assert result.age_days == 49
    assert not result.projectable
    assert result.not_projected_reason is NotProjectedReason.STALE


def test_q19_alternative_projects_regardless_of_age():
    result = classify(
        ["2026-01-15", "2026-02-15"],
        request_date="2026-08-01",
        thresholds=fx.thresholds(staleness_period_multiple="9999"),
    )
    assert result.projectable


# --- Q20 ----------------------------------------------------------------------


def test_q20_default_projects_the_most_recent_amount():
    result = classify(
        ["2026-01-15", "2026-02-15", "2026-03-15"],
        request_date="2026-03-20",
        amounts=["100", "100", "150"],
    )
    assert result.projected_amount == Decimal("150")
    assert result.median_amount == Decimal("100")


def test_q20_alternative_projects_the_median():
    result = classify(
        ["2026-01-15", "2026-02-15", "2026-03-15"],
        request_date="2026-03-20",
        amounts=["100", "100", "150"],
        use_median=True,
    )
    assert result.projected_amount == Decimal("100")


def test_projected_amount_is_decimal_not_float():
    result = classify(
        ["2026-01-15", "2026-02-15"], request_date="2026-02-20", amounts=["0.1", "0.2"]
    )
    assert isinstance(result.projected_amount, Decimal)
    assert result.median_amount == Decimal("0.15")


# --- Q21 and dedup ------------------------------------------------------------


def _scheduled_plus_inferred():
    history = fx.series(
        ["2026-01-15", "2026-02-15", "2026-03-15"],
        amounts=["1000", "1000", "1000"],
        direction="credit",
        event_type="income",
        category="salary",
        description="Payroll credit",
    )
    scheduled = fx.event(
        "sched",
        direction="credit",
        event_type="income",
        category="salary",
        description="Next confirmed salary",
        amount="1200",
        event_date="2026-04-15",
        settlement_date="2026-04-15",
        status="scheduled",
    )
    return [*history, scheduled]


def test_scheduled_row_wins_and_the_inferred_occurrence_is_suppressed():
    result = project_one(_scheduled_plus_inferred(), request_date="2026-03-20")
    april = [e for e in result.events if e.date == date(2026, 4, 15)]
    assert len(april) == 1, "double counted"
    assert april[0].origin is ProjectionOrigin.GIVEN
    assert april[0].amount == Decimal("1200"), "scheduled row must win on amount"
    assert len(result.suppressed) == 1
    assert result.suppressed[0].origin is ProjectionOrigin.INFERRED


def test_q21_default_continues_income_past_the_scheduled_row():
    result = project_one(_scheduled_plus_inferred(), request_date="2026-03-20")
    inflow_dates = [e.date for e in result.inflows]
    assert date(2026, 4, 15) in inflow_dates
    assert date(2026, 5, 15) in inflow_dates, "income stopped at the scheduled row"
    assert date(2026, 6, 15) in inflow_dates


def test_q21_alternative_stops_income_at_the_scheduled_row():
    result = project_one(
        _scheduled_plus_inferred(),
        request_date="2026-03-20",
        income_continues_past_scheduled=False,
    )
    inflow_dates = [e.date for e in result.inflows]
    assert inflow_dates == [date(2026, 4, 15)]


def test_dedup_does_not_suppress_an_unrelated_outflow_on_the_same_day():
    history = fx.series(
        ["2026-01-10", "2026-02-10", "2026-03-10"], description="Rent", category="rent"
    )
    scheduled = fx.event(
        "sched",
        direction="credit",
        event_type="income",
        category="salary",
        description="Next confirmed salary",
        amount="500",
        event_date="2026-04-10",
        settlement_date="2026-04-10",
        status="scheduled",
    )
    result = project_one([*history, scheduled], request_date="2026-03-20")
    on_the_tenth = [e for e in result.events if e.date == date(2026, 4, 10)]
    assert len(on_the_tenth) == 2, "opposite directions must not collide"


# --- scope --------------------------------------------------------------------


def test_only_settled_history_informs_cadence():
    events = [
        *fx.series(["2026-01-15", "2026-02-15"]),
        fx.event("p", event_date="2026-03-15", settlement_date="2026-03-15", status="pending"),
    ]
    grouped = group_series(events)
    assert sum(len(v) for v in grouped.values()) == 2


def test_events_after_the_request_date_do_not_inform_cadence():
    events = fx.series(["2026-01-15", "2026-02-15", "2026-03-15", "2026-04-15"])
    result = project_one(events, request_date="2026-02-20")
    assert result.series[0].occurrences == 2
    assert result.series[0].last_occurrence == date(2026, 2, 15)


def test_projection_never_escapes_the_horizon():
    events = fx.series(["2026-01-15", "2026-02-15"])
    result = project_one(events, request_date="2026-02-20")
    assert all(result.request_date < e.date <= result.horizon_end for e in result.events)
    warnings = aggregate_warnings([result], thresholds=fx.thresholds())
    assert next(w for w in warnings if w.name == "projected_event_outside_horizon").count == 0


def test_nothing_here_touches_the_balance():
    """project() answers what falls in the horizon, never what the balance is."""
    import bow.data.recurrence as module

    source = module.__file__
    text = open(source, encoding="utf-8").read()
    for forbidden in ("current_available_balance", "minimum_balance_to_keep"):
        assert forbidden not in text, f"recurrence must not reference {forbidden}"


# --- warnings -----------------------------------------------------------------


def test_users_with_no_projected_income_are_counted():
    events = fx.series(["2026-01-10", "2026-02-10"])  # outflows only
    result = project_one(events, request_date="2026-02-20")
    warnings = aggregate_warnings([result], thresholds=fx.thresholds())
    assert next(w for w in warnings if w.name == "users_with_no_projected_income").count == 1


def test_amount_divergence_from_median_is_counted():
    events = fx.series(
        ["2026-01-15", "2026-02-15", "2026-03-15"], amounts=["100", "100", "500"]
    )
    result = project_one(events, request_date="2026-03-20")
    warnings = aggregate_warnings([result], thresholds=fx.thresholds())
    assert next(
        w for w in warnings if w.name == "projected_amount_diverges_from_median"
    ).count == 1


def test_config_thresholds_match_the_test_fixture():
    """A config change that breaks a test assumption must surface here."""
    configured = load_thresholds().recurrence
    assert configured == fx.thresholds()


# --- anchor spread across the month boundary ---------------------------------


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        ([15, 15, 15], 0),
        ([30, 31, 1], 2),   # wraps: 30, 31, 32
        ([1, 2, 31], 2),    # wraps: 31, 32, 33
        ([28, 29, 30], 2),
        ([31, 1], 1),
        ([1], 0),
    ],
)
def test_anchor_spread_wraps_around_the_month_boundary(days, expected):
    """The 1st and the 31st are one day apart, not thirty."""
    from bow.data.recurrence import _anchor_day_spread

    assert _anchor_day_spread(days) == expected


def test_anchor_spread_stays_large_for_a_genuinely_scattered_series():
    from bow.data.recurrence import _anchor_day_spread

    assert _anchor_day_spread([1, 15, 28]) > 10


def test_month_end_series_straddling_the_boundary_is_monthly_anchored():
    """A series on the last day of each month wraps to the 1st in short months
    and must still read as anchored rather than as a drifting interval."""
    result = classify(
        ["2025-10-31", "2025-11-30", "2025-12-31", "2026-01-31"],
        request_date="2026-02-05",
    )
    assert result.cadence is Cadence.MONTHLY_ANCHORED
    assert result.anchor_day_spread <= 2
