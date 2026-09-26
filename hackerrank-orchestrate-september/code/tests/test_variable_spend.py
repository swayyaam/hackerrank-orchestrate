"""Variable-spending projection, on hand-built fixtures.

One fixture per cadence class, plus the exclusions that stop anything being
counted twice.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

import _fixtures as fx
from bow.contracts import (
    Cadence,
    CashFlowSource,
    Direction,
    EvidenceLedger,
    NotProjectedReason,
    SeriesClassification,
    SeriesKey,
)
from bow.engine.forecast import (
    build_forecast,
    trailing_rate,
    variable_spend_flows,
    variable_spend_warnings,
)
from bow.settings import load_thresholds

REQUEST = date(2026, 4, 1)
HORIZON = 90
WINDOW = 90
EMPTY = EvidenceLedger(request_id="r1")


def series(
    *,
    cadence: Cadence,
    reason: NotProjectedReason | None,
    projectable: bool = False,
    dates: tuple[str, ...],
    amounts: tuple[str, ...],
    direction: str = "debit",
    category: str = "groceries",
    label: str = "a label nothing reads",
) -> SeriesClassification:
    parsed = tuple(date.fromisoformat(d) for d in dates)
    return SeriesClassification(
        key=SeriesKey(
            user_id="u1", description=label, category=category,
            direction=Direction(direction), event_type="expense",
        ),
        cadence=cadence,
        occurrences=len(parsed),
        occurrence_dates=parsed,
        gaps_days=tuple(
            (b - a).days for a, b in zip(parsed, parsed[1:])
        ),
        first_occurrence=parsed[0],
        last_occurrence=parsed[-1],
        age_days=(REQUEST - parsed[-1]).days,
        currency="EUR",
        amounts=tuple(Decimal(a) for a in amounts),
        projected_amount=Decimal(amounts[-1]),
        median_amount=Decimal(amounts[-1]),
        projectable=projectable,
        not_projected_reason=reason,
    )


IRREGULAR = series(
    cadence=Cadence.IRREGULAR, reason=NotProjectedReason.IRREGULAR_GAPS,
    dates=("2026-01-10", "2026-02-02", "2026-02-20", "2026-03-15"),
    amounts=("90", "90", "90", "90"),
)
SINGLE = series(
    cadence=Cadence.SINGLE_OCCURRENCE, reason=NotProjectedReason.SINGLE_OCCURRENCE,
    dates=("2026-03-01",), amounts=("360",), label="one big purchase",
)
STALE = series(
    cadence=Cadence.MONTHLY_ANCHORED, reason=NotProjectedReason.STALE,
    dates=("2026-01-05", "2026-02-05"), amounts=("180", "180"), label="ended plan",
)
PROJECTED = series(
    cadence=Cadence.MONTHLY_ANCHORED, reason=None, projectable=True,
    dates=("2026-01-15", "2026-02-15", "2026-03-15"),
    amounts=("100", "100", "100"), label="already projected by cadence",
)
CREDIT = series(
    cadence=Cadence.IRREGULAR, reason=NotProjectedReason.IRREGULAR_GAPS,
    dates=("2026-02-01", "2026-03-01"), amounts=("500", "500"),
    direction="credit", category="salary", label="irregular income",
)
DORMANT = series(
    cadence=Cadence.IRREGULAR, reason=NotProjectedReason.IRREGULAR_GAPS,
    dates=("2025-01-10", "2025-02-10"), amounts=("900", "900"), label="long dormant",
)


def flows(*classes, irregular=True, single=False, stale=False):
    return variable_spend_flows(
        classes, REQUEST, HORIZON, window_days=WINDOW, min_occurrences_in_window=1,
        project_irregular=irregular, project_single_occurrence=single,
        project_stale=stale,
    )


def guarded(*classes, minimum=2, irregular=True, single=True, stale=True):
    return variable_spend_flows(
        classes, REQUEST, HORIZON, window_days=WINDOW,
        min_occurrences_in_window=minimum,
        project_irregular=irregular, project_single_occurrence=single,
        project_stale=stale,
    )


# --- the rate has no free coefficient -----------------------------------------


def test_rate_is_window_total_divided_by_window_length():
    rate = trailing_rate(IRREGULAR, request_date=REQUEST, window_days=WINDOW)
    assert rate.occurrences_in_window == 4
    assert rate.window_total == Decimal("360")
    assert rate.per_day == Decimal("360") / Decimal("90")
    assert rate.per_day == Decimal("4")


def test_occurrences_outside_the_window_contribute_nothing():
    rate = trailing_rate(DORMANT, request_date=REQUEST, window_days=WINDOW)
    assert rate.occurrences_in_window == 0
    assert rate.window_total == Decimal(0)
    assert rate.per_day == Decimal(0)


def test_a_dormant_series_is_self_correcting_even_when_admitted():
    emitted, _ = flows(DORMANT, irregular=True)
    assert emitted == (), "a series with nothing in the window must add nothing"


def test_no_free_coefficient_appears_in_the_source():
    """The rate is total/window. Any multiplier would be a fitted parameter."""
    import inspect

    from bow.engine import forecast

    source = inspect.getsource(forecast.trailing_rate)
    assert "total / Decimal(window_days)" in source


# --- one fixture per cadence class --------------------------------------------


def test_irregular_series_projects_when_q26_is_on():
    emitted, rates = flows(IRREGULAR, irregular=True)
    assert len(emitted) == HORIZON
    assert emitted[0].amount == Decimal("4")
    assert rates[0].cadence is Cadence.IRREGULAR


def test_irregular_series_projects_nothing_when_q26_is_off():
    emitted, _ = flows(IRREGULAR, irregular=False)
    assert emitted == ()


def test_single_occurrence_series_is_off_by_default():
    emitted, _ = flows(SINGLE, irregular=True, single=False)
    assert emitted == ()


def test_single_occurrence_series_projects_when_q27_is_on():
    emitted, _ = flows(SINGLE, irregular=True, single=True)
    assert emitted[0].amount == Decimal("360") / Decimal("90")


def test_stale_series_is_off_by_default():
    emitted, _ = flows(STALE, irregular=True, stale=False)
    assert emitted == ()


def test_stale_series_projects_when_q28_is_on():
    emitted, _ = flows(STALE, irregular=True, stale=True)
    assert emitted[0].amount == Decimal("360") / Decimal("90")


# --- exclusions: nothing counted twice ----------------------------------------


def test_a_series_already_projected_by_cadence_is_excluded():
    """The whole point of the exclusion: no double counting."""
    emitted, rates = flows(PROJECTED, irregular=True, single=True, stale=True)
    assert emitted == ()
    assert rates == ()


def test_cadence_and_rate_paths_never_cover_the_same_series():
    emitted, rates = flows(IRREGULAR, PROJECTED, irregular=True)
    labels = {r.series_key_label for r in rates}
    assert PROJECTED.key.label() not in labels
    assert IRREGULAR.key.label() in labels


def test_credit_series_are_never_treated_as_spending():
    emitted, rates = flows(CREDIT, irregular=True, single=True, stale=True)
    assert emitted == ()
    assert rates == ()


def test_given_commitments_are_separate_events_not_series():
    """Given commitments arrive as ProjectedEvents, never as classifications,
    so the rate path cannot see them and cannot double count them."""
    emitted, rates = flows(IRREGULAR, irregular=True)
    forecast = build_forecast(EMPTY, fx.profile(), (), REQUEST, HORIZON)
    combined = forecast.model_copy(update={"flows": forecast.flows + emitted})
    assert not combined.flows_from(CashFlowSource.GIVEN_COMMITMENT)
    assert len(combined.flows_from(CashFlowSource.VARIABLE_SPEND)) == HORIZON


# --- scope --------------------------------------------------------------------


def test_flexible_categories_are_included_in_the_baseline():
    """amount_safe_to_pay is defined BEFORE optional spending changes, so
    reducible and stoppable spending belongs in the baseline forecast."""
    flexible = series(
        cadence=Cadence.IRREGULAR, reason=NotProjectedReason.IRREGULAR_GAPS,
        dates=("2026-02-01", "2026-03-01"), amounts=("90", "90"),
        category="dining", label="weekend dining",
    )
    emitted, rates = flows(flexible, irregular=True)
    assert emitted, "flexible spending must not be excluded from the baseline"
    assert rates[0].category == "dining"


def test_flows_span_the_whole_horizon_and_stay_inside_it():
    emitted, _ = flows(IRREGULAR, irregular=True)
    assert emitted[0].date == REQUEST + timedelta(days=1)
    assert emitted[-1].date == REQUEST + timedelta(days=HORIZON)
    assert all(REQUEST < f.date <= REQUEST + timedelta(days=HORIZON) for f in emitted)


def test_variable_spend_has_its_own_source_and_is_never_a_commitment():
    emitted, _ = flows(IRREGULAR, irregular=True)
    assert all(f.source is CashFlowSource.VARIABLE_SPEND for f in emitted)
    assert all(f.direction is Direction.DEBIT for f in emitted)


# --- warnings -----------------------------------------------------------------


def _warnings(forecast, rates, opening):
    thresholds = load_thresholds().forecast
    return dict(
        (name, (count, detail))
        for name, count, detail in variable_spend_warnings(
            forecast, rates, opening_balance=Decimal(opening),
            warn_fraction_of_opening=thresholds.variable_spend_warn_fraction_of_opening,
            min_occurrences_in_window=thresholds.min_occurrences_in_trailing_window,
        )
    )


def test_warning_fires_when_variable_spend_dominates_the_opening_balance():
    emitted, rates = flows(IRREGULAR, irregular=True)   # 4/day x 90 = 360
    forecast = build_forecast(EMPTY, fx.profile(), (), REQUEST, HORIZON)
    forecast = forecast.model_copy(update={"flows": emitted})
    assert _warnings(forecast, rates, "500")["variable_spend_dominates_opening_balance"][0] == 1
    assert _warnings(forecast, rates, "5000")["variable_spend_dominates_opening_balance"][0] == 0


def test_warning_fires_when_the_forecast_goes_negative():
    emitted, rates = flows(IRREGULAR, irregular=True)
    profile = fx.profile().model_copy(update={"current_available_balance": Decimal("10")})
    forecast = build_forecast(EMPTY, profile, (), REQUEST, HORIZON)
    forecast = forecast.model_copy(update={"flows": emitted})
    assert _warnings(forecast, rates, "10")["forecast_goes_negative_in_horizon"][0] == 1


def test_warning_fires_on_a_thin_trailing_window():
    thin = series(
        cadence=Cadence.IRREGULAR, reason=NotProjectedReason.IRREGULAR_GAPS,
        dates=("2026-03-20",), amounts=("90",), label="one recent purchase",
    )
    emitted, rates = flows(thin, irregular=True, single=True)
    forecast = build_forecast(EMPTY, fx.profile(), (), REQUEST, HORIZON)
    forecast = forecast.model_copy(update={"flows": emitted})
    assert _warnings(forecast, rates, "1000")["trailing_window_with_too_few_occurrences"][0] == 1


def test_no_series_is_ever_projected_by_both_paths():
    emitted, rates = flows(IRREGULAR, PROJECTED, irregular=True)
    forecast = build_forecast(EMPTY, fx.profile(), (), REQUEST, HORIZON)
    forecast = forecast.model_copy(update={"flows": emitted})
    assert _warnings(forecast, rates, "1000")["series_projected_by_both_paths"][0] == 0


# --- the evidence guard -------------------------------------------------------


def test_a_window_with_too_few_occurrences_yields_no_rate():
    """One observation is not evidence, and scaling a non-observation down does
    not make it one. The series is not projected at all."""
    thin = series(
        cadence=Cadence.SINGLE_OCCURRENCE, reason=NotProjectedReason.SINGLE_OCCURRENCE,
        dates=("2026-03-20",), amounts=("900",), label="one large purchase",
    )
    assert guarded(thin, minimum=2)[0] == ()
    assert guarded(thin, minimum=1)[0] != ()


def test_the_guard_is_not_a_damping_coefficient():
    """A series that clears the guard is projected at its FULL rate."""
    emitted, _ = guarded(IRREGULAR, minimum=2)
    assert emitted[0].amount == Decimal("360") / Decimal("90")


def test_the_guard_only_removes_series_that_fail_it():
    thin = series(
        cadence=Cadence.SINGLE_OCCURRENCE, reason=NotProjectedReason.SINGLE_OCCURRENCE,
        dates=("2026-03-20",), amounts=("900",), label="one large purchase",
    )
    emitted, _ = guarded(IRREGULAR, thin, minimum=2)
    assert emitted[0].amount == Decimal("4"), "the qualifying series must survive intact"
