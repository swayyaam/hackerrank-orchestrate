"""Balance forecasting over the safety horizon.

Pure. No I/O, no clock, no randomness, no network, and no import outside
``bow.contracts``. Same input, same output, always — which is what lets the
whole decision layer be tested without a single API call.

The forecast is a step function: an opening balance, plus every cash flow that
lands inside the horizon, applied on the date the cash actually moves.

Three sources of future cash, kept explicitly distinct so a wrong number can be
traced to the layer that produced it:

``projected``
    inferred by the recurrence layer from settled history
``given_commitment``
    already in the data — a scheduled row, or a pending debit reserved per
    ``Q1_pending_duplicate_debit`` — and surviving ``drop_ignored``
``amendment``
    asserted by an evidence entry, typically a message the perception layer
    resolved against an event that already exists

**A given commitment beats a projection on the same occurrence.** A scheduled
bill-payment retry and a projected recurring debt payment are the same money;
counting both would invent an outflow. The recurrence layer suppresses the
inferred side, and :func:`build_forecast` asserts the result.

The opening balance is ``current_available_balance``, taken as given. It is
never reconstructed from history — the reconnaissance established that it is
not reconstructible.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from bow.contracts.enums import (
    BalanceAsOfBasis,
    CashFlowSource,
    Direction,
    EvidenceEffect,
)
from bow.contracts.evidence import EvidenceLedger
from bow.contracts.dataset import FinancialProfile
from bow.contracts.projection import (
    Cadence,
    NotProjectedReason,
    ProjectedEvent,
    ProjectionOrigin,
    SeriesClassification,
)
from bow.contracts.scalars import IsoDate, Money

__all__ = [
    "CashFlow",
    "Forecast",
    "VariableSpendRate",
    "build_forecast",
    "trailing_rate",
    "variable_spend_flows",
]

_ZERO = Decimal()
_ONE = 1
_ONE_DAY = timedelta(days=1)

#: Provenance keys an evidence entry must carry to become a cash flow.
_AMENDMENT_DATE_KEY = "cash_date"
_AMENDMENT_DIRECTION_KEY = "direction"


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CashFlow(_Frozen):
    """One movement of cash on one date. Amount is unsigned; direction carries
    the sign, exactly as the source data does."""

    date: IsoDate
    amount: Money
    direction: Direction
    source: CashFlowSource
    label: str
    source_id: str | None = None

    @property
    def signed(self) -> Decimal:
        return -self.amount if self.direction is Direction.DEBIT else self.amount


class Forecast(_Frozen):
    """A balance curve over ``[request_date, horizon_end]``."""

    request_date: IsoDate
    horizon_end: IsoDate
    opening_balance: Money
    flows: tuple[CashFlow, ...]

    def balance_on(self, when: date) -> Decimal:
        """Balance at the close of ``when``, including anything landing that day."""
        total = self.opening_balance
        for flow in self.flows:
            if flow.date <= when:
                total += flow.signed
        return total

    def trough(self, from_date: date, to_date: date) -> tuple[date, Decimal]:
        """The lowest point of the curve on ``[from_date, to_date]``.

        Evaluated at ``from_date`` and at every flow date inside the window.
        The curve only moves on a flow date, so no other date can be lower.

        Computed in a single ordered pass rather than by re-evaluating the
        balance at each candidate. The variable-spending path adds one flow per
        day, and the quadratic form became the dominant cost once it did.
        """
        if to_date < from_date:
            raise ValueError("trough window ends before it begins")

        running = self.opening_balance
        for flow in self._ordered:
            if flow.date <= from_date:
                running += flow.signed
        best_date, best_value = from_date, running

        for flow in self._ordered:
            if flow.date <= from_date:
                continue
            if flow.date > to_date:
                break
            running += flow.signed
            if running < best_value:
                best_date, best_value = flow.date, running
        return best_date, best_value

    @property
    def _ordered(self) -> tuple[CashFlow, ...]:
        """Flows in date order. ``build_forecast`` already sorts by date."""
        return self.flows

    def series(self) -> tuple[tuple[date, Decimal], ...]:
        """The curve as (date, balance) pairs. For debugging and reporting."""
        dates = sorted({self.request_date, *(f.date for f in self.flows), self.horizon_end})
        return tuple((d, self.balance_on(d)) for d in dates)

    def flows_from(self, source: CashFlowSource) -> tuple[CashFlow, ...]:
        return tuple(f for f in self.flows if f.source is source)

    @property
    def inflows(self) -> tuple[CashFlow, ...]:
        return tuple(f for f in self.flows if f.direction is Direction.CREDIT)


def _amendment_flows(ledger: EvidenceLedger) -> list[CashFlow]:
    """Cash flows asserted by the evidence ledger.

    An entry becomes a flow only when it amends something and carries both a
    cash date and a direction in its provenance. Anything less is evidence
    about the world, not a movement of money, and is ignored here rather than
    guessed at.
    """
    flows: list[CashFlow] = []
    for entry in ledger.entries:
        if entry.effect is not EvidenceEffect.AMEND:
            continue
        provenance = entry.provenance or {}
        raw_date = provenance.get(_AMENDMENT_DATE_KEY)
        raw_direction = provenance.get(_AMENDMENT_DIRECTION_KEY)
        if raw_date is None or raw_direction is None:
            continue
        if not isinstance(entry.normalized_value, Decimal):
            continue
        flows.append(
            CashFlow(
                date=raw_date,
                amount=entry.normalized_value,
                direction=Direction(raw_direction),
                source=CashFlowSource.AMENDMENT,
                label=entry.field,
                source_id=entry.source_id,
            )
        )
    return flows


def build_forecast(
    ledger: EvidenceLedger,
    profile: FinancialProfile,
    projected: tuple[ProjectedEvent, ...],
    request_date: date,
    horizon_days: int,
    *,
    balance_basis: BalanceAsOfBasis = BalanceAsOfBasis.INCLUSIVE_THROUGH_REQUEST_DATE,
) -> Forecast:
    """Assemble the balance curve.

    ``Q8_balance_as_of_semantics`` decides the near boundary. Under the default
    the opening balance already contains every event whose cash date is on or
    before ``request_date``, so such a flow must not be applied again. Under the
    alternative the balance is as of the last settled event and a flow dated
    exactly on ``request_date`` is still to come.
    """
    horizon_end = request_date + timedelta(days=horizon_days)
    include_on_request_date = balance_basis is BalanceAsOfBasis.AS_OF_LAST_SETTLED_EVENT

    def admits(when: date) -> bool:
        if when > horizon_end:
            return False
        if when > request_date:
            return True
        return when == request_date and include_on_request_date

    flows: list[CashFlow] = []
    for event in projected:
        if not admits(event.date):
            continue
        flows.append(
            CashFlow(
                date=event.date,
                amount=event.amount,
                direction=event.direction,
                source=(
                    CashFlowSource.GIVEN_COMMITMENT
                    if event.origin is ProjectionOrigin.GIVEN
                    else CashFlowSource.PROJECTED
                ),
                label=event.series_key.label(),
                source_id=event.source_event_id,
            )
        )
    for flow in _amendment_flows(ledger):
        if admits(flow.date):
            flows.append(flow)

    flows.sort(key=lambda f: (f.date, f.source.value, f.label))
    return Forecast(
        request_date=request_date,
        horizon_end=horizon_end,
        opening_balance=profile.current_available_balance,
        flows=tuple(flows),
    )


# --- essential variable spending ---------------------------------------------
#
# Series whose gaps do not cluster are given no cadence, which is correct. What
# does not follow is that they spend nothing. A household keeps buying food
# whether or not its purchase dates form a pattern, and a forecast in which the
# balance only ever falls on scheduled dates never erodes between income events,
# so its trough lands in the first week instead of just before the next inflow.
#
# The rate has NO FREE COEFFICIENT. It is the total spend inside a trailing
# window divided by the window length, with the window equal to the horizon.
# The claim is "the next 90 days look like the last 90 days" — arguable on its
# merits, and with nothing in it to fit.
#
# Which classes of series are admitted is three separate decisions, because they
# are three separate claims:
#   Q26_project_irregular_series          gaps scattered, spending ongoing
#   Q27_project_single_occurrence_series  one observation predicts recurrence
#   Q28_project_stale_series              a series past its cutoff still bills


class VariableSpendRate(_Frozen):
    """One series' contribution to the variable-spending forecast."""

    series_key_label: str
    category: str
    cadence: Cadence
    per_day: Money
    occurrences_in_window: int
    window_total: Money


def trailing_rate(
    series: SeriesClassification, *, request_date: date, window_days: int
) -> VariableSpendRate:
    """Total spend inside the trailing window, divided by the window length.

    Occurrences outside the window contribute nothing, which is what makes a
    long-dormant series self-correcting: it is admitted by a flag but supplies a
    rate of zero.
    """
    window_start = request_date - timedelta(days=window_days)
    inside = [
        amount
        for when, amount in zip(series.occurrence_dates, series.amounts)
        if window_start <= when <= request_date
    ]
    total = sum(inside, _ZERO)
    return VariableSpendRate(
        series_key_label=series.key.label(),
        category=series.key.category,
        cadence=series.cadence,
        per_day=total / Decimal(window_days),
        occurrences_in_window=len(inside),
        window_total=total,
    )


def _admits(series: SeriesClassification, *, irregular: bool, single: bool, stale: bool) -> bool:
    """Is this series' class admitted by the three flags?

    A series already projected by cadence detection is always excluded: it is
    counted once, as a dated flow, and counting it again as a rate would be a
    straightforward double count.
    """
    if series.projectable:
        return False
    if series.key.direction is not Direction.DEBIT:
        return False
    reason = series.not_projected_reason
    if reason is NotProjectedReason.STALE:
        return stale
    if reason is NotProjectedReason.SINGLE_OCCURRENCE:
        return single
    if reason is NotProjectedReason.IRREGULAR_GAPS:
        return irregular
    if reason is NotProjectedReason.BELOW_MIN_OCCURRENCES:
        return single
    return False


def variable_spend_flows(
    series: Sequence[SeriesClassification],
    request_date: date,
    horizon_days: int,
    *,
    window_days: int,
    min_occurrences_in_window: int,
    project_irregular: bool,
    project_single_occurrence: bool,
    project_stale: bool,
) -> tuple[tuple[CashFlow, ...], tuple[VariableSpendRate, ...]]:
    """Daily outflows at the combined trailing rate of the admitted series.

    A series whose trailing window holds fewer than ``min_occurrences_in_window``
    occurrences contributes nothing. See the threshold's own reasoning in
    ``config/thresholds.yaml``.

    Categories the profile marks reducible or stoppable are deliberately
    INCLUDED. ``amount_safe_to_pay`` is defined as the amount payable *before*
    optional spending changes, so flexible spending belongs in the baseline;
    excluding it would under-project by construction.

    One aggregated flow per day rather than one per series per day: the rate is
    uniform, the sum is what moves the balance, and per-series attribution is
    returned alongside for the ledger and the warnings.
    """
    admitted = [
        s
        for s in series
        if _admits(
            s,
            irregular=project_irregular,
            single=project_single_occurrence,
            stale=project_stale,
        )
    ]
    rates = tuple(
        trailing_rate(s, request_date=request_date, window_days=window_days)
        for s in admitted
    )
    # A guard on the evidence, not a damping coefficient. A window holding too
    # few occurrences yields no rate at all rather than a scaled-down one: the
    # problem with one observation is that it is not evidence, and multiplying
    # a non-observation by a fraction does not make it one.
    usable = tuple(r for r in rates if r.occurrences_in_window >= min_occurrences_in_window)
    per_day = sum((r.per_day for r in usable), _ZERO)
    if per_day <= _ZERO:
        return (), rates

    flows = tuple(
        CashFlow(
            date=request_date + timedelta(days=offset),
            amount=per_day,
            direction=Direction.DEBIT,
            source=CashFlowSource.VARIABLE_SPEND,
            label="essential variable spending (trailing rate)",
        )
        for offset in range(1, horizon_days + 1)
    )
    return flows, rates


def variable_spend_warnings(
    forecast: Forecast,
    rates: Sequence[VariableSpendRate],
    *,
    opening_balance: Decimal,
    warn_fraction_of_opening: Decimal,
    min_occurrences_in_window: int,
) -> tuple[tuple[str, int, dict[str, int]], ...]:
    """Warning-level assertions for one forecast. Counts only; never raises.

    Returned as plain tuples rather than ``WarningCount`` so this module keeps
    importing nothing but contracts it already needs; the caller assembles them
    into the run report.
    """
    horizon_days = (forecast.horizon_end - forecast.request_date).days
    projected_variable = sum(
        (f.amount for f in forecast.flows_from(CashFlowSource.VARIABLE_SPEND)), _ZERO
    )
    dominates = (
        _ONE
        if opening_balance > _ZERO
        and projected_variable > warn_fraction_of_opening * opening_balance
        else 0
    )

    thin = {
        r.series_key_label: r.occurrences_in_window
        for r in rates
        if r.occurrences_in_window < min_occurrences_in_window
    }

    # A series counted on both paths would be double counted. The rate path
    # excludes anything `projectable`, so this must always be empty.
    rate_labels = {r.series_key_label for r in rates}
    cadence_labels = {
        f.label for f in forecast.flows_from(CashFlowSource.PROJECTED)
    }
    both = sorted(rate_labels & cadence_labels)

    goes_negative = [
        when.isoformat() for when, balance in forecast.series() if balance < _ZERO
    ]

    return (
        (
            "variable_spend_dominates_opening_balance",
            dominates,
            {"projected_variable_over_horizon": int(projected_variable)},
        ),
        ("series_projected_by_both_paths", len(both), {label: 1 for label in both}),
        (
            "forecast_goes_negative_in_horizon",
            _ONE if goes_negative else 0,
            {day: 1 for day in goes_negative[:5]},
        ),
        ("trailing_window_with_too_few_occurrences", len(thin), thin),
        ("horizon_days", horizon_days, {}),
    )
