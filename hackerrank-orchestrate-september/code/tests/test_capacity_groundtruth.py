"""Accuracy against published ground truth.

``sample_requests.csv`` carries real values for ``amount_safe_to_pay`` and
``earliest_date_for_full_payment``. This is the first place in the build where
a scored column can be measured rather than argued about.

Five of the twenty-five sample requests belong to users with a blank-amount
event. Those cannot be resolved until the vision layer exists, so they are
reported separately as blocked and excluded from every accuracy figure. They
are not guessed at.

The harness runs under all four combinations of ``Q24_horizon_end_basis`` and
``Q8_balance_as_of_semantics``, and additionally with ``Q22_series_continuation``
on and off, so decisions that ground truth can settle are settled here.

Marked ``dataset``. Run with ``-s`` to see the report.
"""

from __future__ import annotations

import statistics
from collections import Counter
from decimal import Decimal

import pytest

from bow import paths
from bow.contracts import BalanceAsOfBasis, EvidenceLedger, HorizonEndBasis
from bow.data.loaders import load_dataset
from bow.data.normalize import normalize_events
from bow.data.recurrence import project
from bow.engine.capacity import amount_safe_to_pay, earliest_date_for_full_payment
from bow.engine.forecast import build_forecast
from bow.settings import load_thresholds

pytestmark = pytest.mark.dataset

HORIZON_DAYS = 90
ONE_PERCENT = Decimal("0.01")


@pytest.fixture(scope="module")
def world():
    dataset = load_dataset(paths.require_dataset_dir()).dataset
    normalized = normalize_events(
        dataset.events,
        profiles=dataset.profiles,
        rates=dataset.exchange_rates,
        images=dataset.images,
    ).events
    by_user: dict[str, list] = {}
    for event in normalized:
        by_user.setdefault(event.user_id, []).append(event)
    return {
        "dataset": dataset,
        "by_user": by_user,
        "profiles": {p.user_id: p for p in dataset.profiles},
        "thresholds": load_thresholds().recurrence,
    }


def evaluate(world, sample, *, horizon_basis, balance_basis, continuation):
    profile = world["profiles"][sample.user_id]
    events = world["by_user"][sample.user_id]
    projection = project(
        events,
        profile,
        sample.request_date,
        HORIZON_DAYS,
        thresholds=world["thresholds"],
        continue_income_series=continuation,
    )
    forecast = build_forecast(
        EvidenceLedger(request_id=sample.request_id),
        profile,
        projection.events,
        sample.request_date,
        HORIZON_DAYS,
        balance_basis=balance_basis,
    )
    predicted = amount_safe_to_pay(
        forecast, profile.minimum_balance_to_keep, sample.requested_amount
    )
    earliest = earliest_date_for_full_payment(
        forecast,
        profile.minimum_balance_to_keep,
        sample.requested_amount,
        horizon_basis=horizon_basis,
    )
    trough_date, trough_value = forecast.trough(forecast.request_date, forecast.horizon_end)
    return {
        "sample": sample,
        "profile": profile,
        "predicted": predicted,
        "earliest": earliest,
        "forecast": forecast,
        "projection": projection,
        "trough_date": trough_date,
        "trough_value": trough_value,
    }


def blocked_requests(world):
    """Sample requests whose user has a blank-amount event. Typed fields only."""
    blocked = set()
    for sample in world["dataset"].sample_requests:
        if any(e.amount is None for e in world["by_user"][sample.user_id]):
            blocked.add(sample.request_id)
    return blocked


def score(results):
    amount_errors, exact, within_unit, within_pct = [], 0, 0, 0
    date_exact, offsets = 0, []
    confusion = Counter()
    for row in results:
        sample = row["sample"]
        error = abs(row["predicted"] - sample.amount_safe_to_pay)
        amount_errors.append(error)
        if error == 0:
            exact += 1
        if error <= Decimal("0.01"):
            within_unit += 1
        if sample.amount_safe_to_pay and error / abs(sample.amount_safe_to_pay) <= ONE_PERCENT:
            within_pct += 1
        elif not sample.amount_safe_to_pay and error == 0:
            within_pct += 1

        actual_date = sample.earliest_date_for_full_payment
        got = row["earliest"]
        confusion[f"actual={'empty' if actual_date is None else 'set'},"
                  f"predicted={'empty' if got is None else 'set'}"] += 1
        if got == actual_date:
            date_exact += 1
        if got is not None and actual_date is not None:
            offsets.append((got - actual_date).days)
    return {
        "n": len(results),
        "amount_exact": exact,
        "amount_within_unit": within_unit,
        "amount_within_1pct": within_pct,
        "amount_mae": statistics.mean(amount_errors) if amount_errors else Decimal(0),
        "amount_median_ae": statistics.median(amount_errors) if amount_errors else Decimal(0),
        "date_exact": date_exact,
        "offsets": offsets,
        "confusion": dict(confusion),
    }


def test_ground_truth_accuracy(world, capsys):
    blocked = blocked_requests(world)
    measurable = [s for s in world["dataset"].sample_requests if s.request_id not in blocked]

    lines = ["", "=" * 78, "GROUND TRUTH — sample_requests.csv", "=" * 78]
    lines.append(f"sample requests            : {len(world['dataset'].sample_requests)}")
    lines.append(f"BLOCKED (blank-amount evt) : {len(blocked)}  {sorted(blocked)}")
    lines.append(f"measurable                 : {len(measurable)}")
    lines.append("  blocked rows are excluded from every figure below and are not guessed at")
    lines.append("")

    lines.append("--- all four decision combinations, plus Q22 -----------------------------")
    lines.append(
        f"{'Q24 horizon':<10} {'Q8 balance':<12} {'Q22':<5} "
        f"{'amt exact':>9} {'<=1 unit':>9} {'<=1%':>6} {'MAE':>14} {'date exact':>11}"
    )
    grid = {}
    for horizon_basis in HorizonEndBasis:
        for balance_basis in BalanceAsOfBasis:
            for continuation in (True, False):
                rows = [
                    evaluate(
                        world, s,
                        horizon_basis=horizon_basis,
                        balance_basis=balance_basis,
                        continuation=continuation,
                    )
                    for s in measurable
                ]
                result = score(rows)
                grid[(horizon_basis, balance_basis, continuation)] = (result, rows)
                lines.append(
                    f"{horizon_basis.value.split('_')[0]:<10} "
                    f"{balance_basis.value.split('_')[0]:<12} "
                    f"{'on' if continuation else 'off':<5} "
                    f"{result['amount_exact']:>4}/{result['n']:<4} "
                    f"{result['amount_within_unit']:>4}/{result['n']:<4} "
                    f"{result['amount_within_1pct']:>2}/{result['n']:<3} "
                    f"{float(result['amount_mae']):>14,.2f} "
                    f"{result['date_exact']:>5}/{result['n']:<5}"
                )

    default_key = (
        HorizonEndBasis.FIXED_FROM_REQUEST_DATE,
        BalanceAsOfBasis.INCLUSIVE_THROUGH_REQUEST_DATE,
        True,
    )
    result, rows = grid[default_key]
    lines += [
        "",
        "--- defaults in detail (Q24 fixed, Q8 inclusive, Q22 on) -----------------",
        f"amount_safe_to_pay  exact {result['amount_exact']}/{result['n']} "
        f"({result['amount_exact']/result['n']:.1%})  "
        f"within one minor unit {result['amount_within_unit']}/{result['n']}  "
        f"within 1% {result['amount_within_1pct']}/{result['n']}",
        f"                    MAE {float(result['amount_mae']):,.2f}   "
        f"median |error| {float(result['amount_median_ae']):,.2f}",
        f"earliest_date       exact {result['date_exact']}/{result['n']} "
        f"({result['date_exact']/result['n']:.1%})",
        f"                    empty/set confusion: {result['confusion']}",
        f"                    signed day offsets (both populated): "
        f"{dict(sorted(Counter(result['offsets']).items())) or 'none'}",
        "",
        "--- every miss, with trough diagnostics ----------------------------------",
    ]
    misses = [
        r for r in rows
        if r["predicted"] != r["sample"].amount_safe_to_pay
        or r["earliest"] != r["sample"].earliest_date_for_full_payment
    ]
    for row in misses:
        sample, profile = row["sample"], row["profile"]
        lines += [
            "",
            f"  {sample.request_id} / {sample.user_id} ({profile.home_currency})  "
            f"request_date={sample.request_date} requested={sample.requested_amount}",
            f"    amount_safe    predicted={row['predicted']}   actual={sample.amount_safe_to_pay}"
            f"   delta={row['predicted'] - sample.amount_safe_to_pay}",
            f"    earliest_date  predicted={row['earliest']}   "
            f"actual={sample.earliest_date_for_full_payment}",
            f"    opening_balance={profile.current_available_balance}  "
            f"minimum_balance={profile.minimum_balance_to_keep}",
            f"    trough         {row['trough_date']} @ {row['trough_value']}"
            f"   headroom={row['trough_value'] - profile.minimum_balance_to_keep}",
            f"    ground truth   status={sample.affordability_status.value} "
            f"method={sample.recommended_payment_method.value}",
            f"    projected events in horizon: {len(row['forecast'].flows)} "
            f"({len(row['forecast'].inflows)} inflow)",
        ]
        for cash in row["forecast"].flows[:10]:
            lines.append(
                f"      {cash.date}  {cash.direction.value:<7} {str(cash.amount):>16}  "
                f"{cash.source.value:<17} {cash.label[:46]}"
            )
        if len(row["forecast"].flows) > 10:
            lines.append(f"      ... and {len(row['forecast'].flows) - 10} more")

    lines += ["", "--- blocked requests (not guessed) ---------------------------------------"]
    for sample in world["dataset"].sample_requests:
        if sample.request_id in blocked:
            blanks = [e.event_id for e in world["by_user"][sample.user_id] if e.amount is None]
            lines.append(
                f"  {sample.request_id} / {sample.user_id}: blank-amount event(s) {blanks} "
                f"— needs the vision layer. actual safe={sample.amount_safe_to_pay}"
            )
    lines.append("=" * 78)

    with capsys.disabled():
        print("\n".join(lines))

    assert result["n"] > 0, "harness measured nothing"


def test_blocked_requests_are_excluded_not_guessed(world):
    blocked = blocked_requests(world)
    assert blocked, "expected some sample requests to depend on a blank amount"
    assert len(blocked) < len(world["dataset"].sample_requests)


def test_capacity_never_exceeds_requested_on_real_data(world):
    for sample in world["dataset"].sample_requests:
        row = evaluate(
            world, sample,
            horizon_basis=HorizonEndBasis.FIXED_FROM_REQUEST_DATE,
            balance_basis=BalanceAsOfBasis.INCLUSIVE_THROUGH_REQUEST_DATE,
            continuation=True,
        )
        assert Decimal(0) <= row["predicted"] <= sample.requested_amount
