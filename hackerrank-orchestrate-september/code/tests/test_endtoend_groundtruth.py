"""The sample set, end to end, scored against its published answers.

The scorecard itself lives in `evaluation/harness.py`, which is also the CLI a
reviewer runs. There is one implementation: this suite calls it rather than
reimplementing the metrics, so the numbers in the tests and the numbers a
reviewer sees cannot drift apart.

Marked `dataset` because it reads `dataset/` and runs the pipeline. It writes
its predictions to a pytest temporary directory and never to `output.csv`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from _treewalk import SUBMISSION_ROOT

pytestmark = pytest.mark.dataset


def _harness():
    path = SUBMISSION_ROOT / "evaluation" / "harness.py"
    spec = importlib.util.spec_from_file_location("bow_eval_harness", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bow_eval_harness"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    destination = tmp_path_factory.mktemp("out") / "output_sample.csv"
    # Deterministic path: every decision rule runs, no API call, no cost. The
    # perception figures are reproduced by `evaluation/harness.py --perception`.
    return _harness().collect(perception=False, destination=destination)


def test_schema_validity_is_total(run):
    assert run["failures"] == [], f"gate failures: {run['failures']}"


def test_every_sample_row_is_present(run):
    assert set(run["written"]) == set(run["truth"])


def test_no_prediction_breaks_the_bounds_invariant(run):
    """0 <= amount_safe_to_pay <= requested_amount, on the written file."""
    from decimal import Decimal

    for request_id, row in run["written"].items():
        amount = Decimal(row["amount_safe_to_pay"])
        requested = run["truth"][request_id].requested_amount
        assert Decimal(0) <= amount <= requested, request_id


def test_the_scorecard_renders_every_metric(run, capsys):
    """Print it, and assert the sections a reviewer is told to expect exist."""
    lines = _harness().scorecard(run)
    with capsys.disabled():
        print()
        for line in lines:
            print(line)
    text = "\n".join(lines)
    for required in (
        "schema validity",
        "affordability_status",
        "recommended_payment_method",
        "payment_plan exact string",
        "payment_plan structural",
        "earliest_date exact",
        "implied_trough  (PRIMARY)",
        "amount_safe_to_pay",
        "clamp-bound, separate",
        "vision gold set",
    ):
        assert required in text, f"the scorecard no longer reports {required!r}"


def test_the_primary_metric_is_never_quoted_without_its_name(run):
    """Two error figures, and the larger one must not pass for the smaller.

    The trough figure is the primary metric; the raw-amount figure is always
    larger because its denominator is smaller. Both are labelled in the output
    so neither can be quoted as the other.
    """
    text = "\n".join(_harness().scorecard(run))
    trough = next(l for l in text.splitlines() if "implied_trough" in l)
    amount = next(l for l in text.splitlines()
                  if l.startswith("amount_safe_to_pay"))
    assert "PRIMARY" in trough
    assert "median rel err" in amount and "PRIMARY" not in amount
