"""The output gate.

Run against the **written file**, not the in-memory objects, so a serialisation
bug cannot slip through. A column that formats correctly in memory and wrongly
on disk is exactly the failure this catches.

Two gates:

``check_schema``      the file's shape — columns, order, row set, allowed values
``check_invariants``  the specification's stated relationships between columns
"""

from __future__ import annotations

import csv
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from bow.contracts.enums import AffordabilityStatus, PaymentMethod

__all__ = ["GateFailure", "check_invariants", "check_schema", "run_gates"]

COLUMNS = (
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
)

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PLAN_ENTRY = re.compile(r"^\d{4}-\d{2}-\d{2}:-?\d+(?:\.\d+)?$")
_CHANGE = re.compile(r"^(?:stop:[^:|]+|reduce_to:[^:|]+:-?\d+(?:\.\d+)?)$")
_NO = "none"


class GateFailure(Exception):
    """The written output is not valid. Carries every failure, not the first."""

    def __init__(self, failures: list[str]) -> None:
        self.failures = failures
        listed = "\n".join(f"  - {f}" for f in failures)
        super().__init__(f"{len(failures)} gate failure(s):\n{listed}")


def _rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def check_schema(path: Path, expected_ids: tuple[str, ...]) -> list[str]:
    """Columns, order, and the exact row set, in the template's own order."""
    failures: list[str] = []
    if not path.is_file():
        return [f"output file not found: {path}"]

    header, rows = _rows(path)
    if tuple(header) != COLUMNS:
        failures.append(f"column header is {header}, expected {list(COLUMNS)}")
        return failures

    actual_ids = tuple(r["request_id"] for r in rows)
    if len(actual_ids) != len(expected_ids):
        failures.append(f"{len(actual_ids)} rows written, expected {len(expected_ids)}")
    if actual_ids != expected_ids:
        missing = set(expected_ids) - set(actual_ids)
        extra = set(actual_ids) - set(expected_ids)
        if missing:
            failures.append(f"missing request_id(s): {sorted(missing)[:5]}")
        if extra:
            failures.append(f"unexpected request_id(s): {sorted(extra)[:5]}")
        if not missing and not extra:
            failures.append("rows are not in the template's order")

    statuses = {s.value for s in AffordabilityStatus}
    methods = {m.value for m in PaymentMethod}
    for index, row in enumerate(rows, start=1):
        where = f"row {index} ({row['request_id']})"
        if row["affordability_status"] not in statuses:
            failures.append(f"{where}: affordability_status {row['affordability_status']!r}")
        if row["recommended_payment_method"] not in methods:
            failures.append(
                f"{where}: recommended_payment_method {row['recommended_payment_method']!r}"
            )
        try:
            Decimal(row["amount_safe_to_pay"])
        except (InvalidOperation, ValueError):
            failures.append(f"{where}: amount_safe_to_pay {row['amount_safe_to_pay']!r}")
        plan = row["payment_plan"]
        if plan != _NO:
            for entry in plan.split("|"):
                if not _PLAN_ENTRY.match(entry):
                    failures.append(f"{where}: payment_plan entry {entry!r}")
        earliest = row["earliest_date_for_full_payment"]
        if earliest and not _ISO_DATE.match(earliest):
            failures.append(f"{where}: earliest_date_for_full_payment {earliest!r}")
        changes = row["spending_changes_needed"]
        if changes != _NO:
            parts = changes.split("|")
            if len(parts) > 3:
                failures.append(f"{where}: more than three spending changes")
            for part in parts:
                if not _CHANGE.match(part):
                    failures.append(f"{where}: spending change {part!r}")
        if not row["decision_explanation"].strip():
            failures.append(f"{where}: decision_explanation is empty")
    return failures


def check_invariants(path: Path, requests_by_id: dict[str, object]) -> list[str]:
    """The specification's stated relationships between columns."""
    failures: list[str] = []
    if not path.is_file():
        return [f"output file not found: {path}"]
    _, rows = _rows(path)

    for index, row in enumerate(rows, start=1):
        where = f"row {index} ({row['request_id']})"
        request = requests_by_id.get(row["request_id"])
        if request is None:
            continue

        safe = Decimal(row["amount_safe_to_pay"])
        if not (Decimal(0) <= safe <= request.requested_amount):
            failures.append(
                f"{where}: amount_safe_to_pay {safe} outside [0, {request.requested_amount}]"
            )

        status = row["affordability_status"]
        method = row["recommended_payment_method"]
        plan = row["payment_plan"]
        earliest = row["earliest_date_for_full_payment"]

        if method == PaymentMethod.NOT_RECOMMENDED.value:
            if plan != _NO:
                failures.append(f"{where}: not_recommended with a payment plan")
            if status != AffordabilityStatus.NOT_AFFORDABLE.value:
                failures.append(f"{where}: not_recommended paired with {status}")
        elif plan == _NO:
            failures.append(f"{where}: {method} with payment_plan = none")

        if status == AffordabilityStatus.AFFORDABLE_NOW.value:
            if earliest != request.request_date.isoformat():
                failures.append(
                    f"{where}: affordable_now with earliest {earliest!r}, "
                    f"expected {request.request_date.isoformat()}"
                )
            if method != PaymentMethod.FULL_PAYMENT.value:
                failures.append(f"{where}: affordable_now with method {method}")

        if method == PaymentMethod.PARTIAL_PAYMENT.value:
            if status != AffordabilityStatus.AFFORDABLE_WITH_PLAN.value:
                failures.append(f"{where}: partial_payment with status {status}")
            entries = plan.split("|") if plan != _NO else []
            if len(entries) != 2:
                failures.append(f"{where}: partial_payment with {len(entries)} payments")
            else:
                total = sum(Decimal(e.split(":")[1]) for e in entries)
                if total != request.requested_amount:
                    failures.append(
                        f"{where}: partial payments sum to {total}, "
                        f"expected {request.requested_amount}"
                    )

        if plan != _NO:
            dates = [date.fromisoformat(e.split(":")[0]) for e in plan.split("|")]
            if dates != sorted(dates):
                failures.append(f"{where}: payment_plan is not chronological")
            if dates[0] < request.request_date:
                failures.append(f"{where}: a payment falls before request_date")
    return failures


def run_gates(
    path: Path, expected_ids: tuple[str, ...], requests_by_id: dict[str, object]
) -> list[str]:
    """Both gates. Returns every failure; empty means the output is valid."""
    failures = check_schema(path, expected_ids)
    if failures:
        return failures
    return check_invariants(path, requests_by_id)
