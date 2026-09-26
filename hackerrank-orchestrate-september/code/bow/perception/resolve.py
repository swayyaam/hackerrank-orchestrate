"""Resolving what the ledger does not state: blank amounts, and message deltas.

Ordering matters and is not a convention. **Vision runs before FX**: a blank
amount in a foreign currency has nothing to convert, so resolving it first is
what lets the currency step complete. See ``docs/ARCHITECTURE.md``.

Both paths fail closed. An unresolved amount raises a flag and the affected row
is still written, marked in the run report.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from typing import Sequence

from bow.contracts import EvidenceEntry, FinancialEvent, UnresolvedItem
from bow.contracts.observation import ReadPath
from bow.perception.messages import extract_message
from bow.perception.untrusted import MessageDelta
from bow.perception.vision import read_amount

__all__ = ["ResolutionResult", "apply_deltas", "resolve_blank_amounts", "resolve_messages"]

_ALLOWED = {"amount", "event_date", "status"}


class ResolutionResult:
    def __init__(self) -> None:
        self.evidence: list[EvidenceEntry] = []
        self.unresolved: list[UnresolvedItem] = []
        self.paths: dict[str, str] = {}
        self.detail: dict[str, str] = {}


def resolve_blank_amounts(
    events: Sequence[FinancialEvent],
    images,
    client,
    *,
    image_path,
    label_priority: tuple[str, ...],
    label_denylist: tuple[str, ...],
    tolerance: Decimal,
    cache_dir: Path | None = None,
) -> tuple[tuple[FinancialEvent, ...], ResolutionResult]:
    """Read an amount for every event whose ``amount`` is blank.

    A blank amount is never zero. When no read produces a usable figure the
    event keeps its blank amount and an unresolved item is raised.
    """
    result = ResolutionResult()
    by_event = {image.related_event_id: image for image in images}
    blanks = [e for e in events if e.amount is None and e.event_id in by_event]
    if not blanks:
        return tuple(events), result

    resolved: dict[str, Decimal] = {}
    for event in blanks:
        image = by_event[event.event_id]
        cached = _cache_read(cache_dir, image.image_id)
        if cached is not None:
            amount, label, path, detail, substituted_from = cached
        else:
            outcome = read_amount(
                image.image_id, image_path(image.image_id), client,
                label_priority=label_priority, label_denylist=label_denylist,
                tolerance=tolerance,
            )
            amount = outcome.amount
            label, path, detail = outcome.label, outcome.path.value, outcome.detail
            substituted_from = outcome.substituted_from
            _cache_write(cache_dir, image.image_id, amount, label, path, detail,
                         substituted_from)

        result.paths[image.image_id] = path
        result.detail[image.image_id] = detail
        if amount is None:
            result.unresolved.append(
                UnresolvedItem(
                    reason="extraction_failed", source_type="image",
                    source_id=image.image_id, field="amount",
                    detail=f"{event.event_id}: {detail}",
                )
            )
            continue
        if amount < 0:
            # Defence in depth behind select_amount's abs(): nothing negative
            # may ever become an event amount, whatever produced it — a
            # line-item sum that came out below zero, or a cache entry written
            # by an older build. The forecast takes the sign from the event's
            # typed direction and assumes the amount is unsigned, so a negative
            # here would silently invert one cash flow.
            result.unresolved.append(
                UnresolvedItem(
                    reason="extraction_failed", source_type="image",
                    source_id=image.image_id, field="amount",
                    detail=(
                        f"{event.event_id}: read a negative figure; an amount is "
                        f"a magnitude and direction comes from the event's typed "
                        f"field, so this is refused rather than signed"
                    ),
                )
            )
            continue
        resolved[event.event_id] = amount
        result.evidence.append(
            EvidenceEntry(
                source_type="image", source_id=image.image_id, field="amount",
                raw_value=str(amount), normalized_value=amount, effect="extract",
                provenance={
                    "event_id": event.event_id, "label": label,
                    "read_path": path, "currency": event.currency,
                    # Present only when the line-item sum displaced the figure a
                    # label match had chosen, so the substitution is visible in
                    # the evidence trail rather than implied by the amount.
                    **({"substituted_from": substituted_from}
                       if substituted_from else {}),
                },
            )
        )

    updated = tuple(
        e.model_copy(update={"amount": resolved[e.event_id]})
        if e.event_id in resolved else e
        for e in events
    )
    return updated, result


def _cache_path(cache_dir: Path | None, image_id: str) -> Path | None:
    if cache_dir is None:
        return None
    return cache_dir / "vision" / f"{image_id}.json"


def _cache_read(cache_dir: Path | None, image_id: str):
    path = _cache_path(cache_dir, image_id)
    if path is None or not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    amount = Decimal(data["amount"]) if data.get("amount") is not None else None
    return (
        amount,
        data.get("label"),
        data.get("path", ReadPath.DIGITISE.value),
        data.get("detail", ""),
        # Absent from entries written before the substitution rule existed.
        data.get("substituted_from"),
    )


def _cache_write(cache_dir: Path | None, image_id: str, amount, label, path, detail,
                 substituted_from=None) -> None:
    target = _cache_path(cache_dir, image_id)
    if target is None:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "amount": str(amount) if amount is not None else None,
                "label": label, "path": path, "detail": detail,
                "substituted_from": substituted_from,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def resolve_messages(
    messages, events_by_user, events_by_id, client, *, prompt_path: Path,
    in_set: tuple[str, ...], workers: int = 8,
) -> tuple[list, dict[str, int]]:
    """Extract and guard every message. Returns outcomes and drop counts."""
    def one(message):
        return extract_message(
            message, events_by_user.get(message.user_id, []), events_by_id, client,
            prompt_path=prompt_path, in_set=in_set,
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        outcomes = list(pool.map(one, messages))

    drops: dict[str, int] = {}
    for outcome in outcomes:
        if outcome.guard is None:
            continue
        for reason, count in outcome.guard.drop_counts.items():
            drops[reason] = drops.get(reason, 0) + count
    return outcomes, drops


def apply_deltas(
    events: Sequence[FinancialEvent], outcomes, *, max_quote_chars: int
) -> tuple[tuple[FinancialEvent, ...], list[EvidenceEntry]]:
    """Apply every surviving delta to the event it names.

    Only the allowlisted fields are writable, and the guard has already checked
    that. Anything it let through is applied verbatim.
    """
    edits: dict[str, dict[str, object]] = {}
    evidence: list[EvidenceEntry] = []
    for outcome in outcomes:
        for delta in outcome.kept:
            if delta.field not in _ALLOWED or delta.target_event_id is None:
                continue
            value = delta.new_value if delta.field == "amount" else (
                delta.effective_date or delta.new_value
            )
            if value is None:
                continue
            edits.setdefault(delta.target_event_id, {})[delta.field] = value
            evidence.append(
                EvidenceEntry(
                    source_type="message", source_id=outcome.message_id,
                    field=delta.field, raw_value=str(value),
                    normalized_value=str(value), effect=delta.effect,
                    provenance={
                        "event_id": delta.target_event_id,
                        "quote": (delta.evidence_quote or "")[:max_quote_chars],
                        "route": outcome.route.value,
                    },
                )
            )
    if not edits:
        return tuple(events), evidence

    updated: list[FinancialEvent] = []
    for event in events:
        change = edits.get(event.event_id)
        if not change:
            updated.append(event)
            continue
        try:
            updated.append(event.model_copy(update=change).model_validate(
                {**event.model_dump(), **change}
            ))
        except Exception:  # noqa: BLE001 — a delta that will not validate is discarded
            updated.append(event)
    return tuple(updated), evidence
