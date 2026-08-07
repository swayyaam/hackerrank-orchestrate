"""Read a run directory into typed frames. The only I/O module in the engine.

Deliberately imports nothing from the pipeline: if it did, a change to the
pipeline's schemas could break the ability to read an old run, which is the
opposite of retroactivity. Unknown fields are tolerated; missing ones are
reported.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

ACTIONS: tuple[str, ...] = ("notify", "digest", "mute")
MESSAGE_TYPES: tuple[str, ...] = (
    "personal",
    "urgent",
    "event",
    "payment",
    "business_update",
    "promotion",
    "greeting",
    "forward",
    "spam",
    "scam",
    "unknown",
)
OVERRIDE_CODES: tuple[str, ...] = (
    "CREDENTIAL_SOLICITATION",
    "ROUTER_DIRECTIVE",
    "IMPERSONATION",
    "COERCIVE_PAYMENT",
    "UNSAFE_INSTRUCTION",
    "UNAUTHORIZED_PAYMENT_REQUEST",
    "DIRECT_ADDRESS_GUARD",
    "PROMOTION_OPT_OUT",
    "REPEAT_DISMISSAL",
    "FREQUENTLY_FORWARDED_BROADCAST",
    "GROUP_MUTE_CAP",
    "COLD_SENDER_CAP",
    "TIME_CRITICAL_FLOOR",
    "DIRECT_REQUEST_FLOOR",
    "PERCEPTION_ACCEPTED",
)


class RunDirectoryError(Exception):
    """A run directory is missing or unreadable."""


@dataclass(frozen=True)
class RunData:
    run_dir: Path
    manifest: dict[str, Any]
    rows: pd.DataFrame
    eval_rows: pd.DataFrame
    eval_truth: pd.DataFrame

    @property
    def run_id(self) -> str:
        return str(self.manifest.get("run_id", self.run_dir.name))


def _read_jsonl(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return pd.DataFrame(records)


def load_run(run_dir: Path) -> RunData:
    """Read manifest, rows, eval rows, and eval_truth.csv -- all from the run
    directory, never from dataset/.

    Raises:
        RunDirectoryError: the directory or its manifest is missing.
    """
    run_dir = Path(run_dir)
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        raise RunDirectoryError(f"no manifest.json in {run_dir}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    truth_path = run_dir / "eval_truth.csv"
    truth = (
        pd.read_csv(truth_path, dtype=str, keep_default_na=False)
        if truth_path.exists()
        else pd.DataFrame()
    )
    return RunData(
        run_dir=run_dir,
        manifest=manifest,
        rows=_read_jsonl(run_dir / "rows.jsonl"),
        eval_rows=_read_jsonl(run_dir / "eval_rows.jsonl"),
        eval_truth=truth,
    )


def unavailable(missing: list[str], since: int = 2) -> dict[str, Any]:
    """A metric whose input fields are absent from an older run.

    Reported rather than crashed on, and never silently zeroed -- a metric that
    substitutes zero for absent data produces a confident wrong number.
    """
    return {
        "status": "unavailable",
        "missing_fields": missing,
        "first_available_in_schema_version": since,
    }
