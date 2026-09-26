#!/usr/bin/env python3
"""Re-run the variable-spend experiment: eight on/off combinations, from config.

`docs/VARIABLE_SPEND_EXPERIMENT.md` records the original run, including the
predictions written down for all eight combinations BEFORE any of them was
executed. This reproduces it, so the write-up is checkable rather than
testimony.

The three flags are the ones the register marks switchable for variable
spending:

    Q26_project_irregular_series          a series whose gaps do not cluster
    Q27_project_single_occurrence_series  a series with one observed occurrence
    Q28_project_stale_series              a series past its staleness cutoff

Each is toggled between `project_at_trailing_rate` and `never_project`. Nothing
is edited on disk: the flags are overridden in memory for the length of one run.

Deterministic and free — no vision, no messages, no network. It scores the 25
sample requests and never touches `output.csv` or the evaluation set.

Usage:
    python evaluation/ablations.py [--json PATH]
"""

from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
import statistics
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

_SUBMISSION_ROOT = Path(__file__).resolve().parent.parent
if str(_SUBMISSION_ROOT) not in sys.path:
    sys.path.insert(0, str(_SUBMISSION_ROOT))

from bow import paths  # noqa: E402
import bow.pipeline as pipeline  # noqa: E402

FLAGS = (
    "Q26_project_irregular_series",
    "Q27_project_single_occurrence_series",
    "Q28_project_stale_series",
)
ON, OFF = "project_at_trailing_rate", "never_project"


def _harness():
    spec = importlib.util.spec_from_file_location(
        "bow_eval_harness", Path(__file__).resolve().parent / "harness.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["bow_eval_harness"] = module
    spec.loader.exec_module(module)
    return module


def _score(run) -> dict:
    """The two headline numbers, computed the same way the harness computes them."""
    written, truth, profiles = run["written"], run["truth"], run["profiles"]
    measurable = sorted(r for r in written if r not in run["blocked"])
    status = sum(
        1 for r in measurable
        if truth[r].affordability_status.value == written[r]["affordability_status"]
    )
    trough_rels = []
    clamped = 0
    for r in measurable:
        actual = truth[r].amount_safe_to_pay
        predicted = Decimal(written[r]["amount_safe_to_pay"])
        if predicted == truth[r].requested_amount:
            clamped += 1
            continue
        if not actual:
            continue
        floor = profiles[truth[r].user_id].minimum_balance_to_keep
        trough = actual + floor
        trough_rels.append(float(abs((predicted + floor) - trough) / trough))
    return {
        "status_hits": status,
        "scored": len(measurable),
        "trough_median_pct": round(statistics.median(trough_rels) * 100, 1),
        "invertible": len(trough_rels),
        "clamp_bound": clamped,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    harness = _harness()
    original = pipeline._decision_flags
    results = []
    print(f"{'Q26':<6}{'Q27':<6}{'Q28':<6}{'status':>10}{'trough median':>16}"
          f"{'invertible':>12}{'clamped':>9}")
    for combination in itertools.product((True, False), repeat=len(FLAGS)):
        overrides = dict(zip(FLAGS, (ON if on else OFF for on in combination)))

        def patched(decisions, _o=overrides):
            flags = original(decisions)
            flags.update(_o)
            return flags

        pipeline._decision_flags = patched
        failure = None
        try:
            destination = Path(tempfile.mkdtemp()) / "ablation.csv"
            run = harness.collect(perception=False, destination=destination)
        except Exception as exc:  # noqa: BLE001
            # A post-condition is allowed to stop a run — that is what it is for.
            # Reported per combination rather than aborting the sweep, because
            # "this configuration cannot produce a valid row" is a result.
            failure = f"{type(exc).__name__}: {exc}"
        finally:
            pipeline._decision_flags = original
        if failure is not None:
            results.append({"flags": overrides, "failed": failure})
            print(f"{'on' if combination[0] else 'off':<6}"
                  f"{'on' if combination[1] else 'off':<6}"
                  f"{'on' if combination[2] else 'off':<6}"
                  f"   FAILED  {failure[:60]}")
            continue
        score = _score(run)
        results.append({"flags": overrides, **score})
        print(f"{'on' if combination[0] else 'off':<6}"
              f"{'on' if combination[1] else 'off':<6}"
              f"{'on' if combination[2] else 'off':<6}"
              f"{score['status_hits']:>7}/{score['scored']:<2}"
              f"{score['trough_median_pct']:>15}%"
              f"{score['invertible']:>12}{score['clamp_bound']:>9}")

    shipped = {f: ON if f == FLAGS[0] else OFF for f in FLAGS}
    print(f"\nshipped configuration: "
          + ", ".join(f"{k.split('_')[0]}={'on' if v == ON else 'off'}"
                      for k, v in shipped.items()))
    print("write-up, with the predictions made before the run: "
          "docs/VARIABLE_SPEND_EXPERIMENT.md")
    if args.json:
        args.json.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"wrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
