"""The decision register must describe the system that exists.

A register is only worth having if every claim in it is checkable. Two claims
are: that `read_at` names a file that exists, and that a flag marked switchable
is actually consulted at runtime. Both were wrong before this suite was written —
one entry pointed at a module that had never been created, three pointed at the
wrong module, and one described an escalation to a model the runtime never calls.
"""

from __future__ import annotations

import re

import pytest

from _treewalk import SUBMISSION_ROOT
from bow.settings import load_decisions

DECISIONS = load_decisions().decisions

#: How the pipeline reads a flag. Anything marked switchable must appear here.
FLAG_READ = re.compile(r'flags\[\s*["\']([A-Za-z0-9_]+)["\']\s*\]')


def _flags_read_in_code() -> set[str]:
    found: set[str] = set()
    for path in (SUBMISSION_ROOT / "bow").rglob("*.py"):
        found.update(FLAG_READ.findall(path.read_text(encoding="utf-8")))
    return found


@pytest.mark.parametrize("entry", DECISIONS, ids=lambda e: e.id)
def test_every_read_at_path_exists(entry):
    """`read_at` may name several paths, and may annotate them in brackets."""
    for raw in entry.read_at.split(","):
        target = re.sub(r"\(.*?\)", "", raw).strip()
        if not target or not target.endswith((".py", ".yaml")):
            continue
        assert (SUBMISSION_ROOT / target).exists(), (
            f"{entry.id} says it is read at {target!r}, which does not exist"
        )


def test_every_switchable_flag_is_actually_read_at_runtime():
    read = _flags_read_in_code()
    claimed = {e.id for e in DECISIONS if e.switchable}
    unread = sorted(claimed - read)
    assert not unread, (
        "these are marked switchable but no code consults them, so flipping "
        f"them changes nothing: {unread}"
    )


def test_every_flag_the_code_reads_is_registered_and_marked_switchable():
    read = _flags_read_in_code()
    registered = {e.id: e for e in DECISIONS}
    unregistered = sorted(read - set(registered))
    assert not unregistered, f"code reads unregistered flag(s): {unregistered}"
    mismarked = sorted(f for f in read if not registered[f].switchable)
    assert not mismarked, (
        f"the pipeline reads these, so they are switchable: {mismarked}"
    )


def test_a_non_switchable_entry_still_names_where_the_behaviour_lives():
    """Not a switch is not an excuse for pointing nowhere."""
    for entry in DECISIONS:
        assert entry.read_at.strip(), f"{entry.id} names no location"
