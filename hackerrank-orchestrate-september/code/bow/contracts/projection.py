"""Projection contracts.

These live in ``contracts/`` rather than in ``data/`` because ``engine/`` has to
consume them and ``engine/`` may import ``contracts/`` and nothing else. The
recurrence layer produces them; the forecast layer reads them; neither imports
the other.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from bow.contracts.enums import Direction, EventType, Flexibility
from bow.contracts.scalars import CurrencyCode, IsoDate, Money

__all__ = [
    "Cadence",
    "NotProjectedReason",
    "ProjectedEvent",
    "ProjectionOrigin",
    "SeriesClassification",
    "SeriesKey",
]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Cadence(StrEnum):
    """How a series repeats, if it repeats."""

    MONTHLY_ANCHORED = "monthly_anchored"
    FIXED_INTERVAL = "fixed_interval"
    IRREGULAR = "irregular"
    SINGLE_OCCURRENCE = "single_occurrence"


class ProjectionOrigin(StrEnum):
    """Whether a projected event was given by the data or inferred from it."""

    GIVEN = "given"
    INFERRED = "inferred"


class SeriesKey(_Frozen):
    """Identity of a recurring series.

    ``description`` is an opaque label here. It is compared and hashed; it is
    never inspected.
    """

    user_id: str
    description: str
    category: str
    direction: Direction
    event_type: EventType

    def label(self) -> str:
        return (
            f"{self.user_id}/{self.event_type.value}/{self.category}/"
            f"{self.direction.value}/{self.description}"
        )


class ProjectedEvent(_Frozen):
    """One cash event expected inside the horizon."""

    date: IsoDate
    amount: Money
    direction: Direction
    currency: CurrencyCode
    series_key: SeriesKey
    origin: ProjectionOrigin
    source_event_id: str | None = None


class NotProjectedReason(StrEnum):
    """Why a series contributes nothing to the horizon."""

    SINGLE_OCCURRENCE = "single_occurrence"
    BELOW_MIN_OCCURRENCES = "below_min_occurrences"
    IRREGULAR_GAPS = "irregular_gaps"
    STALE = "stale"
    PERIOD_TOO_LONG = "period_too_long"
    NO_AMOUNT = "no_amount"


class SeriesClassification(_Frozen):
    """What one series looks like, and whether it can be projected."""

    key: SeriesKey
    cadence: Cadence
    occurrences: int
    #: One entry per observed occurrence, in order. Carried so a consumer can
    #: compute a trailing-window rate without reaching back into the raw events.
    occurrence_dates: tuple[IsoDate, ...] = ()
    #: One entry per occurrence, in the same order. A spending change must name
    #: a real event_id, so the series has to remember which rows it came from.
    occurrence_event_ids: tuple[str, ...] = ()
    #: Carried from the events, not re-derived. An unrecognised flexibility is
    #: already fatal at load time.
    flexibility: Flexibility = Flexibility.FIXED
    #: The floor a reduction may go down to. Populated iff reducible.
    minimum_allowed_amount: Money | None = None
    gaps_days: tuple[int, ...]
    period_days: int | None = None
    anchor_day: int | None = None
    anchor_day_spread: int | None = None
    first_occurrence: IsoDate
    last_occurrence: IsoDate
    age_days: int
    currency: CurrencyCode
    amounts: tuple[Money, ...]
    projected_amount: Money | None = None
    median_amount: Money | None = None
    projectable: bool
    not_projected_reason: NotProjectedReason | None = None
    #: gaps sat in the monthly band but the day-of-month would not hold still
    anchor_unstable: bool = False
