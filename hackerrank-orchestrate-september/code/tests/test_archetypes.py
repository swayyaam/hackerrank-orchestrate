"""The claim this architecture rests on, checked against the real dataset.

``docs/DATA_REPORT.md`` section 3 catalogues eight lifecycle archetypes and
states a correct cash treatment for each. The design claim is that those eight
cases are not eight cases: they are six rules over typed fields, and **not one
of them reads a description**.

This module runs the six rules against every linked pair in the real dataset
and asserts the archetype table's stated treatment for each side of each pair.

If this ever fails, the description string carries information the typed fields
do not, and the architecture has to change rather than acquire a special case.

Marked ``dataset`` because it reads ``dataset/``. Deselect with
``-m 'not dataset'``.
"""

from __future__ import annotations

from collections import Counter

import pytest

from bow import paths
from bow.contracts import DropReason, KeepReason
from bow.data.loaders import load_dataset
from bow.data.normalize import cash_treatment

pytestmark = pytest.mark.dataset

KEEP = "keep"
DROP = "drop"

#: The archetype table from DATA_REPORT.md section 3, transcribed as the
#: expected outcome for (parent, child). The description strings appear HERE,
#: in a test, as the thing being checked. They never appear in bow/.
ARCHETYPE_TABLE: dict[tuple[str, str], tuple[str, str]] = {
    # 1. a cancelled authorization, then the settled purchase
    ("Card authorization", "Settled card purchase"): (DROP, KEEP),
    # 2. a charge and its settled reversal; both real, net zero
    ("Card charge later reversed", "Settled card charge reversal"): (KEEP, KEEP),
    # 3. a purchase and a refund that has not arrived
    ("Purchase awaiting refund", "Pending merchant refund"): (KEEP, DROP),
    # 4. a work expense and its settled reimbursement
    ("Reimbursable work expense", "Employer expense reimbursement"): (KEEP, KEEP),
    # 5. a charge and a pending possible duplicate (governed by Q1)
    ("Original card charge", "Possible duplicate card charge"): (KEEP, KEEP),
    # 6. a failed debit and its scheduled retry
    ("Failed bill payment attempt", "Scheduled bill payment retry"): (DROP, KEEP),
    # 7. an investment bought and sold; both real cash
    ("Investment contribution", "Investment sale proceeds"): (KEEP, KEEP),
    # 8. an investment bought, then revalued on paper
    ("Investment contribution", "Current portfolio valuation"): (KEEP, DROP),
}


def outcome(event) -> str:
    result = cash_treatment(event)
    if isinstance(result, KeepReason):
        return KEEP
    assert isinstance(result, DropReason)
    return DROP


@pytest.fixture(scope="module")
def linked_pairs():
    dataset = load_dataset(paths.require_dataset_dir()).dataset
    by_id = dataset.event_by_id()
    pairs = [
        (by_id[event.linked_event_id], event)
        for event in dataset.events
        if event.linked_event_id
    ]
    assert pairs, "no linked pairs found; this test would be vacuous"
    return pairs


def test_every_linked_pair_matches_the_archetype_table(linked_pairs):
    mismatches: list[str] = []
    unknown: list[str] = []

    for parent, child in linked_pairs:
        key = (parent.description, child.description)
        expected = ARCHETYPE_TABLE.get(key)
        if expected is None:
            unknown.append(f"{parent.event_id}->{child.event_id} {key}")
            continue
        actual = (outcome(parent), outcome(child))
        if actual != expected:
            mismatches.append(
                f"{parent.event_id}->{child.event_id} {key}: "
                f"six rules say {actual}, table says {expected}"
            )

    assert not unknown, (
        "archetype(s) in the data that the table does not describe:\n"
        + "\n".join(unknown)
    )
    assert not mismatches, (
        "THE SIX RULES DO NOT REPRODUCE THE ARCHETYPE TABLE. That means the "
        "description carries information the typed fields do not, and the "
        "architecture must change rather than gain a special case:\n"
        + "\n".join(mismatches)
    )


def test_the_table_is_fully_exercised(linked_pairs):
    """Every archetype must actually occur, or the table is describing fiction."""
    seen = Counter(
        (parent.description, child.description) for parent, child in linked_pairs
    )
    unexercised = sorted(set(ARCHETYPE_TABLE) - set(seen))
    assert not unexercised, f"archetype(s) never observed: {unexercised}"


def test_six_rules_cover_every_event_in_the_file():
    """No event anywhere falls through the six rules."""
    dataset = load_dataset(paths.require_dataset_dir()).dataset
    for event in dataset.events:
        result = cash_treatment(event)
        assert isinstance(result, (KeepReason, DropReason))


def test_treatment_is_independent_of_description(linked_pairs):
    """Swapping descriptions between two events must not change either outcome.

    This is the architectural claim stated as an experiment rather than as a
    comment: if a description mattered, this would fail.
    """
    for parent, child in linked_pairs:
        swapped_parent = parent.model_copy(update={"description": child.description})
        swapped_child = child.model_copy(update={"description": parent.description})
        assert outcome(swapped_parent) == outcome(parent)
        assert outcome(swapped_child) == outcome(child)
