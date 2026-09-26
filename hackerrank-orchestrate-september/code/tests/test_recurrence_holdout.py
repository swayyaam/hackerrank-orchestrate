"""Held-out validation of cadence inference against real scheduled income rows.

A minority of users carry an explicit ``scheduled`` income row: the data
stating when their next inflow arrives and how large it is. That row is free
ground truth for the one thing this system cannot otherwise check.

The experiment: remove the scheduled row, run inference on the remaining
settled history alone, and compare the first predicted inflow against the row
that was removed.

This is the only honest measurement of the recurrence layer available anywhere
in the build, and it costs nothing. Run it with ``-s`` to see the report.

Marked ``dataset``. Deselect with ``-m 'not dataset'``.
"""

from __future__ import annotations

import statistics
from collections import Counter
from decimal import Decimal

import pytest

from bow import paths
from bow.contracts import Direction, EventStatus, EventType
from bow.data.loaders import load_dataset
from bow.data.normalize import normalize_events
from bow.data.recurrence import Cadence, project
from bow.settings import load_thresholds

pytestmark = pytest.mark.dataset

HORIZON_DAYS = 90


def _is_scheduled_income(event) -> bool:
    """Typed fields only. No description is consulted."""
    return (
        event.status is EventStatus.SCHEDULED
        and event.direction is Direction.CREDIT
        and event.event_type is EventType.INCOME
    )


@pytest.fixture(scope="module")
def holdout():
    dataset = load_dataset(paths.require_dataset_dir()).dataset
    normalized = normalize_events(
        dataset.events,
        profiles=dataset.profiles,
        rates=dataset.exchange_rates,
        images=dataset.images,
    ).events
    profiles = {p.user_id: p for p in dataset.profiles}
    requests = {
        r.user_id: r for r in (*dataset.requests, *dataset.sample_requests)
    }
    by_user: dict[str, list] = {}
    for event in normalized:
        by_user.setdefault(event.user_id, []).append(event)

    thresholds = load_thresholds().recurrence
    cases = []
    for user_id, events in by_user.items():
        targets = [e for e in events if _is_scheduled_income(e)]
        if not targets:
            continue
        target = min(targets, key=lambda e: e.settlement_date or e.event_date)
        request = requests.get(user_id)
        if request is None:
            continue
        remaining = [e for e in events if e.event_id != target.event_id]
        result = project(
            remaining,
            profiles[user_id],
            request.request_date,
            HORIZON_DAYS,
            thresholds=thresholds,
        )
        actual_date = target.settlement_date or target.event_date
        inflows = sorted(result.inflows, key=lambda e: e.date)
        exact = [e for e in inflows if e.date == actual_date]
        nearest = (
            min(inflows, key=lambda e: abs((e.date - actual_date).days))
            if inflows
            else None
        )
        cases.append(
            {
                "user_id": user_id,
                "request_date": request.request_date,
                "actual_date": actual_date,
                "actual_amount": target.amount,
                "currency": target.currency,
                "predicted_any": bool(inflows),
                "hit": bool(exact),
                "nearest": nearest,
                "offset": (nearest.date - actual_date).days if nearest else None,
                "predicted_amount": (exact[0].amount if exact else (nearest.amount if nearest else None)),
                "series": result.series,
                "n_inflows": len(inflows),
            }
        )
    return cases


def test_the_holdout_set_is_not_empty(holdout):
    assert len(holdout) >= 2, "no scheduled income rows found; this test is vacuous"


def test_inference_runs_on_every_holdout_user(holdout):
    """Structural: every held-out user produces a result rather than crashing."""
    assert all(case["series"] is not None for case in holdout)


def test_holdout_report(holdout, capsys):
    total = len(holdout)
    predicted = [c for c in holdout if c["predicted_any"]]
    hits = [c for c in holdout if c["hit"]]
    offsets = [c["offset"] for c in predicted if c["offset"] is not None]

    amount_exact = [
        c for c in hits if c["predicted_amount"] == c["actual_amount"]
    ]
    errors = [
        abs(c["predicted_amount"] - c["actual_amount"])
        for c in predicted
        if c["predicted_amount"] is not None
    ]
    relative = [
        abs(c["predicted_amount"] - c["actual_amount"]) / abs(c["actual_amount"])
        for c in predicted
        if c["predicted_amount"] is not None and c["actual_amount"]
    ]

    lines = [
        "",
        "=" * 72,
        "RECURRENCE HOLDOUT — inference vs. removed scheduled income rows",
        "=" * 72,
        f"held-out users                : {total}",
        f"produced any predicted inflow : {len(predicted)} ({len(predicted)/total:.1%})",
        f"EXACT-DATE HIT RATE           : {len(hits)}/{total} ({len(hits)/total:.1%})",
        f"amount exact match (of hits)  : {len(amount_exact)}/{len(hits) or 1}"
        f" ({len(amount_exact)/len(hits):.1%})" if hits else "amount exact match (of hits)  : n/a",
        f"amount MAE (home currency)    : {statistics.mean(errors) if errors else 0:.2f}",
        f"amount median relative error  : {statistics.median(relative) * 100 if relative else 0:.4f}%",
        "",
        "signed day offset of nearest predicted inflow (predicted - actual):",
    ]
    histogram = Counter(offsets)
    for offset in sorted(histogram):
        bar = "#" * histogram[offset]
        lines.append(f"  {offset:+4d} d : {histogram[offset]:>3}  {bar}")
    if not histogram:
        lines.append("  (none)")

    misses = sorted(
        (c for c in holdout if not c["hit"]),
        key=lambda c: (abs(c["offset"]) if c["offset"] is not None else 10**6),
        reverse=True,
    )[:5]
    lines += ["", f"worst {len(misses)} miss(es), with full series:", ""]
    for case in misses:
        lines += [
            f"  {case['user_id']}  request_date={case['request_date']}  "
            f"actual={case['actual_date']} {case['actual_amount']} {case['currency']}",
            f"    predicted inflows={case['n_inflows']}  "
            f"nearest={case['nearest'].date if case['nearest'] else None}  "
            f"offset={case['offset']}  predicted_amount={case['predicted_amount']}",
        ]
        income_series = [
            s for s in case["series"] if s.key.direction is Direction.CREDIT
        ]
        if not income_series:
            lines.append("    (no credit series at all in settled history)")
        for series in income_series:
            lines.append(
                f"    [{series.cadence.value:18}] n={series.occurrences:<3} "
                f"gaps={list(series.gaps_days)[:6]} anchor={series.anchor_day} "
                f"last={series.last_occurrence} age={series.age_days}d "
                f"projectable={series.projectable} "
                f"reason={series.not_projected_reason.value if series.not_projected_reason else '-'}"
            )
            lines.append(f"      key: {series.key.label()}")
        lines.append("")

    cadences = Counter(
        s.cadence.value for c in holdout for s in c["series"]
    )
    lines += [f"series cadence mix across held-out users: {dict(cadences)}", "=" * 72]

    with capsys.disabled():
        print("\n".join(lines))

    # Non-vacuity only. No threshold was tuned against this number.
    assert total > 0
