"""Spending changes: the smallest set of flexible-expense edits that makes a
plan safe.

Three rules, each established by reconnaissance and each encoded as a rule
rather than a value:

**The reduction amount is the event's own ``minimum_allowed_amount``.** Not a
computed shortfall. The field exists and says how far the user is willing to go;
computing a number instead would propose a reduction they never agreed to.

**The referenced ``event_id`` is the most recent occurrence of the series**
(``Q11_spending_change_target_occurrence``). A projected future occurrence has
no id and cannot be named in the required syntax.

**The verb follows ``flexibility``.** ``stoppable`` can only be stopped,
``reducible`` can only be reduced, and ``reducible_or_stoppable`` is decided by
``Q29_flexible_both_verb`` — subject to permission, below.

**The profile decides what may be touched at all.** AGENTS.md 6.2: *only
non-protected, flexible events in a category the user permits may be changed*.
Flexibility is a property of the event; permission is a property of the profile,
and both must hold. The two encodings agree on the data seen so far, which is
asserted as a warning count rather than relied on: this filter is what makes the
rule hold on data where they disagree.

Changes are searched only for a plan that fails safety unmodified, because
ranking rule 2 puts a plan needing no change ahead of one that does.

The three-change cap is hard. **A plan needing four changes is rejected, never
truncated to three** — a truncated set does not make the plan safe, so emitting
it would claim a safety that does not hold.
"""

from __future__ import annotations

from decimal import Decimal
from itertools import combinations
from typing import Sequence

from pydantic import BaseModel, ConfigDict

from bow.contracts.dataset import FinancialProfile
from bow.contracts.enums import Direction, Flexibility
from bow.contracts.projection import ProjectedEvent, SeriesClassification, SeriesKey
from bow.contracts.scalars import Money

__all__ = [
    "ChangeCandidate",
    "ChangePermission",
    "ChangeSet",
    "apply_changes",
    "candidate_changes",
    "search_changes",
]

_ZERO = Decimal()
_STOP = "stop"
_REDUCE = "reduce_to"
_SEPARATOR = ":"


class ChangeCandidate(BaseModel):
    """One flexible series that could be stopped or reduced."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    series_key: SeriesKey
    verb: str
    current_amount: Money
    new_amount: Money

    @property
    def saving_per_occurrence(self) -> Decimal:
        return self.current_amount - self.new_amount

    def render(self) -> str:
        """The output syntax. Amount formatting happens at the boundary."""
        if self.verb == _STOP:
            return f"{_STOP}{_SEPARATOR}{self.event_id}"
        return f"{_REDUCE}{_SEPARATOR}{self.event_id}{_SEPARATOR}{self.new_amount}"


class ChangeSet(BaseModel):
    """A ranked set of changes, at most ``max_changes`` of them."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    changes: tuple[ChangeCandidate, ...]

    @property
    def total_reduction(self) -> Decimal:
        return sum((c.saving_per_occurrence for c in self.changes), _ZERO)

    def rendered(self) -> tuple[str, ...]:
        return tuple(c.render() for c in self.changes)


class ChangePermission(BaseModel):
    """What one user's profile authorises, per AGENTS.md 6.2.

    Three category lists, used exactly as the profile states them. A protected
    category vetoes both verbs; otherwise the verb must appear in its own list,
    because a user willing to reduce a category has not thereby agreed to stop
    it. Nothing here infers permission from flexibility.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    protected: frozenset[str]
    willing_to_reduce: frozenset[str]
    willing_to_stop: frozenset[str]

    @classmethod
    def from_profile(cls, profile: FinancialProfile) -> "ChangePermission":
        return cls(
            protected=frozenset(profile.expense_categories_to_protect),
            willing_to_reduce=frozenset(
                profile.expense_categories_user_is_willing_to_reduce
            ),
            willing_to_stop=frozenset(
                profile.expense_categories_user_is_willing_to_stop
            ),
        )

    def allows(self, category: str, verb: str) -> bool:
        if category in self.protected:
            return False
        permitted = self.willing_to_reduce if verb == _REDUCE else self.willing_to_stop
        return category in permitted


def _verbs_for(flexibility: Flexibility, prefer_reduce: bool) -> tuple[str, ...]:
    """The verbs this flexibility allows, most preferred first.

    ``reducible_or_stoppable`` offers both. Q29 orders them; permission and the
    presence of a floor decide which one survives, so the second entry is a
    fallback rather than an alternative reading of the flag.
    """
    if flexibility is Flexibility.STOPPABLE:
        return (_STOP,)
    if flexibility is Flexibility.REDUCIBLE:
        return (_REDUCE,)
    if flexibility is Flexibility.REDUCIBLE_OR_STOPPABLE:
        return (_REDUCE, _STOP) if prefer_reduce else (_STOP, _REDUCE)
    return ()


def candidate_changes(
    series: Sequence[SeriesClassification],
    *,
    permitted: ChangePermission,
    prefer_reduce: bool = True,
) -> tuple[ChangeCandidate, ...]:
    """Every change the event's flexibility AND the profile permit.

    ``permitted`` is required, not defaulted: a default would be a silent
    authorisation, and the one thing this function must never do is propose a
    change the user did not agree to.

    A series with no amount, or a reducible series with no floor, produces no
    candidate: there is nothing to reduce to.
    """
    candidates: list[ChangeCandidate] = []
    for classification in series:
        if classification.key.direction is not Direction.DEBIT:
            continue
        if not classification.occurrence_event_ids:
            continue
        current = classification.projected_amount
        if current is None:
            continue
        category = classification.key.category
        for verb in _verbs_for(classification.flexibility, prefer_reduce):
            if not permitted.allows(category, verb):
                continue
            if verb == _REDUCE:
                floor = classification.minimum_allowed_amount
                if floor is None or floor >= current:
                    continue
                new_amount = floor
            else:
                new_amount = _ZERO
            candidates.append(
                ChangeCandidate(
                    # Q11: the most recent occurrence, which is the last in order.
                    event_id=classification.occurrence_event_ids[-1],
                    series_key=classification.key,
                    verb=verb,
                    current_amount=current,
                    new_amount=new_amount,
                )
            )
            break
    return tuple(candidates)


def apply_changes(
    projected: Sequence[ProjectedEvent], changes: Sequence[ChangeCandidate]
) -> tuple[ProjectedEvent, ...]:
    """Rewrite the projection as if the changes were made.

    Stopping removes every future occurrence of that series; reducing rewrites
    each to the permitted floor. Neither touches the past.
    """
    stopped = {c.series_key for c in changes if c.verb == _STOP}
    reduced = {c.series_key: c.new_amount for c in changes if c.verb == _REDUCE}
    out: list[ProjectedEvent] = []
    for event in projected:
        if event.series_key in stopped:
            continue
        if event.series_key in reduced:
            out.append(event.model_copy(update={"amount": reduced[event.series_key]}))
        else:
            out.append(event)
    return tuple(out)


def _rank(changes: tuple[ChangeCandidate, ...]) -> tuple[int, Decimal, tuple[str, ...]]:
    """Fewest changes, then smallest total reduction, then lowest event_id."""
    return (
        len(changes),
        sum((c.saving_per_occurrence for c in changes), _ZERO),
        tuple(sorted(c.event_id for c in changes)),
    )


def search_changes(
    candidates: Sequence[ChangeCandidate],
    is_safe_with,
    *,
    max_changes: int,
) -> ChangeSet | None:
    """The smallest set of changes that makes the plan safe, or ``None``.

    ``is_safe_with`` takes a tuple of changes and returns whether the plan holds
    once they are applied. Subsets are searched smallest-first, so the first
    success is already minimal in count; ties within a size are broken by total
    reduction and then by event id.

    Returns ``None`` when no set of at most ``max_changes`` works. A plan that
    needs more is rejected rather than partially changed.
    """
    if not candidates:
        return None
    # One candidate per event: an event can be stopped or reduced, never both.
    by_event: dict[str, ChangeCandidate] = {}
    for candidate in candidates:
        by_event.setdefault(candidate.event_id, candidate)
    pool = sorted(by_event.values(), key=lambda c: c.event_id)

    for size in range(1, max_changes + 1):
        viable = [
            tuple(subset)
            for subset in combinations(pool, size)
            if is_safe_with(tuple(subset))
        ]
        if viable:
            return ChangeSet(changes=min(viable, key=_rank))
    return None
