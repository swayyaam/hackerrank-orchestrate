"""Choosing between safe plans.

The specification gives six ranking keys in order. They live here, in one
function, as one tuple. Scattering the comparison across call sites is how a
ranking quietly stops matching its own specification.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

from bow.contracts.enums import PaymentMethod
from bow.engine.plans import PlanCandidate

__all__ = ["RANKING_KEYS", "best_plan", "option_rank", "sort_key"]

#: Documentation of the tuple below, in the spec's own numbering.
RANKING_KEYS = (
    "1. complete the full request by desired_completion_date",
    "2. require no spending changes",
    "3. minimize the total amount paid",
    "4. start payment earlier",
    "5. use fewer payments",
    "6. lowest payment_option_id as the final tie-breaker",
)

#: Split an identifier into digit and non-digit runs. Rule 6 says *lowest*
#: payment_option_id, and an id whose numeric part is not fixed-width does not
#: order correctly as a plain string: a two-digit and a three-digit suffix under
#: the same prefix compare on their first character. Comparing digit runs as
#: numbers is what "lowest" means for an identifier that carries a number.
_ID_RUNS = re.compile(r"\d+|\D+")


def option_rank(option_id: str | None) -> tuple[int, tuple[tuple[int, int, str], ...]]:
    """Ordering key for rule 6, natural rather than lexicographic.

    A plan with no supplied option id ranks after every plan that has one: rule
    6 is stated over ``payment_option_id`` values, so a candidate without one
    has nothing to compare and takes the remaining position.
    """
    if option_id is None:
        return (1, ())
    runs = tuple(
        (0, int(run), "") if run.isdigit() else (1, 0, run)
        for run in _ID_RUNS.findall(option_id)
    )
    return (0, runs)


def sort_key(
    plan: PlanCandidate,
) -> tuple[bool, bool, Decimal, date, int, tuple[int, tuple[tuple[int, int, str], ...]]]:
    """The spec's six keys, in order, as one ascending sort key.

    Each element is negated or not so that **smaller is better** throughout:

    ``not completes_by_deadline``  rule 1 — completing by the deadline wins
    ``requires_spending_changes``  rule 2 — needing no change wins
    ``total_payable``              rule 3 — paying less wins
    ``first_payment_date``         rule 4 — starting earlier wins
    ``payment_count``              rule 5 — fewer payments wins
    ``option_rank(option_id)``     rule 6 — lowest id breaks the remaining tie
    """
    return (
        not plan.completes_by_deadline,
        plan.requires_spending_changes,
        plan.total_payable,
        plan.first_payment_date,
        plan.payment_count,
        option_rank(plan.option_id),
    )


def best_plan(candidates: tuple[PlanCandidate, ...]) -> PlanCandidate | None:
    """The highest-ranked candidate, or ``None`` when there is none."""
    return min(candidates, key=sort_key) if candidates else None


def supplied_option_count(candidates: tuple[PlanCandidate, ...]) -> int:
    """How many candidates came from a supplied payment option.

    The reconnaissance found that no request has more than one eligible supplied
    option, which would mean the tie-breakers almost never bind. That is an
    observation about one dataset, asserted as a count and never depended on.
    """
    return sum(
        1
        for plan in candidates
        if plan.option_id is not None and plan.method is PaymentMethod.INSTALLMENTS
    )
