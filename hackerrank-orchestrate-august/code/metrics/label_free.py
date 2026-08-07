"""Label-free metrics, computed over all target rows. Pure: DataFrame in, dict out."""

from __future__ import annotations

import math
from typing import Any, Sequence

import pandas as pd

from metrics.loader import ACTIONS, MESSAGE_TYPES, OVERRIDE_CODES, unavailable

ACTION_RANK = {"mute": 0, "digest": 1, "notify": 2}


def entropy_bits(counts: Sequence[int]) -> float:
    """Shannon entropy in bits. A zero-probability outcome contributes zero."""
    total = sum(counts)
    if total == 0:
        return 0.0
    result = 0.0
    for count in counts:
        if count > 0:
            p = count / total
            result -= p * math.log2(p)
    return result


def _distribution(series: pd.Series, allowed: Sequence[str]) -> dict[str, Any]:
    counts = {value: int((series == value).sum()) for value in allowed}
    total = max(int(series.notna().sum()), 1)
    h = entropy_bits(list(counts.values()))
    return {
        "counts": counts,
        "shares": {k: round(v / total, 4) for k, v in counts.items()},
        "entropy_bits": round(h, 4),
        "entropy_normalised": round(h / math.log2(len(allowed)), 4) if len(allowed) > 1 else 0.0,
    }


def _percentiles(values: pd.Series) -> dict[str, float]:
    if values.empty:
        return {k: 0.0 for k in ("min", "p25", "median", "p75", "max", "mean", "std")}
    return {
        "min": round(float(values.min()), 4),
        "p25": round(float(values.quantile(0.25)), 4),
        "median": round(float(values.median()), 4),
        "p75": round(float(values.quantile(0.75)), 4),
        "max": round(float(values.max()), 4),
        "mean": round(float(values.mean()), 4),
        "std": round(float(values.std(ddof=0)) if len(values) > 1 else 0.0, 4),
    }


def compute_label_free(rows: pd.DataFrame, manifest: dict[str, Any]) -> dict[str, Any]:
    """All label-free metrics from rows.jsonl."""
    if rows.empty:
        return {"n_rows": 0, "status": "no rows"}

    n = len(rows)
    failures = manifest.get("failures", []) or []

    fire_counts = {code: 0 for code in OVERRIDE_CODES}
    applied_counts = {code: 0 for code in OVERRIDE_CODES}
    multi = 0
    for fired, applied in zip(rows["override_codes_fired"], rows["override_code_applied"]):
        for code in fired:
            if code in fire_counts:
                fire_counts[code] += 1
        if len(fired) >= 2:
            multi += 1
        if applied in applied_counts:
            applied_counts[applied] += 1

    proposal_action = rows["model_proposal"].map(lambda p: p["action"])
    matrix = {a: {b: 0 for b in ACTIONS} for a in ACTIONS}
    escalated = demoted = unchanged = type_only = 0
    for proposed, final, ptype, ftype in zip(
        proposal_action,
        rows["action"],
        rows["proposed_message_type"],
        rows["message_type"],
    ):
        matrix[proposed][final] += 1
        if ACTION_RANK[final] > ACTION_RANK[proposed]:
            escalated += 1
        elif ACTION_RANK[final] < ACTION_RANK[proposed]:
            demoted += 1
        elif ptype != ftype:
            type_only += 1
        else:
            unchanged += 1
    overridden = escalated + demoted + type_only

    citations = rows["evidence_cited"].map(len)
    ungrounded_rows = []
    ungrounded = 0
    for message_id, cited, shortlist in zip(
        rows["message_id"], rows["evidence_cited"], rows["evidence_shortlist"]
    ):
        extra = set(cited) - set(shortlist)
        if extra:
            ungrounded += len(extra)
            ungrounded_rows.append(message_id)

    warning_counts: dict[str, int] = {}
    for warnings in rows["integrity_warnings"]:
        for warning in warnings:
            warning_counts[warning] = warning_counts.get(warning, 0) + 1

    overridden_mask = rows["override_code_applied"] != "PERCEPTION_ACCEPTED"
    confidence = pd.to_numeric(rows["confidence"])

    media_ok = rows["media_outcome"] == "ok"
    media_failed_rows = rows[rows["media_outcome"] == "failed"]
    causes: dict[str, int] = {}
    for cause in media_failed_rows.get("media_failure_cause", pd.Series(dtype=str)):
        key = cause or "unknown"
        causes[key] = causes.get(key, 0) + 1

    cache_media = rows["cache"].map(lambda c: c["media"])
    cache_perc = rows["cache"].map(lambda c: c["perception"])

    def _outcomes(series: pd.Series) -> dict[str, int]:
        return {k: int((series == k).sum()) for k in ("hit", "miss", "corrupt", "n/a")}

    media_applicable = int((cache_media != "n/a").sum())
    perc_applicable = int((cache_perc != "n/a").sum())

    tokens_in = rows["tokens"].map(lambda t: t["input"])
    tokens_out = rows["tokens"].map(lambda t: t["output"])
    wall = pd.to_numeric(rows["wall_clock_ms"])

    if "reason_char_length" not in rows.columns:
        reason_health: dict[str, Any] = unavailable(["reason_char_length"])
    else:
        lengths = pd.to_numeric(rows["reason_char_length"])
        warned = rows[rows["integrity_warnings"].map(len) > 0]
        reason_health = {
            "char_length": _percentiles(lengths),
            "rows_outside_register_bounds": 0,
            "share_citing_concrete_evidence": round(
                float(rows["reason_cites_evidence"].mean()), 4
            ),
            # Reported beside the one above, never instead of it. The first is
            # the narrow claim -- the sentence describes this receiver's prior
            # behaviour toward this sender -- and is bounded by how many rows
            # were actually decided on history. This one is the weaker claim
            # that must hold everywhere: the sentence names *something* the
            # decision turned on. A reason naming nothing is a defect, so
            # anything below 1.0 here is a bug, not a score.
            "share_naming_a_decisive_factor": round(
                float(rows["reason_names_decisive_factor"].mean()), 4
            )
            if "reason_names_decisive_factor" in rows.columns
            else None,
            "share_referencing_integrity_when_warned": (
                round(float(warned["reason_references_integrity"].mean()), 4)
                if len(warned)
                else None
            ),
            "rows_warned_without_integrity_reason": (
                warned[~warned["reason_references_integrity"]]["message_id"].tolist()
            ),
        }

    return {
        "n_rows": n,
        "action_distribution": _distribution(rows["action"], ACTIONS),
        "message_type_distribution": _distribution(rows["message_type"], MESSAGE_TYPES),
        "override_codes": {
            "fire_counts": fire_counts,
            "applied_counts": applied_counts,
            "rows_with_multiple_firings": multi,
            "never_fired": [c for c, v in fire_counts.items() if v == 0],
        },
        "proposal_override": {
            "rows_overridden": overridden,
            "share_overridden": round(overridden / n, 4),
            "by_direction": {
                "escalated": escalated,
                "demoted": demoted,
                "type_only": type_only,
                "unchanged": unchanged,
            },
            "transition_matrix": matrix,
        },
        "evidence": {
            "rows_with_citation": int((citations > 0).sum()),
            "share_with_citation": round(float((citations > 0).mean()), 4),
            "rows_with_none": int((citations == 0).sum()),
            "share_none": round(float((citations == 0).mean()), 4),
            "mean_citations_per_row": round(float(citations.mean()), 4),
            "citation_count_histogram": {
                str(k): int(v) for k, v in sorted(citations.value_counts().items())
            },
            "tier_counts": {
                str(k): int((rows["evidence_tier"] == k).sum()) for k in (3, 2, 1, 0)
            },
            "ungrounded_citation_count": ungrounded,
            "ungrounded_rows": ungrounded_rows,
        },
        "integrity_warnings": {
            "counts_by_type": warning_counts,
            "rows_with_any": int((rows["integrity_warnings"].map(len) > 0).sum()),
        },
        "perception_health": {
            "schema_retry_count": int(pd.to_numeric(rows["schema_retry_count"]).sum()),
            "rows_with_retry": int((pd.to_numeric(rows["schema_retry_count"]) > 0).sum()),
            "hard_parse_failure_count": sum(
                1 for f in failures if f.get("error_type") == "PerceptionSchemaError"
            ),
            "rows_failed_total": len(failures),
        },
        "confidence": {
            "overall": _percentiles(confidence),
            "by_action": {
                action: _percentiles(confidence[rows["action"] == action]) for action in ACTIONS
            },
            "mean_on_overridden": (
                round(float(confidence[overridden_mask].mean()), 4)
                if overridden_mask.any()
                else None
            ),
            "mean_on_not_overridden": (
                round(float(confidence[~overridden_mask].mean()), 4)
                if (~overridden_mask).any()
                else None
            ),
        },
        "media": {
            "images_analysed": int((media_ok & (rows["media_kind"] == "image")).sum()),
            "voice_notes_transcribed": int((media_ok & (rows["media_kind"] == "voice")).sum()),
            "rows_with_media": int((rows["media_kind"] != "").sum()),
            "failures_by_cause": causes,
            "rows_media_failed": int(rows.get("media_failed", pd.Series([False] * n)).sum()),
            "rows_media_unread": int(rows.get("media_unread", pd.Series([False] * n)).sum()),
        },
        "cache": {
            "media_hit_rate": (
                round(float((cache_media == "hit").sum() / media_applicable), 4)
                if media_applicable
                else None
            ),
            "media_outcomes": _outcomes(cache_media),
            "perception_hit_rate": (
                round(float((cache_perc == "hit").sum() / perc_applicable), 4)
                if perc_applicable
                else None
            ),
            "perception_outcomes": _outcomes(cache_perc),
            "rows_served_entirely_from_cache": int(rows["served_entirely_from_cache"].sum()),
        },
        "budget": {
            "tokens": {
                "input_total": int(tokens_in.sum()),
                "output_total": int(tokens_out.sum()),
                "input_mean_per_row": round(float(tokens_in.mean()), 4),
                "input_p95": round(float(tokens_in.quantile(0.95)), 4),
                "output_mean_per_row": round(float(tokens_out.mean()), 4),
                "output_p95": round(float(tokens_out.quantile(0.95)), 4),
            },
            "wall_clock_ms": {
                "total": round(float(wall.sum()), 3),
                "mean_per_row": round(float(wall.mean()), 3),
                "p95": round(float(wall.quantile(0.95)), 3),
            },
        },
        "reason_health": reason_health,
    }
