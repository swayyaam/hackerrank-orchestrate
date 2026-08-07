"""Project RoutingResult into the per-row record; write the manifest and eval truth.

Pure projection. Every field is copied or mechanically derived from the
RoutingResult -- no counts, no rates, no totals. Aggregation is code/metrics/.
"""

from __future__ import annotations

import csv
import json
import subprocess
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Sequence

from config import RecordConfig, RouterConfig  # noqa: F401  RecordConfig used at class scope
from media import extract as media_extract  # version constant only
from perception import prompt  # version constant only
from schemas import (
    EngagementStance,
    EvalTruthRow,
    FiringStatus,
    IntegrityState,
    RoutingResult,
    RunFailure,
    SenderRelationshipClass,
)


def sender_relationship_class(result: RoutingResult) -> SenderRelationshipClass:
    """Derived at write time: the consistency scan groups on it."""
    features = result.features
    if not features.relationship_established:
        return SenderRelationshipClass.UNESTABLISHED
    if features.sender_stance is EngagementStance.RECEPTIVE:
        return SenderRelationshipClass.ESTABLISHED_RECEPTIVE
    if features.sender_stance is EngagementStance.DISMISSIVE:
        return SenderRelationshipClass.ESTABLISHED_DISMISSIVE
    return SenderRelationshipClass.ESTABLISHED_NEUTRAL


def integrity_state(result: RoutingResult) -> IntegrityState:
    """The row's risk posture, derived at write time."""
    from policy.rules import RULES_BY_CODE
    from schemas import RuleEffect

    applied = result.output.firings
    for firing in applied:
        if firing.status is FiringStatus.APPLIED:
            rule = RULES_BY_CODE[firing.code]
            if rule.effect is RuleEffect.HARD_OVERRIDE and rule.is_integrity_rule:
                return IntegrityState.FLAGGED
    if result.output.threat_events:
        return IntegrityState.WARNED
    return IntegrityState.CLEAN


def to_record(result: RoutingResult, cfg: RecordConfig) -> dict[str, Any]:
    """Project one RoutingResult into the per-row record. Computes nothing else."""
    from compose.reason import cites_evidence, names_decisive_factor, references_integrity
    from schemas import Decision

    output = result.output
    decision = Decision(
        action=output.action, message_type=output.message_type, firings=output.firings
    )
    applied = decision.applied

    return {
        "schema_version": cfg.SCHEMA_VERSION,
        "message_id": output.message_id,
        "action": output.action.value,
        "message_type": output.message_type.value,
        "reason": output.reason,
        "confidence": output.confidence,
        "evidence_cited": list(output.evidence_message_ids),
        "model_proposal": {
            "action": result.perception.proposed_action.value,
            "type_candidates": [t.value for t in result.perception.type_candidates],
        },
        "override_codes_fired": [
            f.code.value
            for f in output.firings
            if f.status
            in (
                FiringStatus.APPLIED,
                FiringStatus.SUPERSEDED,
                FiringStatus.OVERRIDDEN_BY_CAP,
                FiringStatus.FIRED_NOT_DETERMINING,
                FiringStatus.SUPPRESSED_BY_GUARD,
            )
        ],
        "override_code_applied": applied.code.value,
        "firings": [
            {
                "code": f.code.value,
                "effect": f.effect.value,
                "status": f.status.value,
                "would_set_action": f.would_set_action.value if f.would_set_action else None,
            }
            for f in output.firings
        ],
        "integrity_warnings": [e.value for e in output.threat_events],
        "evidence_shortlist": list(result.evidence.shortlist_considered),
        "evidence_tier": int(result.evidence.tier),
        "schema_retry_count": result.telemetry.schema_retry_count,
        "hard_parse_failure": False,
        "media_id": result.context_summary.media_id,
        "media_kind": result.context_summary.media_kind.value,
        "media_outcome": result.context_summary.media_outcome.value,
        "media_failure_cause": result.context_summary.media_failure_cause,
        "media_failed": result.features.media_failed,
        "media_unread": result.features.media_unread,
        "has_text": result.context_summary.has_text,
        "cache": {
            "media": result.telemetry.cache_media.value,
            "perception": result.telemetry.cache_perception.value,
        },
        "served_entirely_from_cache": (
            result.telemetry.cache_perception.value == "hit"
            and result.telemetry.cache_media.value in ("hit", "n/a")
        ),
        "tokens": {
            "input": result.telemetry.tokens_input,
            "output": result.telemetry.tokens_output,
        },
        "wall_clock_ms": result.telemetry.wall_clock_ms,
        "conversation_type": result.context_summary.conversation_type.value,
        "sender_relationship_class": sender_relationship_class(result).value,
        "proposed_message_type": result.perception.type_candidates[0].value,
        "integrity_state": integrity_state(result).value,
        "reason_char_length": len(output.reason),
        "reason_cites_evidence": cites_evidence(output.reason),
        "reason_names_decisive_factor": names_decisive_factor(output.reason),
        "reason_references_integrity": references_integrity(output.reason),
    }


def write_records(results: Sequence[RoutingResult], path: Path, cfg: RecordConfig) -> int:
    """Serialise one record per RoutingResult to the given .jsonl path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for result in results:
            handle.write(json.dumps(to_record(result, cfg), ensure_ascii=False, sort_keys=True) + "\n")
    return len(results)


def write_eval_truth(truth: Sequence[EvalTruthRow], path: Path) -> None:
    """Copy the sample label rows into the run directory.

    Labeled metrics read this, never dataset/, so a past run stays gradable even
    if the dataset changes.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            ["message_id", "action", "message_type", "reason", "confidence", "evidence_message_ids"]
        )
        for row in truth:
            writer.writerow(
                [
                    row.message_id,
                    row.action.value,
                    row.message_type.value,
                    row.reason,
                    row.confidence,
                    row.evidence_message_ids,
                ]
            )


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=RecordConfig.GIT_TIMEOUT_SECONDS, check=False
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def config_snapshot(cfg: RouterConfig) -> dict[str, Any]:
    """Full config values, so a diff can name which constant changed."""
    out: dict[str, Any] = {}
    for name in (
        "policy", "confidence", "evidence", "reason",
        "limits", "extraction", "perception", "record",
    ):
        section = getattr(cfg, name)
        if is_dataclass(section):
            out[type(section).__name__] = {
                key: (str(value) if isinstance(value, Path) else value)
                for key, value in asdict(section).items()
            }
    return out


def config_hash(snapshot: dict[str, Any]) -> str:
    import hashlib

    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def build_run_id(cfg_hash: str, cfg: RecordConfig) -> str:
    """<UTC compact timestamp>-<git short sha>-<config hash prefix>."""
    # Wall clock is permitted here: emit/record.py is one of the four stamping
    # modules exempted by ENGINEERING_CONVENTIONS section 7.
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    sha = _git("rev-parse", "--short", "HEAD") or "nogit"
    return f"{stamp}-{sha}-{cfg_hash[:cfg.RUN_ID_HASH_CHARS]}"


def write_manifest(
    run_dir: Path,
    *,
    run_id: str,
    cfg: RouterConfig,
    snapshot: dict[str, Any],
    cfg_hash: str,
    dataset_fingerprint: str,
    mode: str,
    rows_expected: int,
    rows_written: int,
    eval_rows_expected: int,
    eval_rows_written: int,
    failures: Sequence[RunFailure],
) -> None:
    """Write manifest.json: config hash, git commit, timestamp, mode, counts."""
    manifest = {
        "schema_version": cfg.record.SCHEMA_VERSION,
        "run_id": run_id,
        "created_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "git_commit": _git("rev-parse", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "config_hash": cfg_hash,
        "config_snapshot": snapshot,
        "prompt_template_version": prompt.PROMPT_TEMPLATE_VERSION,
        "media_extraction_version": media_extract.MEDIA_EXTRACTION_VERSION,
        "model_id": cfg.model_id,
        "dataset_fingerprint": dataset_fingerprint,
        "mode": mode,
        "rows_expected": rows_expected,
        "rows_written": rows_written,
        "eval_rows_expected": eval_rows_expected,
        "eval_rows_written": eval_rows_written,
        "failures": [{"message_id": f.message_id, "error_type": f.error_type} for f in failures],
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
