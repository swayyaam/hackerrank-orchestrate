"""The guard between untrusted message content and the financial picture.

Message text is written by third parties. It reaches a model only inside a
delimited block in the **user** turn, never in a system prompt, and whatever
comes back is filtered here before it can touch anything.

Four rules, each one a way a message could otherwise change an answer it has no
right to change:

**Referential integrity.** A delta must name an event that exists in the loaded
set. A model that invents an id, or attaches a claim to an unrelated row, is
dropped.

**Field allowlist.** Only ``amount``, ``event_date`` and ``status`` may be
amended. A message may not move the balance floor, the requested amount, or the
payment methods the user will consider.

**No new financial facts.** ``Q13``: a message asserting income or a charge that
no event row records is recorded as evidence and dropped as a delta. The two
advance-fee messages in the dataset are exactly this shape.

**Every drop is logged**, with the rule that dropped it.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Iterable, Sequence

from pydantic import BaseModel, ConfigDict

__all__ = ["DropReason", "GuardResult", "MessageDelta", "MessageExtraction", "guard_deltas"]

#: The only fields a message may amend. Everything else is the user's own
#: configuration or the request itself, which a third party may not rewrite.
ALLOWED_FIELDS = frozenset({"amount", "event_date", "status"})

ALLOWED_EFFECTS = frozenset({"clarify", "amend", "cancel", "delay", "confirm", "none"})


class DropReason(StrEnum):
    UNKNOWN_EVENT = "target_event_id not in the loaded event set"
    NO_TARGET = "no target event, so nothing to amend"
    FIELD_NOT_ALLOWED = "field is outside the allowlist"
    CREATES_NEW_FACT = "asserts a fact no event row supports"
    UNKNOWN_EFFECT = "effect is outside the closed set"
    WRONG_OWNER = "target event belongs to a different user"
    NO_VALUE = "amends a field without supplying a value"


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MessageDelta(_Frozen):
    effect: str
    target_event_id: str | None = None
    field: str | None = None
    new_value: str | None = None
    effective_date: str | None = None
    creates_new_fact: bool = False
    evidence_quote: str | None = None


class MessageExtraction(_Frozen):
    deltas: tuple[MessageDelta, ...] = ()


class GuardResult(_Frozen):
    kept: tuple[MessageDelta, ...] = ()
    dropped: tuple[tuple[MessageDelta, str], ...] = ()

    @property
    def drop_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for _delta, reason in self.dropped:
            counts[reason] = counts.get(reason, 0) + 1
        return counts


def guard_deltas(
    deltas: Iterable[MessageDelta],
    *,
    message_user_id: str,
    events_by_id: dict,
) -> GuardResult:
    """Filter extracted deltas down to what a message is permitted to change."""
    kept: list[MessageDelta] = []
    dropped: list[tuple[MessageDelta, str]] = []

    for delta in deltas:
        if delta.effect not in ALLOWED_EFFECTS:
            dropped.append((delta, DropReason.UNKNOWN_EFFECT.value))
            continue
        if delta.creates_new_fact:
            dropped.append((delta, DropReason.CREATES_NEW_FACT.value))
            continue
        if delta.target_event_id is None:
            dropped.append((delta, DropReason.NO_TARGET.value))
            continue
        event = events_by_id.get(delta.target_event_id)
        if event is None:
            dropped.append((delta, DropReason.UNKNOWN_EVENT.value))
            continue
        if getattr(event, "user_id", None) != message_user_id:
            dropped.append((delta, DropReason.WRONG_OWNER.value))
            continue
        if delta.effect in ("amend", "delay"):
            if delta.field not in ALLOWED_FIELDS:
                dropped.append((delta, DropReason.FIELD_NOT_ALLOWED.value))
                continue
            if not (delta.new_value or delta.effective_date):
                dropped.append((delta, DropReason.NO_VALUE.value))
                continue
        kept.append(delta)

    return GuardResult(kept=tuple(kept), dropped=tuple(dropped))
