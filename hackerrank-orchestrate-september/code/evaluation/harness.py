#!/usr/bin/env python3
"""The sample-set scorecard. One place, used by both the tests and the CLI.

`dataset/sample_requests.csv` is the only labelled data available, so it is the
only place any accuracy figure in this repository comes from. AGENTS.md §6.1
permits it for understanding format and decision style; it is never used as a
label for an evaluation request (`tests/test_sample_set_isolation.py` proves the
predictions do not move when the answers are stripped from the input).

Two figures are reported for `amount_safe_to_pay`, and the distinction matters:

* **PRIMARY** — relative error on the *implied trough*,
  ``amount_safe_to_pay + minimum_balance_to_keep``. The trough is what the
  forecast actually predicts; the safe amount is that trough minus a constant,
  then clamped.
* **secondary** — relative error on the safe amount itself. Same error over a
  smaller denominator, so it is always the larger number. Quoted only with its
  name attached.

Rows where the prediction is clamped to ``requested_amount`` are reported
separately rather than folded into either figure: they measure the clamp, not
the forecast.

Running it re-runs the pipeline over the 25 sample requests ONLY. It never
touches `output.csv`, and it never runs against the evaluation set.

Usage:
    python evaluation/harness.py                 # deterministic path, no cost
    python evaluation/harness.py --perception    # with vision and messages
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

_SUBMISSION_ROOT = Path(__file__).resolve().parent.parent
if str(_SUBMISSION_ROOT) not in sys.path:
    sys.path.insert(0, str(_SUBMISSION_ROOT))

from bow import paths  # noqa: E402
from bow.data.loaders import load_dataset  # noqa: E402
from bow.pipeline import run_pipeline, write_output  # noqa: E402
from bow.validate import run_gates  # noqa: E402

__all__ = ["collect", "scorecard"]


def _confusion(pairs) -> dict:
    return dict(sorted(Counter(pairs).items()))


def _plan_structure(plan: str) -> tuple:
    """A plan compared by dates and amounts, ignoring how they were rendered."""
    if not plan or plan == "none":
        return ("none",)
    parts = []
    for leg in plan.split("|"):
        date, _, amount = leg.partition(":")
        try:
            parts.append((date.strip(), Decimal(amount.strip())))
        except Exception:  # noqa: BLE001 - an unparseable leg is its own signal
            parts.append((date.strip(), amount.strip()))
    return tuple(parts)


def collect(*, perception: bool, destination: Path) -> dict:
    """Run the sample set and gather everything the scorecard needs."""
    result, requests = run_pipeline(
        paths.require_dataset_dir(), targets="sample_requests", perception=perception
    )
    write_output(result.rows, destination)
    failures = run_gates(destination, tuple(requests), requests)
    with destination.open(newline="", encoding="utf-8") as handle:
        written = {row["request_id"]: row for row in csv.DictReader(handle)}

    dataset = load_dataset(paths.require_dataset_dir()).dataset
    by_user: dict[str, list] = {}
    for event in dataset.events:
        by_user.setdefault(event.user_id, []).append(event)
    blocked = {
        sample.request_id
        for sample in dataset.sample_requests
        if any(e.amount is None for e in by_user[sample.user_id])
    }
    return dict(
        result=result,
        written=written,
        failures=failures,
        truth={s.request_id: s for s in dataset.sample_requests},
        profiles={p.user_id: p for p in dataset.profiles},
        blocked=blocked,
        perception=perception,
    )


def scorecard(run: dict) -> list[str]:
    """Every number, as lines. Returned rather than printed so tests can assert."""
    written, truth, profiles = run["written"], run["truth"], run["profiles"]
    blocked = run["blocked"]
    # A blocked row needs an image resolved before it can be scored at all, so
    # without perception it is excluded and counted rather than scored as wrong.
    measurable = sorted(written) if run["perception"] else sorted(
        r for r in written if r not in blocked
    )

    lines: list[str] = []
    add = lines.append
    add("=" * 74)
    add(f"SAMPLE-SET SCORECARD  ({'with' if run['perception'] else 'without'} "
        f"perception)")
    add("=" * 74)
    add(f"rows written              : {len(written)}")
    add(f"scored                    : {len(measurable)}"
        + ("" if run["perception"] else
           f"  ({len(blocked)} blocked on an unresolved image, excluded)"))
    add(f"schema validity           : "
        f"{'100%' if not run['failures'] else str(run['failures'])}")

    for column, title in (("affordability_status", "affordability_status"),
                          ("recommended_payment_method", "recommended_payment_method")):
        hits = sum(1 for r in measurable
                   if getattr(truth[r], column).value == written[r][column])
        add("")
        add(f"{title:<26}: {hits}/{len(measurable)} "
            f"({hits * 100 // max(len(measurable), 1)}%)")
        pairs = [(getattr(truth[r], column).value, written[r][column])
                 for r in measurable]
        for (actual, predicted), count in _confusion(pairs).items():
            mark = "  " if actual == predicted else "!!"
            add(f"   {mark} {actual:<22} -> {predicted:<22} {count}")

    exact = sum(1 for r in measurable
                if written[r]["payment_plan"] == truth[r].payment_plan)
    structural = sum(1 for r in measurable
                     if _plan_structure(written[r]["payment_plan"])
                     == _plan_structure(truth[r].payment_plan))
    add("")
    add(f"payment_plan exact string : {exact}/{len(measurable)}")
    add(f"payment_plan structural   : {structural}/{len(measurable)} "
        f"(ignoring formatting)")

    date_hits = 0
    date_pairs = []
    for r in measurable:
        actual = truth[r].earliest_date_for_full_payment
        actual_s = actual.isoformat() if actual else ""
        predicted = written[r]["earliest_date_for_full_payment"]
        date_hits += int(actual_s == predicted)
        date_pairs.append(("empty" if not actual_s else "set",
                           "empty" if not predicted else "set"))
    add("")
    add(f"earliest_date exact       : {date_hits}/{len(measurable)}")
    add(f"  empty/populated         : {_confusion(date_pairs)}")

    rels, trough_rels, clamped = [], [], []
    for r in measurable:
        actual = truth[r].amount_safe_to_pay
        predicted = Decimal(written[r]["amount_safe_to_pay"])
        if predicted == truth[r].requested_amount:
            clamped.append(r)
            continue
        if not actual:
            continue
        rels.append(float(abs(predicted - actual) / actual))
        floor = profiles[truth[r].user_id].minimum_balance_to_keep
        trough = actual + floor
        trough_rels.append(float(abs((predicted + floor) - trough) / trough))
    add("")
    add(f"implied_trough  (PRIMARY) : median rel err "
        f"{statistics.median(trough_rels) * 100:.1f}% over {len(trough_rels)} "
        f"invertible rows")
    add(f"amount_safe_to_pay        : median rel err "
        f"{statistics.median(rels) * 100:.1f}% over {len(rels)} invertible rows")
    add(f"  clamp-bound, separate   : {len(clamped)}  {sorted(clamped)}")

    vision = paths.EVAL_DIR / "vision_run.json"
    if vision.is_file():
        records = json.loads(vision.read_text(encoding="utf-8"))
        ok = sum(1 for v in records.values() if v["ok"])
        misses = sorted(k for k, v in records.items() if not v["ok"])
        add("")
        add(f"vision gold set           : {ok}/{len(records)}   "
            f"miss list: {misses or 'empty'}")
        add("  (scored against the cache the pipeline uses; "
            "regenerate with scripts/score_vision_gold.py)")
    add("=" * 74)
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--perception", action="store_true",
                        help="run vision and message extraction (billed)")
    parser.add_argument("--out", type=Path, default=None,
                        help="where to write the sample predictions "
                             "(default: a temporary file; never output.csv)")
    args = parser.parse_args()

    import tempfile

    destination = args.out or Path(tempfile.mkdtemp()) / "output_sample.csv"
    if destination.resolve() == paths.OUTPUT_PATH.resolve():
        parser.error("refusing to write the graded output.csv")
    for line in scorecard(collect(perception=args.perception,
                                  destination=destination)):
        print(line)
    print(f"\nsample predictions written to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
