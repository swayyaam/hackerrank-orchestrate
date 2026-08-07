"""Compose the modules for one message, and for a whole run."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Protocol, Sequence

from compose.reason import render_reason
from config import RouterConfig
from emit import record as record_module
from emit.writer import write_output
from errors import RouterError
from features.assemble import build_features
from features.integrity import threat_events
from ingest.context import build_context
from ingest.loader import Dataset
from logging_setup import StructuredLogger
from media.extract import extract
from policy.engine import decide
from scoring.confidence import score_confidence
from scoring.evidence import select_evidence
from schemas import (
    CallTelemetry,
    ContextSummary,
    FiringStatus,
    IncomingMessage,
    LogEvent,
    MediaKind,
    MediaOutcome,
    MessageContext,
    OutputRow,
    Perception,
    RoutingResult,
    RunFailure,
    RunResult,
)


class PerceptionClient(Protocol):
    """Single-method protocol, so tests inject a stub and touch no network."""

    def perceive(self, context: MessageContext) -> tuple[Perception, CallTelemetry]: ...


def _media_paths(dataset: Dataset, message: IncomingMessage) -> dict[str, str]:
    return dict(dataset.images if message.media_kind is MediaKind.IMAGE else dataset.voice_notes)


def _summarise(context: MessageContext) -> ContextSummary:
    media = context.media
    if media is None:
        outcome, cause = MediaOutcome.NONE, None
    elif media.readable:
        outcome, cause = MediaOutcome.OK, None
    else:
        outcome, cause = MediaOutcome.FAILED, media.failure_reason
    return ContextSummary(
        conversation_type=context.message.conversation_type,
        media_id=context.message.media_id,
        media_kind=context.message.media_kind,
        media_outcome=outcome,
        media_failure_cause=cause,
        has_text=context.message.has_text,
    )


def route_one(
    dataset: Dataset,
    message_id: str,
    client: PerceptionClient,
    cfg: RouterConfig,
    log: StructuredLogger | None = None,
) -> RoutingResult:
    """Run every stage for one message. The only function that composes modules.

    Returns the OutputRow plus the Perception, FeatureVector, EvidenceSelection
    (including the shortlist considered), and CallTelemetry -- everything the
    per-row record needs, so emit projects and never recomputes.
    """
    started = time.perf_counter()
    message = dataset.message_by_id[message_id]

    media, media_cache = extract(
        message, cfg, media_paths=_media_paths(dataset, message), log=log
    )
    context = build_context(dataset, message_id, media)

    perception, telemetry = client.perceive(context)
    features = build_features(context, cfg.policy)
    decision = decide(perception, features, cfg.policy)
    evidence = select_evidence(context, decision, cfg.evidence)
    confidence = score_confidence(decision, features, perception, evidence, cfg.confidence)
    # Warnings are derived before composition because the reason must name them:
    # a row that carries an integrity observation and does not say so is an
    # explanation that omits the thing the reader most needs.
    warnings = threat_events(
        perception, features, context.media, telemetry, telemetry.prompt_threat_events
    )
    reason = render_reason(decision, features, evidence, warnings, cfg.reason)

    output = OutputRow(
        message_id=message_id,
        action=decision.action,
        message_type=decision.message_type,
        reason=reason,
        confidence=confidence,
        evidence_message_ids=evidence.message_ids,
        override_codes=tuple(
            f.code for f in decision.firings if f.status is FiringStatus.APPLIED
        ),
        firings=decision.firings,
        threat_events=warnings,
    )

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return RoutingResult(
        output=output,
        perception=perception,
        features=features,
        evidence=evidence,
        telemetry=CallTelemetry(
            cache_media=media_cache,
            cache_perception=telemetry.cache_perception,
            schema_retry_count=telemetry.schema_retry_count,
            tokens_input=telemetry.tokens_input,
            tokens_output=telemetry.tokens_output,
            wall_clock_ms=round(elapsed_ms, cfg.record.WALL_CLOCK_DECIMALS),
        ),
        context_summary=_summarise(context),
    )


def _route_set(
    dataset: Dataset,
    message_ids: Sequence[str],
    client: PerceptionClient,
    cfg: RouterConfig,
    log: StructuredLogger,
) -> tuple[list[RoutingResult], list[RunFailure]]:
    results: list[RoutingResult] = []
    failures: list[RunFailure] = []
    for message_id in message_ids:
        try:
            results.append(route_one(dataset, message_id, client, cfg, log))
        except RouterError as exc:
            failures.append(RunFailure(message_id=message_id, error_type=type(exc).__name__))
            log.error(
                LogEvent.ROW_FAILED.value,
                path="pipeline._route_set",
                message_id=message_id,
                detail={"error_type": type(exc).__name__},
                action_taken="row_failed_run_continues",
            )
    return results, failures


def run(
    dataset: Dataset,
    out_path: Path,
    client: PerceptionClient,
    cfg: RouterConfig,
    log: StructuredLogger,
    *,
    mode: str = "offline",
) -> RunResult:
    """Load once, route the target rows AND the eval rows, write every artifact."""
    snapshot = record_module.config_snapshot(cfg)
    cfg_hash = record_module.config_hash(snapshot)
    run_id = record_module.build_run_id(cfg_hash, cfg.record)
    run_dir = cfg.record.RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    log.info(
        LogEvent.RUN_STARTED.value,
        path="pipeline.run",
        detail={"run_id": run_id, "mode": mode, "targets": len(dataset.targets)},
    )

    target_ids = [m.message_id for m in dataset.targets]
    eval_ids = [m.message_id for m in dataset.eval_messages]
    results, failures = _route_set(dataset, target_ids, client, cfg, log)
    eval_results, eval_failures = _route_set(dataset, eval_ids, client, cfg, log)
    all_failures = [*failures, *eval_failures]

    record_module.write_records(results, run_dir / "rows.jsonl", cfg.record)
    record_module.write_records(eval_results, run_dir / "eval_rows.jsonl", cfg.record)
    record_module.write_eval_truth(dataset.eval_truth, run_dir / "eval_truth.csv")

    written = 0
    if not failures:
        outputs = [r.output for r in results]
        written = write_output(outputs, out_path, target_ids, cfg.evidence)
        write_output(outputs, run_dir / "output.csv", target_ids, cfg.evidence)

    record_module.write_manifest(
        run_dir,
        run_id=run_id,
        cfg=cfg,
        snapshot=snapshot,
        cfg_hash=cfg_hash,
        dataset_fingerprint=dataset.fingerprint,
        mode=mode,
        rows_expected=len(target_ids),
        rows_written=written,
        eval_rows_expected=len(eval_ids),
        eval_rows_written=len(eval_results),
        failures=all_failures,
    )
    log.info(
        LogEvent.RUN_COMPLETED.value,
        path="pipeline.run",
        detail={"run_id": run_id, "rows_written": written, "failures": len(all_failures)},
    )
    return RunResult(
        run_id=run_id,
        run_dir=run_dir,
        rows_written=written,
        rows_expected=len(target_ids),
        eval_rows_written=len(eval_results),
        eval_rows_expected=len(eval_ids),
        failures=tuple(all_failures),
    )
