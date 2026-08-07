"""Two-run comparison, and the determinism gate."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from metrics.loader import OVERRIDE_CODES, RunData
from metrics.labeled import REPORTABLE_THRESHOLD_ROWS

VOLATILE_FIELDS: dict[str, frozenset[str]] = {
    "rows.jsonl": frozenset({"wall_clock_ms"}),
    "eval_rows.jsonl": frozenset({"wall_clock_ms"}),
    "manifest.json": frozenset({"run_id", "created_at_utc"}),
}
"""Fields that legitimately vary between two runs of identical code. Anything
differing outside this allowlist fails the determinism gate. A new timing or
timestamp field must be added here explicitly or the gate breaks loudly.

events.jsonl is excluded from the comparison entirely: it is a log stream whose
timestamps and interleaving are inherently volatile.
"""


class RunsDifferError(Exception):
    """Two runs differ outside the volatile-field allowlist."""


def _strip(record: dict[str, Any], volatile: frozenset[str]) -> dict[str, Any]:
    return {k: v for k, v in record.items() if k not in volatile}


def assert_runs_identical(baseline: RunData, candidate: RunData) -> None:
    """Field-level equality ignoring VOLATILE_FIELDS.

    Raises:
        RunsDifferError: naming every differing field. This is the determinism
            gate from IMPLEMENTATION_PLAN Phase 0 verification 3.
    """
    problems: list[str] = []

    for name, frame_a, frame_b in (
        ("rows.jsonl", baseline.rows, candidate.rows),
        ("eval_rows.jsonl", baseline.eval_rows, candidate.eval_rows),
    ):
        volatile = VOLATILE_FIELDS[name]
        if len(frame_a) != len(frame_b):
            problems.append(f"{name}: row count {len(frame_a)} vs {len(frame_b)}")
            continue
        a = {r["message_id"]: _strip(r, volatile) for r in frame_a.to_dict("records")}
        b = {r["message_id"]: _strip(r, volatile) for r in frame_b.to_dict("records")}
        if set(a) != set(b):
            problems.append(f"{name}: message_id sets differ")
            continue
        for message_id in sorted(a):
            for field in sorted(set(a[message_id]) | set(b[message_id])):
                left = json.dumps(a[message_id].get(field), sort_keys=True, default=str)
                right = json.dumps(b[message_id].get(field), sort_keys=True, default=str)
                if left != right:
                    problems.append(f"{name}:{message_id}.{field}: {left} != {right}")

    volatile = VOLATILE_FIELDS["manifest.json"]
    ma, mb = _strip(baseline.manifest, volatile), _strip(candidate.manifest, volatile)
    for field in sorted(set(ma) | set(mb)):
        left = json.dumps(ma.get(field), sort_keys=True, default=str)
        right = json.dumps(mb.get(field), sort_keys=True, default=str)
        if left != right:
            problems.append(f"manifest.json.{field}: {left} != {right}")

    if problems:
        raise RunsDifferError(
            f"{len(problems)} field(s) differ outside the volatile allowlist:\n  "
            + "\n  ".join(problems[:40])
        )


def compute_diff(baseline: RunData, candidate: RunData) -> dict[str, Any]:
    """Compare two runs. Rows join on message_id."""
    base_rows = {r["message_id"]: r for r in baseline.rows.to_dict("records")}
    cand_rows = {r["message_id"]: r for r in candidate.rows.to_dict("records")}
    shared = sorted(set(base_rows) & set(cand_rows))

    changed: list[dict[str, Any]] = []
    action_changed = type_changed = confidence_changed = unexplained = 0
    unexplained_ids: list[str] = []

    for message_id in shared:
        a, b = base_rows[message_id], cand_rows[message_id]
        acts = (a["action"], b["action"])
        types = (a["message_type"], b["message_type"])
        codes = (sorted(a["override_codes_fired"]), sorted(b["override_codes_fired"]))
        warns = (sorted(a["integrity_warnings"]), sorted(b["integrity_warnings"]))
        proposals = (a["model_proposal"]["action"], b["model_proposal"]["action"])
        if a["confidence"] != b["confidence"]:
            confidence_changed += 1
        if types[0] != types[1]:
            type_changed += 1
        if acts[0] == acts[1]:
            continue
        action_changed += 1
        # The model's proposal counts as an explanation, exactly as it does in
        # the consistency scan. A prompt revision changes what perception reads
        # out of a message; a row that moves because the proposal moved is
        # explained by something recorded on it. Without this the two
        # instruments disagree about the same phenomenon -- consistency calls it
        # explained, the diff calls it UNEXPLAINED -- and the reader cannot tell
        # which is right. What stays unexplained is a row that moved with the
        # codes, the warnings AND the proposal all unchanged.
        explained = (
            codes[0] != codes[1] or warns[0] != warns[1] or proposals[0] != proposals[1]
        )
        if not explained:
            unexplained += 1
            unexplained_ids.append(message_id)
        changed.append(
            {
                "message_id": message_id,
                "action": {"from": acts[0], "to": acts[1]},
                "message_type": {"from": types[0], "to": types[1]},
                "confidence": {"from": a["confidence"], "to": b["confidence"]},
                "override_codes_fired": {"from": codes[0], "to": codes[1]},
                "integrity_warnings": {"from": warns[0], "to": warns[1]},
                "model_proposal": {"from": proposals[0], "to": proposals[1]},
                "explained": explained,
            }
        )

    net = _net_labeled(baseline, candidate)
    ratio = (
        round(action_changed / net["net_rows"], 2)
        if net["net_rows"] > 0
        else None
    )

    deltas: dict[str, dict[str, int]] = {}
    for code in OVERRIDE_CODES:
        before = sum(1 for r in base_rows.values() if code in r["override_codes_fired"])
        after = sum(1 for r in cand_rows.values() if code in r["override_codes_fired"])
        if before != after:
            deltas[code] = {"from": before, "to": after, "delta": after - before}

    return {
        "baseline_run_id": baseline.run_id,
        "candidate_run_id": candidate.run_id,
        "config_delta": _config_delta(baseline.manifest, candidate.manifest),
        "dataset_fingerprint_changed": baseline.manifest.get("dataset_fingerprint")
        != candidate.manifest.get("dataset_fingerprint"),
        "gross_churn": {
            "rows_action_changed": action_changed,
            "share": round(action_changed / len(shared), 4) if shared else 0.0,
            "rows_type_changed": type_changed,
            "rows_confidence_changed": confidence_changed,
        },
        "net_labeled": net,
        "churn_to_net_ratio": {
            "gross_churn_rows": action_changed,
            "net_improvement_rows": net["net_rows"],
            "ratio": ratio,
            "interpretation": _interpret(action_changed, net),
        },
        "override_code_deltas": deltas,
        "unexplained_action_changes": {
            "count": unexplained,
            "note": (
                "Action changed with no change in override codes or integrity warnings. "
                "Indicates model nondeterminism or an unrecorded input, not a design change."
            ),
            "message_ids": unexplained_ids,
        },
        "changed_rows": changed,
    }


def _net_labeled(baseline: RunData, candidate: RunData) -> dict[str, Any]:
    if baseline.eval_rows.empty or candidate.eval_rows.empty or baseline.eval_truth.empty:
        return {"improved": 0, "regressed": 0, "unchanged": 0, "net_rows": 0, "net_pp": 0.0,
                "verdict": "UNAVAILABLE"}
    truth = dict(zip(baseline.eval_truth["message_id"], baseline.eval_truth["action"]))
    a = dict(zip(baseline.eval_rows["message_id"], baseline.eval_rows["action"]))
    b = dict(zip(candidate.eval_rows["message_id"], candidate.eval_rows["action"]))
    improved = regressed = unchanged = 0
    for message_id, gold in truth.items():
        was, now = a.get(message_id), b.get(message_id)
        if was is None or now is None:
            continue
        if was != gold and now == gold:
            improved += 1
        elif was == gold and now != gold:
            regressed += 1
        else:
            unchanged += 1
    net = improved - regressed
    n = max(len(truth), 1)
    return {
        "improved": improved,
        "regressed": regressed,
        "unchanged": unchanged,
        "net_rows": net,
        "net_pp": round(100.0 * net / n, 2),
        "verdict": "WITHIN_NOISE" if abs(net) < REPORTABLE_THRESHOLD_ROWS else (
            "IMPROVED" if net > 0 else "REGRESSED"
        ),
    }


def _interpret(churn: int, net: dict[str, Any]) -> str:
    if churn == 0:
        return "No row changed action."
    if net["verdict"] == "UNAVAILABLE":
        return f"{churn} rows changed action; no labeled comparison was available."
    if net["verdict"] == "WITHIN_NOISE":
        return (
            f"{churn} rows changed action to gain a net {net['net_rows']} labeled row(s), which is "
            f"within noise. This change reshuffled behaviour rather than improving it."
        )
    return (
        f"{churn} rows changed action for a net {net['net_rows']} labeled row(s) "
        f"({net['net_pp']}pp), which is outside the noise floor."
    )


def _config_delta(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    sa, sb = a.get("config_snapshot", {}) or {}, b.get("config_snapshot", {}) or {}
    for section in sorted(set(sa) | set(sb)):
        for key in sorted(set(sa.get(section, {})) | set(sb.get(section, {}))):
            va, vb = sa.get(section, {}).get(key), sb.get(section, {}).get(key)
            if va != vb:
                out[f"{section}.{key}"] = {"from": va, "to": vb}
    return out
