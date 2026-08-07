"""Label-dependent metrics over the eval rows only.

Confusion matrix, calibration bucketing, ECE and Brier are implemented directly
-- stdlib and pandas only, no sklearn.

Every figure carries the noise floor: at n=30 one row moves any rate by 3.33
percentage points, and a change under two rows is never called an improvement.
"""

from __future__ import annotations

from typing import Any, Sequence

import pandas as pd

from metrics.loader import ACTIONS, MESSAGE_TYPES

REPORTABLE_THRESHOLD_ROWS = 2
"""SOURCE (b): one row is indistinguishable from noise at this sample size; two
is the smallest difference that can be described as a change at all."""

CALIBRATION_BUCKET_EDGES: tuple[float, ...] = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
"""SOURCE (b): five equal-width buckets, not the conventional ten. At n=30 ten
buckets average three rows each and per-bucket accuracy is meaningless."""


def confusion_matrix(truth: Sequence[str], pred: Sequence[str], labels: Sequence[str]) -> list[list[int]]:
    """Rows are truth, columns are prediction."""
    index = {label: i for i, label in enumerate(labels)}
    matrix = [[0 for _ in labels] for _ in labels]
    for t, p in zip(truth, pred):
        if t in index and p in index:
            matrix[index[t]][index[p]] += 1
    return matrix


def per_class(matrix: list[list[int]], labels: Sequence[str]) -> dict[str, dict[str, float]]:
    """Precision, recall, F1 and support per class, from the matrix alone."""
    out: dict[str, dict[str, float]] = {}
    for i, label in enumerate(labels):
        tp = matrix[i][i]
        support = sum(matrix[i])
        predicted = sum(matrix[r][i] for r in range(len(labels)))
        precision = tp / predicted if predicted else 0.0
        recall = tp / support if support else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        out[label] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": support,
        }
    return out


def macro_f1(stats: dict[str, dict[str, float]]) -> float:
    """Mean F1 over classes with non-zero support."""
    present = [s["f1"] for s in stats.values() if s["support"] > 0]
    return round(sum(present) / len(present), 4) if present else 0.0


def _field_metrics(truth: pd.Series, pred: pd.Series, labels: Sequence[str]) -> dict[str, Any]:
    matrix = confusion_matrix(list(truth), list(pred), labels)
    correct = int((truth.values == pred.values).sum())
    stats = per_class(matrix, labels)
    return {
        "accuracy": round(correct / len(truth), 4) if len(truth) else 0.0,
        "correct": correct,
        "incorrect": len(truth) - correct,
        "per_class": stats,
        "macro_f1": macro_f1(stats),
        "confusion_matrix": {"rows_are_truth": True, "labels": list(labels), "matrix": matrix},
    }


def _parse_ids(raw: str) -> set[str]:
    text = (raw or "").strip()
    if not text or text == "none":
        return set()
    return {part.strip() for part in text.split(";") if part.strip()}


def _evidence_overlap(merged: pd.DataFrame) -> dict[str, Any]:
    exact = any_overlap = both_none = pred_none = truth_none = 0
    jaccards: list[float] = []
    precisions: list[float] = []
    recalls: list[float] = []
    for cited, truth_raw in zip(merged["evidence_cited"], merged["evidence_message_ids"]):
        pred = set(cited)
        gold = _parse_ids(truth_raw)
        if not pred and not gold:
            both_none += 1
            exact += 1
            jaccards.append(1.0)
            precisions.append(1.0)
            recalls.append(1.0)
            continue
        if not pred and gold:
            pred_none += 1
        if pred and not gold:
            truth_none += 1
        inter = pred & gold
        union = pred | gold
        if pred == gold:
            exact += 1
        if inter:
            any_overlap += 1
        jaccards.append(len(inter) / len(union) if union else 1.0)
        precisions.append(len(inter) / len(pred) if pred else 0.0)
        recalls.append(len(inter) / len(gold) if gold else 0.0)
    n = max(len(merged), 1)
    return {
        "rows_compared": len(merged),
        "exact_set_match": exact,
        "any_overlap": any_overlap,
        "mean_jaccard": round(sum(jaccards) / n, 4),
        "mean_precision": round(sum(precisions) / n, 4),
        "mean_recall": round(sum(recalls) / n, 4),
        "both_none": both_none,
        "predicted_none_truth_cited": pred_none,
        "predicted_cited_truth_none": truth_none,
    }


def _calibration(confidence: pd.Series, correct: pd.Series) -> dict[str, Any]:
    buckets: list[dict[str, Any]] = []
    ece = 0.0
    n = len(confidence)
    for lower, upper in zip(CALIBRATION_BUCKET_EDGES[:-1], CALIBRATION_BUCKET_EDGES[1:]):
        in_bucket = (
            (confidence >= lower) & (confidence < upper)
            if upper < 1.0
            else (confidence >= lower) & (confidence <= upper)
        )
        count = int(in_bucket.sum())
        if count == 0:
            buckets.append(
                {"lower": lower, "upper": upper, "n": 0, "mean_confidence": None, "accuracy": None, "gap": None}
            )
            continue
        mean_conf = float(confidence[in_bucket].mean())
        acc = float(correct[in_bucket].mean())
        gap = abs(acc - mean_conf)
        ece += (count / n) * gap if n else 0.0
        buckets.append(
            {
                "lower": lower,
                "upper": upper,
                "n": count,
                "mean_confidence": round(mean_conf, 4),
                "accuracy": round(acc, 4),
                "gap": round(gap, 4),
            }
        )
    brier = float(((confidence - correct.astype(float)) ** 2).mean()) if n else 0.0
    return {
        "indicative_only": True,
        "bucket_edges": list(CALIBRATION_BUCKET_EDGES),
        "buckets": buckets,
        "expected_calibration_error": round(ece, 4),
        "brier_score": round(brier, 4),
        "brier_definition": (
            "mean((confidence - is_action_correct)^2), one-vs-rest on the predicted action"
        ),
    }


def compute_labeled(eval_rows: pd.DataFrame, truth: pd.DataFrame) -> dict[str, Any]:
    """All label-dependent metrics from eval_rows.jsonl joined to eval_truth.csv."""
    if eval_rows.empty or truth.empty:
        return {"n_rows": 0, "status": "no labeled rows"}

    merged = eval_rows.merge(truth, on="message_id", how="inner", suffixes=("", "_truth"))
    n = len(merged)
    one_row_pp = round(100.0 / n, 2) if n else 0.0

    action = _field_metrics(merged["action_truth"], merged["action"], ACTIONS)
    mtype = _field_metrics(merged["message_type_truth"], merged["message_type"], MESSAGE_TYPES)

    both = (merged["action"] == merged["action_truth"]) & (
        merged["message_type"] == merged["message_type_truth"]
    )
    correct_action = (merged["action"] == merged["action_truth"]).astype(int)

    return {
        "n_rows": n,
        "noise_floor": {
            "one_row_pp": one_row_pp,
            "reportable_threshold_rows": REPORTABLE_THRESHOLD_ROWS,
            "reportable_threshold_pp": round(one_row_pp * REPORTABLE_THRESHOLD_ROWS, 2),
            "note": (
                f"n={n}. A single row flip moves any rate by {one_row_pp}pp. Changes below "
                f"{REPORTABLE_THRESHOLD_ROWS} rows are not described as improvements."
            ),
        },
        "action": action,
        "message_type": mtype,
        "joint_accuracy": {
            "both_correct": int(both.sum()),
            "accuracy": round(float(both.mean()), 4),
        },
        "evidence_overlap": _evidence_overlap(merged),
        "calibration": _calibration(pd.to_numeric(merged["confidence"]), correct_action),
    }
