"""Recurrence inference: what cash events fall inside the horizon.

This is the highest-risk module in the system and it has no ground truth. Only
a minority of users carry an explicit scheduled income row; for everyone else
every future inflow in the forecast originates here. A wrong cadence does not
fail loudly. It produces a plausible balance curve and a wrong answer on every
output column, so the rules below are deliberately conservative and every one
of them is a named, configured decision rather than a judgement buried in code.

**Nothing here touches the balance.** This module answers only "what cash
events fall in the horizon", never "what is the balance".

Series identification
---------------------
Events are grouped by ``(user_id, description, category, direction,
event_type)``. The description is used as an **opaque grouping key**: equality
and hashing only. Nothing here reads what the string says — no substring test,
no keyword list, no regex over its content. ``tests/test_no_hardcoding.py``
distinguishes the two uses so the difference stays enforceable.

Only ``settled`` history informs cadence. A ``scheduled`` or ``pending`` row is
a commitment, not evidence of a pattern.

Month-end handling
------------------
A monthly-anchored series projects by anchoring to its day-of-month, never by
adding a fixed day count, because repeatedly adding 30 days drifts off the
anchor within a quarter. When the anchor day does not exist in a target month
it **clamps to the last day of that month**: a series anchored on the 31st
falls on 28 February (29 in a leap year), 30 April, and 31 May. Clamping never
carries forward — the following month returns to the 31st, because projection
is always computed from the anchor rather than from the previous projection.
"""

from __future__ import annotations

import calendar
import statistics
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Iterable, Sequence

from dateutil.relativedelta import relativedelta
from pydantic import BaseModel, ConfigDict

from bow.contracts import (
    Cadence,
    CurrencyCode,
    Direction,
    EventStatus,
    EventType,
    EvidenceEffect,
    EvidenceEntry,
    EvidenceSourceType,
    FinancialEvent,
    FinancialProfile,
    IsoDate,
    Money,
    NotProjectedReason,
    ProjectedEvent,
    ProjectionOrigin,
    SeriesClassification,
    SeriesKey,
    WarningCount,
)
from bow.settings import RecurrenceThresholds

__all__ = [
    "ContinuationLink",
    "ProjectedEvent",
    "ProjectionOrigin",
    "ProjectionResult",
    "SeriesKey",
    "aggregate_warnings",
    "classify_series",
    "group_series",
    "project",
]

_FIRST_DAY_OF_MONTH = 1
_ZERO = Decimal()





class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)




class ContinuationLink(_Frozen):
    """A stale income series continued by a newly started one.

    ``Q22_series_continuation``, scoped to income only.

    An employer change breaks a description-keyed series while the underlying
    income continues: the old payroll goes stale and the new one has a single
    occurrence, so neither projects and the user reads as having no income at
    all. Continuation inherits the cadence and anchor from the stale series and
    the amount from the new one.

    **Known limitation, stated where it is implemented.** The typed fields
    cannot distinguish an employer change from an employment ending. Both leave
    a stale income series and a new single-occurrence one, and telling them
    apart would require reading the description, which is banned. Continuation
    therefore also fires when someone has simply stopped being paid.
    """

    stale_key: SeriesKey
    successor_key: SeriesKey
    inherited_cadence: Cadence
    inherited_period_days: int
    inherited_anchor_day: int | None
    gap_days: int
    amount: Money



class ProjectionResult(_Frozen):
    """Everything projection produced for one user."""

    user_id: str
    request_date: IsoDate
    horizon_end: IsoDate
    events: tuple[ProjectedEvent, ...]
    series: tuple[SeriesClassification, ...]
    evidence: tuple[EvidenceEntry, ...]
    suppressed: tuple[ProjectedEvent, ...] = ()
    continuations: tuple[ContinuationLink, ...] = ()

    @property
    def inflows(self) -> tuple[ProjectedEvent, ...]:
        return tuple(e for e in self.events if e.direction is Direction.CREDIT)

    @property
    def outflows(self) -> tuple[ProjectedEvent, ...]:
        return tuple(e for e in self.events if e.direction is Direction.DEBIT)

    @property
    def has_projected_income(self) -> bool:
        return bool(self.inflows)


# --- series identification ----------------------------------------------------


def group_series(events: Iterable[FinancialEvent]) -> dict[SeriesKey, list[FinancialEvent]]:
    """Group settled history into series.

    Only ``settled`` rows with an amount inform a cadence. A scheduled or
    pending row is a commitment already recorded elsewhere, not evidence that a
    pattern exists.
    """
    grouped: dict[SeriesKey, list[FinancialEvent]] = defaultdict(list)
    for event in events:
        if event.status is not EventStatus.SETTLED or event.amount is None:
            continue
        key = SeriesKey(
            user_id=event.user_id,
            description=event.description,
            category=event.category,
            direction=event.direction,
            event_type=event.event_type,
        )
        grouped[key].append(event)
    for occurrences in grouped.values():
        occurrences.sort(key=lambda e: (e.event_date, e.event_id))
    return dict(grouped)


# --- cadence detection --------------------------------------------------------


def _gaps(occurrences: Sequence[FinancialEvent]) -> tuple[int, ...]:
    return tuple(
        (later.event_date - earlier.event_date).days
        for earlier, later in zip(occurrences, occurrences[1:])
    )


def _spread(values: Sequence[int]) -> int:
    return max(values) - min(values)


def _anchor_day_spread(days: Sequence[int]) -> int:
    """Spread of day-of-month values, measured on a calendar that wraps.

    The 1st and the 31st are one day apart across a month boundary, not thirty.
    Without wrapping, a series that lands on the last day of one month and the
    first of the next looks wildly unstable when it is perfectly regular.

    Computed as the smallest spread over every rotation: sort the days, then
    try lifting each leading run up by a month's length and take the tightest
    window that results.
    """
    if not days:
        return 0
    ordered = sorted(days)
    month_length = max(calendar.mdays)
    best = ordered[-1] - ordered[0]
    for cut in range(len(ordered)):
        rotated = ordered[cut:] + [day + month_length for day in ordered[:cut]]
        best = min(best, max(rotated) - min(rotated))
    return best


def classify_series(
    key: SeriesKey,
    occurrences: Sequence[FinancialEvent],
    *,
    request_date: date,
    thresholds: RecurrenceThresholds,
    use_median_amount: bool,
) -> SeriesClassification:
    """Decide how, or whether, one series repeats."""
    amounts = tuple(e.amount for e in occurrences if e.amount is not None)
    first = occurrences[0].event_date
    last = occurrences[-1].event_date
    age = (request_date - last).days
    gaps = _gaps(occurrences)
    days_of_month = [e.event_date.day for e in occurrences]
    median_amount = statistics.median(amounts) if amounts else None
    chosen = None
    if amounts:
        chosen = median_amount if use_median_amount else amounts[-1]

    common = {
        "key": key,
        "occurrences": len(occurrences),
        "occurrence_dates": tuple(e.event_date for e in occurrences),
        "occurrence_event_ids": tuple(e.event_id for e in occurrences),
        "flexibility": occurrences[-1].flexibility,
        "minimum_allowed_amount": occurrences[-1].minimum_allowed_amount,
        "gaps_days": gaps,
        "first_occurrence": first,
        "last_occurrence": last,
        "age_days": age,
        "currency": occurrences[-1].currency,
        "amounts": amounts,
        "median_amount": median_amount,
        "projected_amount": chosen,
    }

    if len(occurrences) < thresholds.min_occurrences:
        cadence = (
            Cadence.SINGLE_OCCURRENCE if len(gaps) == _ZERO else Cadence.IRREGULAR
        )
        reason = (
            NotProjectedReason.SINGLE_OCCURRENCE
            if cadence is Cadence.SINGLE_OCCURRENCE
            else NotProjectedReason.BELOW_MIN_OCCURRENCES
        )
        return SeriesClassification(
            cadence=cadence, projectable=False, not_projected_reason=reason, **common
        )

    in_monthly_band = all(
        thresholds.monthly_gap_min_days <= gap <= thresholds.monthly_gap_max_days
        for gap in gaps
    )
    anchor_spread = _anchor_day_spread(days_of_month)
    anchor_is_stable = anchor_spread <= thresholds.day_of_month_tolerance_days
    interval_is_stable = _spread(gaps) <= thresholds.fixed_interval_tolerance_days

    if in_monthly_band and anchor_is_stable:
        cadence = Cadence.MONTHLY_ANCHORED
        anchor_day = Counter(days_of_month).most_common()[0][0]
        period = round(statistics.median(gaps))
    elif interval_is_stable:
        cadence = Cadence.FIXED_INTERVAL
        anchor_day = None
        period = round(statistics.median(gaps))
    else:
        return SeriesClassification(
            cadence=Cadence.IRREGULAR,
            anchor_day_spread=anchor_spread,
            projectable=False,
            not_projected_reason=NotProjectedReason.IRREGULAR_GAPS,
            **common,
        )

    reason: NotProjectedReason | None = None
    if period > thresholds.max_period_days:
        reason = NotProjectedReason.PERIOD_TOO_LONG
    elif chosen is None:
        reason = NotProjectedReason.NO_AMOUNT
    elif Decimal(age) > thresholds.staleness_period_multiple * Decimal(period):
        reason = NotProjectedReason.STALE

    return SeriesClassification(
        cadence=cadence,
        period_days=period,
        anchor_day=anchor_day,
        anchor_day_spread=anchor_spread,
        projectable=reason is None,
        not_projected_reason=reason,
        anchor_unstable=in_monthly_band and not anchor_is_stable,
        **common,
    )


# --- projection ---------------------------------------------------------------


def find_continuations(
    classifications: Sequence[SeriesClassification],
    *,
    thresholds: RecurrenceThresholds,
) -> tuple[ContinuationLink, ...]:
    """Pair each stale income series with a newly started replacement.

    Typed fields only: same user, same category, same direction, same event
    type; the old series stale with a detected cadence; the new one too short
    to have a cadence of its own; and the handover falling within a configured
    multiple of the old period. The description is never read — the rule keys
    on the fact that the grouping key CHANGED, not on what it changed to.

    Income only, per Q22. A stale expense series reappearing under a new label
    is a different problem, and projecting nothing there is already right.
    """
    links: list[ContinuationLink] = []
    stale = [
        s
        for s in classifications
        if s.key.direction is Direction.CREDIT
        and s.not_projected_reason is NotProjectedReason.STALE
        and s.period_days is not None
    ]
    fresh = [
        s
        for s in classifications
        if s.key.direction is Direction.CREDIT
        and s.not_projected_reason
        in (NotProjectedReason.SINGLE_OCCURRENCE, NotProjectedReason.BELOW_MIN_OCCURRENCES)
        and s.projected_amount is not None
    ]
    claimed: set[SeriesKey] = set()
    for ended in sorted(stale, key=lambda s: s.last_occurrence, reverse=True):
        candidates = [
            s
            for s in fresh
            if s.key not in claimed
            and s.key.category == ended.key.category
            and s.key.event_type is ended.key.event_type
            and s.first_occurrence > ended.last_occurrence
        ]
        if not candidates:
            continue
        successor = min(candidates, key=lambda s: s.first_occurrence)
        gap = (successor.first_occurrence - ended.last_occurrence).days
        assert ended.period_days is not None
        if Decimal(gap) > thresholds.continuation_period_multiple * Decimal(
            ended.period_days
        ):
            continue
        claimed.add(successor.key)
        assert successor.projected_amount is not None
        links.append(
            ContinuationLink(
                stale_key=ended.key,
                successor_key=successor.key,
                inherited_cadence=ended.cadence,
                inherited_period_days=ended.period_days,
                inherited_anchor_day=ended.anchor_day,
                gap_days=gap,
                amount=successor.projected_amount,
            )
        )
    return tuple(links)


def _continued_series(
    link: ContinuationLink, classifications: Sequence[SeriesClassification]
) -> SeriesClassification:
    """The successor series, wearing the stale series' cadence."""
    successor = next(s for s in classifications if s.key == link.successor_key)
    return successor.model_copy(
        update={
            "cadence": link.inherited_cadence,
            "period_days": link.inherited_period_days,
            "anchor_day": link.inherited_anchor_day,
            "projectable": True,
            "not_projected_reason": None,
        }
    )


def _on_anchor(base: date, months_ahead: int, anchor_day: int) -> date:
    """The anchored day-of-month, clamped to the last day of a short month.

    Always computed from the anchor, never from the previous projection, so a
    clamp in February does not drag March back to the 28th.
    """
    target = date(base.year, base.month, _FIRST_DAY_OF_MONTH) + relativedelta(
        months=months_ahead
    )
    last_day = calendar.monthrange(target.year, target.month)[-1]
    return date(target.year, target.month, min(anchor_day, last_day))


def _occurrences_in_window(
    series: SeriesClassification, *, after: date, until: date
) -> list[date]:
    """Every occurrence strictly after ``after`` and on or before ``until``."""
    found: list[date] = []
    if series.cadence is Cadence.MONTHLY_ANCHORED:
        assert series.anchor_day is not None
        step = _FIRST_DAY_OF_MONTH
        while True:
            candidate = _on_anchor(series.last_occurrence, step, series.anchor_day)
            if candidate > until:
                break
            if candidate > after:
                found.append(candidate)
            step += _FIRST_DAY_OF_MONTH
            if candidate > until:
                break
        return found

    assert series.period_days is not None
    candidate = series.last_occurrence
    while True:
        candidate = candidate + timedelta(days=series.period_days)
        if candidate > until:
            break
        if candidate > after:
            found.append(candidate)
    return found


def _given_commitments(
    events: Sequence[FinancialEvent], *, after: date, until: date, basis_is_settlement: bool
) -> list[ProjectedEvent]:
    """Scheduled and reserved rows already in the data. Given, not inferred."""
    out: list[ProjectedEvent] = []
    for event in events:
        if event.status not in (EventStatus.SCHEDULED, EventStatus.PENDING):
            continue
        if event.status is EventStatus.PENDING and event.direction is Direction.CREDIT:
            continue
        if event.amount is None:
            continue
        when = (
            event.settlement_date
            if basis_is_settlement and event.settlement_date is not None
            else event.event_date
        )
        if not (after < when <= until):
            continue
        out.append(
            ProjectedEvent(
                date=when,
                amount=event.amount,
                direction=event.direction,
                currency=event.currency,
                series_key=SeriesKey(
                    user_id=event.user_id,
                    description=event.description,
                    category=event.category,
                    direction=event.direction,
                    event_type=event.event_type,
                ),
                origin=ProjectionOrigin.GIVEN,
                source_event_id=event.event_id,
            )
        )
    return out


def _collides(given: ProjectedEvent, inferred: ProjectedEvent, tolerance_days: int) -> bool:
    """Do a given commitment and an inferred projection describe one occurrence?

    Matched on direction, currency and date proximity. **Amount is deliberately
    not part of the match**: the whole point of the rule is that the given row
    wins on amount, so a disagreement about amount is exactly when the override
    must happen rather than a reason to treat them as different events.
    """
    return (
        given.direction is inferred.direction
        and given.currency == inferred.currency
        and abs((given.date - inferred.date).days) <= tolerance_days
    )


def project(
    events: Sequence[FinancialEvent],
    profile: FinancialProfile,
    request_date: date,
    horizon_days: int,
    *,
    thresholds: RecurrenceThresholds,
    use_median_amount: bool = False,
    income_continues_past_scheduled: bool = True,
    continue_income_series: bool = True,
    basis_is_settlement: bool = True,
) -> ProjectionResult:
    """Project the cash events falling inside the horizon for one user.

    Given commitments already in the data win over anything inferred: where the
    two predict the same occurrence the given row supplies both the date and the
    amount, and the inferred one is suppressed rather than added.
    """
    horizon_end = request_date + timedelta(days=horizon_days)
    owned = [e for e in events if e.user_id == profile.user_id]
    history = [e for e in owned if e.event_date <= request_date]

    grouped = group_series(history)
    classifications = tuple(
        classify_series(
            key,
            occurrences,
            request_date=request_date,
            thresholds=thresholds,
            use_median_amount=use_median_amount,
        )
        for key, occurrences in sorted(grouped.items(), key=lambda kv: kv[0].label())
    )

    given = _given_commitments(
        owned, after=request_date, until=horizon_end, basis_is_settlement=basis_is_settlement
    )

    links = (
        find_continuations(classifications, thresholds=thresholds)
        if continue_income_series
        else ()
    )
    continued = {link.successor_key: _continued_series(link, classifications) for link in links}
    projectable_series = tuple(
        continued.get(series.key, series) for series in classifications
    )

    inferred: list[ProjectedEvent] = []
    for series in projectable_series:
        if not series.projectable or series.projected_amount is None:
            continue
        for when in _occurrences_in_window(series, after=request_date, until=horizon_end):
            inferred.append(
                ProjectedEvent(
                    date=when,
                    amount=series.projected_amount,
                    direction=series.key.direction,
                    currency=series.currency,
                    series_key=series.key,
                    origin=ProjectionOrigin.INFERRED,
                )
            )

    # Q21: when a scheduled income row is treated as the only future income,
    # inferred inflows on or after the earliest such row are withheld.
    if not income_continues_past_scheduled:
        scheduled_inflows = [g for g in given if g.direction is Direction.CREDIT]
        if scheduled_inflows:
            cutoff = min(g.date for g in scheduled_inflows)
            inferred = [
                e
                for e in inferred
                if not (e.direction is Direction.CREDIT and e.date >= cutoff)
            ]

    kept: list[ProjectedEvent] = []
    suppressed: list[ProjectedEvent] = []
    for candidate in inferred:
        if any(
            _collides(g, candidate, thresholds.day_of_month_tolerance_days) for g in given
        ):
            suppressed.append(candidate)
        else:
            kept.append(candidate)

    combined = sorted(given + kept, key=lambda e: (e.date, e.series_key.label()))
    evidence = tuple(_evidence_for(e, projectable_series, links) for e in combined)

    return ProjectionResult(
        user_id=profile.user_id,
        request_date=request_date,
        horizon_end=horizon_end,
        events=tuple(combined),
        series=projectable_series,
        evidence=evidence,
        suppressed=tuple(suppressed),
        continuations=links,
    )


def _evidence_for(
    projected: ProjectedEvent,
    classifications: Sequence[SeriesClassification],
    links: Sequence[ContinuationLink] = (),
) -> EvidenceEntry:
    """One ledger entry per projected event, recording what produced it."""
    series = next((s for s in classifications if s.key == projected.series_key), None)
    provenance: dict[str, object] = {
        "origin": projected.origin.value,
        "series": projected.series_key.label(),
        "direction": projected.direction.value,
        "currency": projected.currency,
    }
    if projected.origin is ProjectionOrigin.GIVEN:
        provenance["source_event_id"] = projected.source_event_id
        provenance["decisions"] = ["Q21_income_beyond_scheduled_row"]
    link = next((x for x in links if x.successor_key == projected.series_key), None)
    if link is not None:
        provenance["continued_from"] = link.stale_key.label()
        provenance["continuation_gap_days"] = link.gap_days
    if series is not None:
        provenance.update(
            {
                "cadence": series.cadence.value,
                "anchor_day": series.anchor_day,
                "period_days": series.period_days,
                "observed_occurrences": series.occurrences,
                "last_observed": series.last_occurrence.isoformat(),
                "decisions": [
                    "Q18_min_occurrences_for_cadence",
                    "Q19_series_staleness",
                    "Q20_projected_amount",
                    "Q21_income_beyond_scheduled_row",
                    "Q22_series_continuation",
                    "Q23_irregular_income_treatment",
                ],
            }
        )
    return EvidenceEntry(
        source_type=EvidenceSourceType.EVENT,
        source_id=projected.source_event_id or projected.series_key.label(),
        field="projected_cash_event",
        raw_value=f"{projected.amount} {projected.currency} on {projected.date.isoformat()}",
        normalized_value=projected.amount,
        effect=(
            EvidenceEffect.CONFIRM
            if projected.origin is ProjectionOrigin.GIVEN
            else EvidenceEffect.CLARIFY
        ),
        provenance=provenance,
    )


# --- warning-level assertions -------------------------------------------------
#
# Each reports a count and never raises. Recurrence has no ground truth, so
# these are the only way a wrong cadence becomes visible before it has already
# shaped a balance curve.


def _warn(name: str, count: int, note: str, detail: dict[str, int] | None = None) -> WarningCount:
    return WarningCount(name=name, count=count, detail=detail or {}, note=note)


def aggregate_warnings(
    results: Sequence[ProjectionResult], *, thresholds: RecurrenceThresholds
) -> tuple[WarningCount, ...]:
    """Warning-level assertions across every projection in a run."""
    no_income = [r.user_id for r in results if not r.has_projected_income]

    irregular_by_category: Counter[str] = Counter()
    stale_ages: Counter[str] = Counter()
    divergent: Counter[str] = Counter()
    unstable_anchor: Counter[str] = Counter()
    cadence_counts: Counter[str] = Counter()

    for result in results:
        for series in result.series:
            cadence_counts[series.cadence.value] += _FIRST_DAY_OF_MONTH
            if series.cadence is Cadence.IRREGULAR:
                irregular_by_category[series.key.category] += _FIRST_DAY_OF_MONTH
            if series.not_projected_reason is NotProjectedReason.STALE:
                stale_ages[str(series.age_days)] += _FIRST_DAY_OF_MONTH
            if series.anchor_unstable:
                unstable_anchor[series.key.category] += _FIRST_DAY_OF_MONTH
            if (
                series.projected_amount is not None
                and series.median_amount is not None
                and series.median_amount != _ZERO
            ):
                divergence = abs(
                    series.projected_amount - series.median_amount
                ) / abs(series.median_amount)
                if divergence > thresholds.amount_median_divergence_fraction:
                    divergent[series.key.category] += _FIRST_DAY_OF_MONTH

    out_of_horizon = [
        f"{r.user_id}:{e.date.isoformat()}"
        for r in results
        for e in r.events
        if not (r.request_date < e.date <= r.horizon_end)
    ]
    suppressed = sum(len(r.suppressed) for r in results)

    return (
        _warn(
            "users_with_no_projected_income",
            len(no_income),
            "these users look permanently broke to the forecast; every one is a "
            "user whose income the cadence rules failed to find",
        ),
        _warn(
            "series_classified_irregular",
            sum(irregular_by_category.values()),
            "gaps that do not cluster; not projected",
            dict(irregular_by_category),
        ),
        _warn(
            "series_excluded_by_staleness",
            sum(stale_ages.values()),
            "detected but too old to still be running, per Q19_series_staleness",
            dict(stale_ages),
        ),
        _warn(
            "projected_amount_diverges_from_median",
            sum(divergent.values()),
            "Q20_projected_amount picked a figure far from the series median; a "
            "price change or a one-off spike",
            dict(divergent),
        ),
        _warn(
            "monthly_series_with_unstable_anchor",
            sum(unstable_anchor.values()),
            "gaps sat in the monthly band but the day-of-month would not hold "
            "still, so the series was projected on an interval instead",
            dict(unstable_anchor),
        ),
        _warn(
            "projected_event_outside_horizon",
            len(out_of_horizon),
            "a projection escaped its own window; a bug, not a data property",
            {k: _FIRST_DAY_OF_MONTH for k in out_of_horizon},
        ),
        _warn(
            "inferred_events_suppressed_by_given_row",
            suppressed,
            "an inferred occurrence a scheduled or reserved row already covers; "
            "suppressed rather than double-counted",
        ),
        _warn(
            "series_cadence_classification",
            sum(cadence_counts.values()),
            "informational: how every series was classified",
            dict(cadence_counts),
        ),
    )
