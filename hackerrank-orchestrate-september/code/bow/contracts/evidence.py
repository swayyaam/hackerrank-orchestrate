"""The evidence ledger.

Every field of every output row must trace back to an entry here. The ledger is
frozen before the engine runs, and the explanation writer sees the ledger and
nothing else — that is what stops citations drifting from decisions.

``provenance`` carries structured extras, most usefully the Document AI
per-field ``confidence`` and ``sources`` annotations. Carry it, surface it in
the run report, and never branch on it in ``engine/``.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Strict

from bow.contracts.enums import EvidenceEffect, EvidenceSourceType, UnresolvedReason

__all__ = ["EvidenceEntry", "EvidenceLedger", "NormalizedValue", "UnresolvedItem"]

#: A normalized evidence value, kept at exactly the type it was built as.
NormalizedValue = (
    Annotated[Decimal, Strict()]
    | Annotated[datetime, Strict()]
    | Annotated[date, Strict()]
    | Annotated[str, Strict()]
    | None
)


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceEntry(_Frozen):
    """One traceable fact.

    ``normalized_value`` holds a typed value. Construction passes a real
    ``Decimal``, ``date`` or ``str``, which is what lets :meth:`EvidenceLedger.amounts`
    and :meth:`EvidenceLedger.dates` select without re-parsing.
    """

    source_type: EvidenceSourceType
    source_id: str
    field: str
    raw_value: str
    # Per-member Strict() is load-bearing. Under pydantic's default lax union a
    # `datetime` is silently narrowed to a `date` and a numeric string to a
    # `Decimal`, which would make an instant indistinguishable from a calendar
    # date in `dates()`. Strict validation keeps each member exactly the type it
    # was constructed as. A union-level strict=True is rejected by pydantic, so
    # the constraint has to sit on each member.
    normalized_value: NormalizedValue = None
    effect: EvidenceEffect
    confidence: Decimal | None = None
    provenance: dict[str, Any] | None = None


class UnresolvedItem(_Frozen):
    """Something that could not be resolved. Fail closed, never default-fill."""

    reason: UnresolvedReason
    source_type: EvidenceSourceType
    source_id: str
    field: str
    detail: str


class EvidenceLedger(_Frozen):
    """Frozen evidence for one request."""

    request_id: str
    entries: tuple[EvidenceEntry, ...] = ()
    unresolved: tuple[UnresolvedItem, ...] = ()

    def ids(self) -> tuple[str, ...]:
        """Every source id cited, in first-seen order, without duplicates."""
        seen: dict[str, None] = {}
        for entry in self.entries:
            seen.setdefault(entry.source_id, None)
        return tuple(seen)

    def amounts(self) -> tuple[Decimal, ...]:
        """Every normalized amount, in entry order."""
        return tuple(
            entry.normalized_value
            for entry in self.entries
            if isinstance(entry.normalized_value, Decimal)
        )

    def dates(self) -> tuple[date, ...]:
        """Every normalized calendar date, in entry order.

        ``datetime`` is excluded: it is an instant, not a calendar date, and the
        two are never interchangeable in this problem.
        """
        return tuple(
            entry.normalized_value
            for entry in self.entries
            if isinstance(entry.normalized_value, date)
            and not isinstance(entry.normalized_value, datetime)
        )

    @property
    def is_resolved(self) -> bool:
        return not self.unresolved

    def extended(
        self,
        *,
        entries: tuple[EvidenceEntry, ...] = (),
        unresolved: tuple[UnresolvedItem, ...] = (),
    ) -> EvidenceLedger:
        """Return a new ledger with more evidence. The ledger never mutates."""
        return EvidenceLedger(
            request_id=self.request_id,
            entries=self.entries + entries,
            unresolved=self.unresolved + unresolved,
        )
