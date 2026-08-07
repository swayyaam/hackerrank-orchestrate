"""Project OutputRow to the six contract columns and write output.csv."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Sequence

from config import EvidenceConfig, RecordConfig

_TRUNC = RecordConfig.ERROR_LIST_TRUNCATION
from errors import OutputContractError
from schemas import Action, MessageType, OutputRow

CONTRACT_COLUMNS: tuple[str, ...] = (
    "message_id",
    "action",
    "message_type",
    "reason",
    "confidence",
    "evidence_message_ids",
)


def render_evidence(ids: Sequence[str], cfg: EvidenceConfig) -> str:
    """Semicolon-joined with no spaces, or the literal `none`."""
    return cfg.SEPARATOR.join(ids) if ids else cfg.NONE_LITERAL


def write_output(
    rows: Sequence[OutputRow],
    path: Path,
    order: Sequence[str],
    cfg: EvidenceConfig,
) -> int:
    """Write the six contract columns in the given message_id order.

    Takes OutputRow alone -- output.csv is built from the OutputRow and nothing
    else. Returns the number of rows written.

    Raises:
        OutputContractError: the row set does not match the requested order.
    """
    by_id = {row.message_id: row for row in rows}
    if len(by_id) != len(rows):
        raise OutputContractError("duplicate message_id among output rows")
    missing = [message_id for message_id in order if message_id not in by_id]
    if missing:
        raise OutputContractError(f"missing output rows for: {missing[:_TRUNC]}")
    extra = sorted(set(by_id) - set(order))
    if extra:
        raise OutputContractError(f"output rows not in the requested order: {extra[:_TRUNC]}")

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(CONTRACT_COLUMNS)
        for message_id in order:
            row = by_id[message_id]
            writer.writerow(
                [
                    row.message_id,
                    row.action.value,
                    row.message_type.value,
                    row.reason,
                    f"{row.confidence:.2f}",
                    render_evidence(row.evidence_message_ids, cfg),
                ]
            )
    return len(order)


def validate_submission(
    path: Path,
    expected_ids: Sequence[str],
    history_ids: Iterable[str],
    cfg: EvidenceConfig,
) -> list[str]:
    """Check a written output.csv against the submission contract.

    Returns a list of human-readable problems; empty means the file passes.
    """
    problems: list[str] = []
    history = set(history_ids)
    allowed_actions = {a.value for a in Action}
    allowed_types = {t.value for t in MessageType}

    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration:
            return ["output.csv is empty"]
        rows = list(reader)

    if tuple(header) != CONTRACT_COLUMNS:
        problems.append(f"header is {header}, expected {list(CONTRACT_COLUMNS)}")
        return problems

    seen: list[str] = []
    for index, row in enumerate(rows, start=2):
        if len(row) != len(CONTRACT_COLUMNS):
            problems.append(f"line {index}: has {len(row)} fields, expected {len(CONTRACT_COLUMNS)}")
            continue
        message_id, action, message_type, reason, confidence, evidence = row
        seen.append(message_id)
        if action not in allowed_actions:
            problems.append(f"line {index} ({message_id}): action={action!r} not allowed")
        if message_type not in allowed_types:
            problems.append(f"line {index} ({message_id}): message_type={message_type!r} not allowed")
        if not reason.strip():
            problems.append(f"line {index} ({message_id}): reason is empty")
        try:
            value = float(confidence)
        except ValueError:
            problems.append(f"line {index} ({message_id}): confidence={confidence!r} is not numeric")
        else:
            if not 0.0 <= value <= 1.0:
                problems.append(f"line {index} ({message_id}): confidence={value} outside [0, 1]")
        if evidence != cfg.NONE_LITERAL:
            if not evidence.strip():
                problems.append(f"line {index} ({message_id}): evidence is blank, expected 'none'")
            for eid in evidence.split(cfg.SEPARATOR):
                if eid != eid.strip():
                    problems.append(f"line {index} ({message_id}): evidence id {eid!r} has whitespace")
                if eid.strip() not in history:
                    problems.append(
                        f"line {index} ({message_id}): evidence id {eid!r} is not in message_history"
                    )

    duplicates = sorted({m for m in seen if seen.count(m) > 1})
    if duplicates:
        problems.append(f"duplicate message_ids: {duplicates}")
    missing = [m for m in expected_ids if m not in set(seen)]
    if missing:
        problems.append(f"missing message_ids ({len(missing)}): {missing[:_TRUNC]}")
    unexpected = sorted(set(seen) - set(expected_ids))
    if unexpected:
        problems.append(f"unexpected message_ids: {unexpected[:_TRUNC]}")
    if len(seen) != len(expected_ids):
        problems.append(f"row count is {len(seen)}, expected {len(expected_ids)}")
    return problems
