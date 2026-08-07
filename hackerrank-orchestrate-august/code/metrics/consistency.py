"""Consistency scan: group by a composite key and compare within groups.

A single groupby. No all-pairs comparison -- 110 rows would be 5,995 pairs.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

GROUPING_KEY: tuple[str, ...] = (
    "conversation_type",
    "sender_relationship_class",
    "proposed_message_type",
    "integrity_state",
)


def compute_consistency(rows: pd.DataFrame) -> dict[str, Any]:
    """Report groups of structurally similar rows that received different actions.

    A divergent group is *explained* when something recorded on the row differs
    between members that got different actions -- either the set of override
    codes, or the model's own proposed action.

    The proposal counts, and deliberately so. The grouping key holds the
    *structural* facts plus the proposed message type; two rows alike on all of
    those can still be read differently by perception, and when they are, the
    proposal is the cause and it is recorded on the row. Calling that
    "unexplained" would mean the model is never permitted to distinguish two
    structurally similar messages -- which is the entire reason perception
    exists. What remains unexplained is the case worth alarming on: identical
    codes AND an identical proposal producing different actions, which can only
    come from an unrecorded input or from nondeterminism.
    """
    if rows.empty:
        return {"n_rows": 0, "status": "no rows"}

    missing = [column for column in GROUPING_KEY if column not in rows.columns]
    if missing:
        return {"n_rows": len(rows), "status": "unavailable", "missing_fields": missing}

    divergent: list[dict[str, Any]] = []
    unexplained = 0
    groups = rows.groupby(list(GROUPING_KEY), sort=True)

    for key, frame in groups:
        if len(frame) < 2 or frame["action"].nunique() < 2:
            continue
        code_sets = {
            action: frozenset(
                code for codes in sub["override_codes_fired"] for code in codes
            )
            for action, sub in frame.groupby("action", sort=True)
        }
        proposals = {
            action: frozenset(sub["model_proposal"].map(lambda p: p["action"]))
            for action, sub in frame.groupby("action", sort=True)
        }
        codes_differ = len(set(code_sets.values())) > 1
        proposals_differ = len(set(proposals.values())) > 1
        explained = codes_differ or proposals_differ
        if not explained:
            unexplained += 1
        distinguishing = sorted(
            set().union(*code_sets.values()) - set.intersection(*[set(s) for s in code_sets.values()])
        )
        if proposals_differ:
            distinguishing = [*distinguishing, "MODEL_PROPOSAL"]
        divergent.append(
            {
                "key": dict(zip(GROUPING_KEY, key)),
                "size": len(frame),
                "actions": {a: int(c) for a, c in frame["action"].value_counts().sort_index().items()},
                "explained": explained,
                "distinguishing_codes": distinguishing,
                "distinguished_by_proposal": proposals_differ,
                "members": [
                    {
                        "message_id": row["message_id"],
                        "action": row["action"],
                        "override_codes_fired": list(row["override_codes_fired"]),
                        "proposed_action": row["model_proposal"]["action"],
                        "confidence": float(row["confidence"]),
                    }
                    for _, row in frame.sort_values("message_id").iterrows()
                ],
            }
        )

    return {
        "n_rows": len(rows),
        "grouping_key": list(GROUPING_KEY),
        "groups_total": int(groups.ngroups),
        "groups_with_divergence": len(divergent),
        "groups_diverging_without_code_difference": unexplained,
        "divergent_groups": divergent,
    }
