"""Normalization: lifecycle collapse, cash-treatment rules, currency conversion.

The central result of this module is that the eight lifecycle archetypes in
``docs/DATA_REPORT.md`` section 3 are not eight cases. They are six rules over
typed fields, and **not one of them reads a description**:

====================================================  ========
condition                                             outcome
====================================================  ========
``status in {cancelled, failed}``                     drop
``status == unrealized`` or ``direction == non_cash`` drop
``status == pending`` and ``direction == credit``     drop
``status == pending`` and ``direction == debit``      keep    (governed by Q1)
``status == scheduled``                               keep
``status == settled``                                 keep
====================================================  ========

``tests/test_archetypes.py`` runs these six rules against every linked pair in
the real dataset and asserts the archetype table's stated cash treatment for
each. If that test ever fails, the description string carries information the
typed fields do not, and the architecture has to change.

Nothing here reads the clock, the network, or a description.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from typing import Iterable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict

from bow.contracts import (
    CashDateBasis,
    Direction,
    DropReason,
    EventStatus,
    EvidenceEffect,
    EvidenceEntry,
    EvidenceSourceType,
    ExchangeRate,
    FinancialEvent,
    FinancialProfile,
    Flexibility,
    ImageLink,
    KeepReason,
    PendingDuplicatePolicy,
    RunReport,
    WarningCount,
)

__all__ = [
    "ConversionResult",
    "DroppedEvent",
    "DropResult",
    "KeptEvent",
    "LifecycleCycleError",
    "LifecycleResolution",
    "MissingCashDateError",
    "NormalizationError",
    "NormalizationResult",
    "UnresolvableFX",
    "cash_date",
    "cash_treatment",
    "collapse_lifecycles",
    "conversion_warnings",
    "convert_currency",
    "drop_ignored",
    "normalize_events",
]

_WHITESPACE = re.compile(r"\s+")


# --- errors -------------------------------------------------------------------


class NormalizationError(RuntimeError):
    """Normalization cannot proceed. Fail closed rather than guess."""


class LifecycleCycleError(NormalizationError):
    """``linked_event_id`` forms a cycle, so no chain has a terminal state."""

    def __init__(self, cycle: Sequence[str]) -> None:
        self.cycle = tuple(cycle)
        super().__init__(
            "linked_event_id forms a cycle: " + " -> ".join([*self.cycle, self.cycle[0]])
        )


class MissingCashDateError(NormalizationError):
    """The date the chosen basis needs is blank on an event that still counts."""

    def __init__(self, event_id: str, basis: CashDateBasis) -> None:
        self.event_id = event_id
        self.basis = basis
        super().__init__(
            f"event {event_id} has no {basis.value}, which "
            f"Q16_cash_date_basis selects as the date cash moves"
        )


class UnresolvableFX(NormalizationError):
    """No rate for this exact pair on this exact date.

    Never softened. The rate table is directionally asymmetric, so inverting a
    reverse rate or reaching for a nearby date would produce a plausible wrong
    number instead of an error.
    """

    def __init__(
        self,
        *,
        from_currency: str,
        to_currency: str,
        on: date,
        event_id: str,
        reverse_exists: bool,
    ) -> None:
        self.from_currency = from_currency
        self.to_currency = to_currency
        self.on = on
        self.event_id = event_id
        self.reverse_exists = reverse_exists
        hint = (
            f" The reverse pair {to_currency}->{from_currency} exists, but "
            "inverting it is not permitted."
            if reverse_exists
            else ""
        )
        super().__init__(
            f"no exchange rate for {from_currency}->{to_currency} on {on} "
            f"(needed by event {event_id}).{hint}"
        )


# --- results ------------------------------------------------------------------


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LifecycleResolution(_Frozen):
    """The ``linked_event_id`` graph, resolved."""

    chains: tuple[tuple[str, ...], ...]
    parent_of: dict[str, str]
    children_of: dict[str, tuple[str, ...]]
    terminals_of: dict[str, tuple[str, ...]]
    orphan_links: tuple[str, ...]

    @property
    def length_distribution(self) -> dict[str, int]:
        return {str(k): v for k, v in sorted(Counter(len(c) for c in self.chains).items())}

    @property
    def multi_child_parents(self) -> tuple[str, ...]:
        return tuple(sorted(p for p, kids in self.children_of.items() if len(kids) > 1))

    @property
    def max_length(self) -> int:
        return max((len(c) for c in self.chains), default=0)


class KeptEvent(_Frozen):
    event: FinancialEvent
    reason: KeepReason


class DroppedEvent(_Frozen):
    event: FinancialEvent
    reason: DropReason
    detail: str = ""


class DropResult(_Frozen):
    kept: tuple[KeptEvent, ...]
    dropped: tuple[DroppedEvent, ...]

    @property
    def events(self) -> tuple[FinancialEvent, ...]:
        return tuple(k.event for k in self.kept)

    @property
    def drop_counts(self) -> dict[str, int]:
        return dict(Counter(d.reason.value for d in self.dropped))

    @property
    def keep_counts(self) -> dict[str, int]:
        return dict(Counter(k.reason.value for k in self.kept))


class ConversionResult(_Frozen):
    events: tuple[FinancialEvent, ...]
    evidence: tuple[EvidenceEntry, ...]
    converted: int
    unchanged: int
    #: Events that need conversion but have no amount yet. They stay in their
    #: source currency until the document is read, so they must be converted
    #: after resolution rather than assumed done. Named, not silent.
    deferred: tuple[str, ...] = ()


class NormalizationResult(_Frozen):
    """Everything normalization produced, including what it threw away."""

    lifecycle: LifecycleResolution
    drops: DropResult
    conversion: ConversionResult
    report: RunReport

    @property
    def events(self) -> tuple[FinancialEvent, ...]:
        return self.conversion.events


# --- 1. lifecycle -------------------------------------------------------------


def collapse_lifecycles(events: Sequence[FinancialEvent]) -> LifecycleResolution:
    """Resolve ``linked_event_id`` to terminal state.

    Handles chains of arbitrary length and parents with more than one inbound
    link. That every chain in one particular dataset happens to be two events
    long is asserted as a warning-level count elsewhere, never assumed here.

    Raises :class:`LifecycleCycleError` if the links form a cycle, because a
    cycle has no terminal state and every downstream reading would be arbitrary.
    """
    known = {event.event_id for event in events}
    parent_of: dict[str, str] = {}
    orphan_links: list[str] = []

    for event in events:
        link = event.linked_event_id
        if link is None:
            continue
        if link not in known:
            orphan_links.append(event.event_id)
            continue
        parent_of[event.event_id] = link

    children_of: dict[str, list[str]] = defaultdict(list)
    for child, parent in parent_of.items():
        children_of[parent].append(child)
    children_sorted = {p: tuple(sorted(kids)) for p, kids in children_of.items()}

    # Every node that participates in the graph at all.
    in_graph = set(parent_of) | set(children_sorted)
    roots = sorted(node for node in in_graph if node not in parent_of)

    chains: list[tuple[str, ...]] = []
    visiting: list[str] = []
    on_stack: set[str] = set()

    def walk(node: str) -> None:
        if node in on_stack:
            cycle_start = visiting.index(node)
            raise LifecycleCycleError(visiting[cycle_start:])
        visiting.append(node)
        on_stack.add(node)
        kids = children_sorted.get(node, ())
        if not kids:
            chains.append(tuple(visiting))
        else:
            for kid in kids:
                walk(kid)
        on_stack.discard(node)
        visiting.pop()

    for root in roots:
        walk(root)

    # A node inside a cycle is unreachable from any root, so an unvisited node
    # that has a parent means a cycle exists that the root walk never entered.
    reached = {node for chain in chains for node in chain}
    for node in sorted(in_graph - reached):
        walk(node)

    terminals_of: dict[str, tuple[str, ...]] = defaultdict(tuple)
    for chain in chains:
        leaf = chain[-1]
        for node in chain:
            if leaf not in terminals_of[node]:
                terminals_of[node] = terminals_of[node] + (leaf,)

    return LifecycleResolution(
        chains=tuple(chains),
        parent_of=dict(parent_of),
        children_of=children_sorted,
        terminals_of=dict(terminals_of),
        orphan_links=tuple(sorted(orphan_links)),
    )


# --- 2. cash treatment --------------------------------------------------------


def cash_treatment(event: FinancialEvent) -> KeepReason | DropReason:
    """The six rules. Typed fields only; no description is ever consulted."""
    if event.status in (EventStatus.CANCELLED, EventStatus.FAILED):
        return DropReason.CANCELLED_OR_FAILED
    if event.status is EventStatus.UNREALIZED or event.direction is Direction.NON_CASH:
        return DropReason.UNREALIZED_OR_NON_CASH
    if event.status is EventStatus.PENDING:
        if event.direction is Direction.CREDIT:
            return DropReason.PENDING_CREDIT
        return KeepReason.PENDING_DEBIT_RESERVED
    if event.status is EventStatus.SCHEDULED:
        return KeepReason.SCHEDULED_COMMITMENT
    if event.status is EventStatus.SETTLED:
        return KeepReason.SETTLED
    raise NormalizationError(
        f"event {event.event_id}: no cash rule covers "
        f"status={event.status.value} direction={event.direction.value}"
    )


def _duplicate_key(event: FinancialEvent) -> tuple[str, ...]:
    """Every field except ``event_id``, with whitespace normalised."""
    def text(value: object) -> str:
        return _WHITESPACE.sub(" ", str(value).strip()) if value is not None else ""

    return tuple(
        text(getattr(event, name))
        for name in event.__class__.model_fields
        if name != "event_id"
    )


def _is_linked_duplicate_pending_debit(
    event: FinancialEvent,
    by_id: Mapping[str, FinancialEvent],
) -> bool:
    """A pending debit that restates an already-settled parent.

    Typed fields only: same owner, same direction, same currency, same amount,
    parent already settled. The description is never consulted.
    """
    if event.status is not EventStatus.PENDING or event.direction is not Direction.DEBIT:
        return False
    parent = by_id.get(event.linked_event_id or "")
    if parent is None:
        return False
    return (
        parent.status is EventStatus.SETTLED
        and parent.user_id == event.user_id
        and parent.direction is event.direction
        and parent.currency == event.currency
        and parent.amount is not None
        and event.amount is not None
        and parent.amount == event.amount
    )


def drop_ignored(
    events: Sequence[FinancialEvent],
    *,
    pending_duplicate_policy: PendingDuplicatePolicy = PendingDuplicatePolicy.RESERVE,
) -> DropResult:
    """Apply the six rules, plus exact-duplicate removal.

    Duplicates are compared on the full field tuple excluding ``event_id``, with
    whitespace normalised, so two rows that differ only in spacing collapse.
    The first occurrence in input order is kept.

    Returns both halves. Every dropped row carries its reason so the drop can
    enter the evidence ledger rather than vanishing.
    """
    by_id = {event.event_id: event for event in events}
    kept: list[KeptEvent] = []
    dropped: list[DroppedEvent] = []
    seen: dict[tuple[str, ...], str] = {}

    for event in events:
        key = _duplicate_key(event)
        first = seen.get(key)
        if first is not None:
            dropped.append(
                DroppedEvent(
                    event=event,
                    reason=DropReason.EXACT_DUPLICATE,
                    detail=f"identical to {first} on every field but event_id",
                )
            )
            continue
        seen[key] = event.event_id

        outcome = cash_treatment(event)
        if isinstance(outcome, DropReason):
            dropped.append(DroppedEvent(event=event, reason=outcome))
            continue

        if (
            outcome is KeepReason.PENDING_DEBIT_RESERVED
            and pending_duplicate_policy is PendingDuplicatePolicy.IGNORE_AS_DUPLICATE
            and _is_linked_duplicate_pending_debit(event, by_id)
        ):
            dropped.append(
                DroppedEvent(
                    event=event,
                    reason=DropReason.LINKED_DUPLICATE_PENDING_DEBIT,
                    detail=(
                        f"restates settled {event.linked_event_id}; "
                        "Q1_pending_duplicate_debit=ignore_as_duplicate"
                    ),
                )
            )
            continue

        kept.append(KeptEvent(event=event, reason=outcome))

    return DropResult(kept=tuple(kept), dropped=tuple(dropped))


# --- 3. currency --------------------------------------------------------------


def cash_date(event: FinancialEvent, basis: CashDateBasis) -> date:
    """The date cash moves, per ``Q16_cash_date_basis``.

    This is also the date the exchange-rate lookup keys on. The coupling is
    deliberate and recorded in the decision entry: converting on a different
    day from the one the money moves would be a silent inconsistency.
    """
    if basis is CashDateBasis.EVENT_DATE:
        return event.event_date
    if event.settlement_date is None:
        raise MissingCashDateError(event.event_id, basis)
    return event.settlement_date


def _rate_index(rates: Iterable[ExchangeRate]) -> dict[tuple[str, str, date], Decimal]:
    return {(r.from_currency, r.to_currency, r.rate_date): r.rate for r in rates}


def convert_currency(
    events: Sequence[FinancialEvent],
    rates: Sequence[ExchangeRate],
    profiles: Sequence[FinancialProfile],
    *,
    basis: CashDateBasis = CashDateBasis.SETTLEMENT_DATE,
) -> ConversionResult:
    """Restate every foreign-currency event in its owner's home currency.

    Only events whose currency differs from the owner's ``home_currency`` are
    touched. The rate must exist for the **exact** ordered pair on the **exact**
    date the basis selects. A rate is never inverted, never triangulated through
    a third currency, and never taken from a nearby date; a miss raises
    :class:`UnresolvableFX`.

    Every conversion performed is recorded as evidence, carrying the rate and
    the date it was keyed on.
    """
    home_of = {profile.user_id: profile.home_currency for profile in profiles}
    index = _rate_index(rates)
    available_pairs = {(f, t) for f, t, _ in index}

    out: list[FinancialEvent] = []
    evidence: list[EvidenceEntry] = []
    deferred: list[str] = []
    converted = 0

    for event in events:
        home = home_of.get(event.user_id)
        if home is None:
            raise NormalizationError(
                f"event {event.event_id} belongs to unknown user {event.user_id}"
            )
        if event.currency == home:
            out.append(event)
            continue

        on = cash_date(event, basis)
        pair = (event.currency, home)
        rate = index.get((event.currency, home, on))
        if rate is None:
            raise UnresolvableFX(
                from_currency=event.currency,
                to_currency=home,
                on=on,
                event_id=event.event_id,
                reverse_exists=(home, event.currency) in available_pairs,
            )

        if event.amount is None:
            # A blank amount stays blank. It is resolved from its document
            # first; converting nothing would silently invent a zero. The event
            # is recorded as deferred so that it is converted once resolved
            # rather than quietly left in a foreign currency.
            deferred.append(event.event_id)
            out.append(event)
            continue

        original = event.amount
        restated = original * rate
        out.append(event.model_copy(update={"amount": restated, "currency": home}))
        converted += 1
        evidence.append(
            EvidenceEntry(
                source_type=EvidenceSourceType.EXCHANGE_RATE,
                source_id=f"{pair[0]}->{pair[1]}@{on.isoformat()}",
                field="amount",
                raw_value=f"{original} {event.currency}",
                normalized_value=restated,
                effect=EvidenceEffect.CONVERT,
                confidence=Decimal(1),
                provenance={
                    "event_id": event.event_id,
                    "rate": str(rate),
                    "rate_date": on.isoformat(),
                    "cash_date_basis": basis.value,
                    "from_currency": pair[0],
                    "to_currency": pair[1],
                },
            )
        )

    return ConversionResult(
        events=tuple(out),
        evidence=tuple(evidence),
        converted=converted,
        unchanged=len(out) - converted - len(deferred),
        deferred=tuple(deferred),
    )


# --- warning-level assertions -------------------------------------------------
#
# Every observation the reconnaissance made about one dataset is checked here
# and reported as a count. None of these raises. The general case is
# implemented for all of them, so a dataset that breaks one produces a loud
# number in the run report rather than a crash or a silently wrong answer.


def _warn(name: str, count: int, note: str, detail: dict[str, int] | None = None) -> WarningCount:
    return WarningCount(name=name, count=count, detail=detail or {}, note=note)


def lifecycle_warnings(lifecycle: LifecycleResolution) -> tuple[WarningCount, ...]:
    distribution = lifecycle.length_distribution
    longer_than_pair = sum(
        n for length, n in distribution.items() if int(length) > len("ab")
    )
    return (
        _warn(
            "lifecycle_chain_length_distribution",
            longer_than_pair,
            "chains longer than two events; the general walk handles any depth",
            distribution,
        ),
        _warn(
            "lifecycle_parents_with_multiple_children",
            len(lifecycle.multi_child_parents),
            "one parent restated by more than one later record",
        ),
        _warn(
            "lifecycle_orphan_links",
            len(lifecycle.orphan_links),
            "linked_event_id pointing at an event that does not exist",
        ),
    )


def flexibility_warnings(
    events: Sequence[FinancialEvent],
    profiles: Sequence[FinancialProfile],
) -> tuple[WarningCount, ...]:
    profile_of = {profile.user_id: profile for profile in profiles}

    bound_without_flex = 0
    flex_without_bound = 0
    bound_not_below_amount = 0
    flexible_but_protected = 0
    flexible_outside_profile = 0
    fixed_in_changeable_category = 0

    for event in events:
        reducible = event.flexibility.is_reducible
        has_bound = event.minimum_allowed_amount is not None

        if has_bound and not reducible:
            bound_without_flex += 1
        if reducible and not has_bound:
            flex_without_bound += 1
        if has_bound and event.amount is not None:
            if event.minimum_allowed_amount >= event.amount:
                bound_not_below_amount += 1

        profile = profile_of.get(event.user_id)
        if profile is None:
            continue
        if event.flexibility.is_flexible and event.category in profile.expense_categories_to_protect:
            flexible_but_protected += 1

        in_reduce = event.category in profile.expense_categories_user_is_willing_to_reduce
        in_stop = event.category in profile.expense_categories_user_is_willing_to_stop
        # The two encodings are redundant in ONE direction only. A flexible
        # event must sit in the matching profile list, or a spending change on
        # it would exceed what the user authorised. The converse does not hold:
        # a category the user would reduce in general still contains one-off
        # purchases that are individually fixed.
        if (event.flexibility.is_reducible and not in_reduce) or (
            event.flexibility.is_stoppable and not in_stop
        ):
            flexible_outside_profile += 1
        if not event.flexibility.is_flexible and (in_reduce or in_stop):
            fixed_in_changeable_category += 1

    return (
        _warn(
            "minimum_allowed_amount_without_reducible_flexibility",
            bound_without_flex,
            "a reduction floor on an event that cannot be reduced",
        ),
        _warn(
            "reducible_flexibility_without_minimum_allowed_amount",
            flex_without_bound,
            "a reducible event with no floor to reduce to",
        ),
        _warn(
            "minimum_allowed_amount_not_below_amount",
            bound_not_below_amount,
            "a floor at or above the current amount leaves nothing to reduce",
        ),
        _warn(
            "flexible_event_in_protected_category",
            flexible_but_protected,
            "the profile protects a category the event marks changeable",
        ),
        _warn(
            "flexible_event_outside_profile_lists",
            flexible_outside_profile,
            "a change on this event would exceed what the profile authorises",
        ),
        _warn(
            "fixed_event_in_changeable_category",
            fixed_in_changeable_category,
            "informational: a one-off purchase inside a category the user would "
            "otherwise reduce or stop. Expected, and not a conflict.",
        ),
    )


def rate_warnings(rates: Sequence[ExchangeRate]) -> tuple[WarningCount, ...]:
    by_pair: dict[tuple[str, str], set[Decimal]] = defaultdict(set)
    for rate in rates:
        by_pair[rate.pair].add(rate.rate)
    varying = {
        f"{f}->{t}": len(values) for (f, t), values in by_pair.items() if len(values) > 1
    }
    return (
        _warn(
            "currency_pairs_with_more_than_one_rate",
            len(varying),
            "a pair whose rate moves over time; lookup is per date regardless",
            varying,
        ),
    )


def conversion_warnings(conversion: ConversionResult) -> tuple[WarningCount, ...]:
    return (
        _warn(
            "conversion_deferred_pending_amount",
            len(conversion.deferred),
            "needs conversion but has no amount yet; convert after the document "
            "is read, never leave in the source currency",
            {event_id: 1 for event_id in conversion.deferred},
        ),
    )


def date_warnings(events: Sequence[FinancialEvent]) -> tuple[WarningCount, ...]:
    settles_before_event = sum(
        1
        for event in events
        if event.settlement_date is not None and event.settlement_date < event.event_date
    )
    return (
        _warn(
            "settlement_date_before_event_date",
            settles_before_event,
            "cash moving before the obligation arose",
        ),
    )


def blank_amount_warnings(
    events: Sequence[FinancialEvent],
    images: Sequence[ImageLink],
) -> tuple[WarningCount, ...]:
    with_image = {image.related_event_id for image in images}
    blanks = [event for event in events if event.amount is None]
    unresolvable = [event for event in blanks if event.event_id not in with_image]
    image_counts = Counter(image.related_event_id for image in images)
    multi = {k: v for k, v in image_counts.items() if v > 1}
    return (
        _warn(
            "blank_amount_events",
            len(blanks),
            "amounts that must be resolved from a document, never read as zero",
        ),
        _warn(
            "blank_amount_without_image",
            len(unresolvable),
            "unresolvable without a document; needs the fail-closed path",
        ),
        _warn(
            "blank_amount_with_multiple_images",
            len(multi),
            "more than one document for one event; reads must be reconciled",
            {k: v for k, v in sorted(multi.items())},
        ),
    )


# --- orchestration ------------------------------------------------------------


def normalize_events(
    events: Sequence[FinancialEvent],
    *,
    profiles: Sequence[FinancialProfile],
    rates: Sequence[ExchangeRate],
    images: Sequence[ImageLink] = (),
    basis: CashDateBasis = CashDateBasis.SETTLEMENT_DATE,
    pending_duplicate_policy: PendingDuplicatePolicy = PendingDuplicatePolicy.RESERVE,
) -> NormalizationResult:
    """Collapse lifecycles, drop what is not cash, restate into home currency.

    The order matters: dropping first is what guarantees no ``non_cash`` row
    with a blank settlement date ever reaches the currency step.
    """
    lifecycle = collapse_lifecycles(events)
    drops = drop_ignored(events, pending_duplicate_policy=pending_duplicate_policy)
    conversion = convert_currency(drops.events, rates, profiles, basis=basis)

    report = (
        RunReport()
        .with_counts(drops=drops.drop_counts, keeps=drops.keep_counts)
        .with_warnings(lifecycle_warnings(lifecycle))
        .with_warnings(flexibility_warnings(events, profiles))
        .with_warnings(rate_warnings(rates))
        .with_warnings(conversion_warnings(conversion))
        .with_warnings(date_warnings(events))
        .with_warnings(blank_amount_warnings(events, images))
    )

    return NormalizationResult(
        lifecycle=lifecycle, drops=drops, conversion=conversion, report=report
    )
