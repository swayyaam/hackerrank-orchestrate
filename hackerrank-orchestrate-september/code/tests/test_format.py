"""Formatter round-trips against every distinct value in the ground truth.

The three formats disagree with each other on purpose, and matching them is
free accuracy that is easy to lose. Every distinct value the published sample
answers contain is asserted here, so a regression is caught by a unit test
rather than by a scoring drop.
"""

from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from bow import paths
from bow.format import (
    format_explanation_amount,
    format_explanation_date,
    format_minimal_amount,
    format_payment_plan,
    format_plan_amount,
    quantize_money,
)


def _sample_rows():
    path = paths.DATASET_DIR / "sample_requests.csv"
    if not path.is_file():
        pytest.skip("dataset not available")
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


# --- offline unit cases -------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("25256", "25256"), ("17229139.2", "17229139.2"), ("603.3", "603.3"),
        ("87170.56", "87170.56"), ("462", "462"), ("0", "0"),
        ("620.40", "620.4"), ("1425000", "1425000"),
        ("356.154888888888888888888892", "356.15"),
    ],
)
def test_minimal_amount(value, expected):
    assert format_minimal_amount(Decimal(value)) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("25256", "25256"), ("620.4", "620.40"), ("15952906.67", "15952906.67"),
        ("3246.1", "3246.10"), ("996.6", "996.60"), ("23.5", "23.50"),
        ("38016", "38016"), ("665950", "665950"),
    ],
)
def test_plan_amount(value, expected):
    assert format_plan_amount(Decimal(value)) == expected


@pytest.mark.parametrize(
    ("currency", "value", "expected"),
    [
        ("ZAR", "25256", "ZAR 25,256"),
        ("IDR", "15952906.67", "IDR 15,952,906.67"),
        ("EUR", "620.4", "EUR 620.40"),
        ("USD", "23.5", "USD 23.50"),
        ("INR", "92800", "INR 92,800"),
        ("EUR", "5414.2", "EUR 5,414.20"),
        ("IDR", "34140600", "IDR 34,140,600"),
    ],
)
def test_explanation_amount(currency, value, expected):
    assert format_explanation_amount(currency, Decimal(value)) == expected


@pytest.mark.parametrize(
    ("iso", "expected"),
    [
        ("2019-11-15", "15 November 2019"),
        ("2025-08-08", "8 August 2025"),
        ("2026-01-12", "12 January 2026"),
        ("2026-03-01", "1 March 2026"),
    ],
)
def test_explanation_date(iso, expected):
    assert format_explanation_date(date.fromisoformat(iso)) == expected


def test_the_two_amount_formats_disagree_on_the_same_value():
    """This is the whole point, and the reason they are separate functions."""
    value = Decimal("620.40")
    assert format_minimal_amount(value) == "620.4"
    assert format_plan_amount(value) == "620.40"


# --- round-trips against the published ground truth ---------------------------


@pytest.mark.dataset
def test_amount_safe_to_pay_round_trips_every_ground_truth_value():
    values = {row["amount_safe_to_pay"] for row in _sample_rows()}
    assert values, "no values found"
    mismatches = [
        (text, format_minimal_amount(Decimal(text)))
        for text in sorted(values)
        if format_minimal_amount(Decimal(text)) != text
    ]
    assert not mismatches, f"amount_safe_to_pay formatter mismatches: {mismatches}"


@pytest.mark.dataset
def test_payment_plan_round_trips_every_ground_truth_value():
    mismatches = []
    seen = 0
    for row in _sample_rows():
        plan = row["payment_plan"]
        if plan == "none":
            continue
        payments = tuple(
            (date.fromisoformat(e.split(":")[0]), Decimal(e.split(":")[1]))
            for e in plan.split("|")
        )
        seen += 1
        rebuilt = format_payment_plan(payments)
        if rebuilt != plan:
            mismatches.append((plan, rebuilt))
    assert seen, "no plans found"
    assert not mismatches, f"payment_plan formatter mismatches: {mismatches}"


@pytest.mark.dataset
def test_spending_change_amounts_round_trip():
    mismatches = []
    for row in _sample_rows():
        changes = row["spending_changes_needed"]
        if changes == "none":
            continue
        for part in changes.split("|"):
            if part.startswith("reduce_to:"):
                _, _event, amount = part.split(":")
                rebuilt = format_plan_amount(Decimal(amount))
                if rebuilt != amount:
                    mismatches.append((amount, rebuilt))
    assert not mismatches, f"reduce_to amount mismatches: {mismatches}"


@pytest.mark.dataset
def test_every_currency_amount_in_the_explanations_round_trips():
    """Pull every `CUR 1,234.56` token out of the published explanations."""
    import re

    # Anchored on digit groups so a sentence comma after the number
    # ("INR 68,432, starting ...") is not swallowed into the match.
    token = re.compile(r"\b(INR|ZAR|IDR|USD|EUR) (\d{1,3}(?:,\d{3})*(?:\.\d+)?)")
    mismatches = []
    seen = 0
    for row in _sample_rows():
        for currency, amount in token.findall(row["decision_explanation"]):
            seen += 1
            value = Decimal(amount.replace(",", ""))
            rebuilt = format_explanation_amount(currency, value)
            if rebuilt != f"{currency} {amount}":
                mismatches.append((f"{currency} {amount}", rebuilt))
    assert seen > 10, f"only {seen} amounts found in explanations"
    assert not mismatches, f"explanation amount mismatches: {mismatches}"


@pytest.mark.dataset
def test_every_date_in_the_explanations_round_trips():
    import re

    token = re.compile(r"\b(\d{1,2}) (January|February|March|April|May|June|July|"
                       r"August|September|October|November|December) (\d{4})\b")
    months = {
        "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
        "July": 7, "August": 8, "September": 9, "October": 10,
        "November": 11, "December": 12,
    }
    seen, mismatches = 0, []
    for row in _sample_rows():
        for day, month, year in token.findall(row["decision_explanation"]):
            seen += 1
            when = date(int(year), months[month], int(day))
            rebuilt = format_explanation_date(when)
            if rebuilt != f"{day} {month} {year}":
                mismatches.append((f"{day} {month} {year}", rebuilt))
    assert seen > 5, f"only {seen} dates found"
    assert not mismatches, f"explanation date mismatches: {mismatches}"


def test_quantization_happens_exactly_once_on_the_way_out():
    raw = Decimal("1800") * Decimal("83.33") / Decimal("3")
    assert quantize_money(raw) == Decimal("49998.00")
    assert format_minimal_amount(raw) == "49998"
