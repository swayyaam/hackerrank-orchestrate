"""The end-to-end path: dataset in, output.csv out.

Deliberately thin. Every stage is the crudest thing that works, and no stage is
missing — a pipeline with one primitive stage produces a scoreable file, and a
pipeline with one missing stage produces nothing.

Not yet wired in: vision, messages, the resolver. A blank-amount event raises an
unresolved flag and the affected request **still produces a row**, marked in the
run report rather than dropped.
"""

from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

from bow.contracts import (
    BalanceAsOfBasis,
    CashDateBasis,
    EvidenceLedger,
    HorizonEndBasis,
    PendingDuplicatePolicy,
    Request,
    UnresolvedItem,
)
from bow.data.loaders import load_dataset
from bow.data.normalize import normalize_events
from bow.data.recurrence import project
from bow.engine.capacity import amount_safe_to_pay, earliest_date_for_full_payment
from bow.engine.changes import (
    ChangePermission,
    apply_changes,
    candidate_changes,
    search_changes,
)
from bow.engine.forecast import build_forecast, variable_spend_flows, variable_spend_warnings
from bow.engine.plans import enumerate_plans
from bow.engine.policy import decide
from bow.engine.ranker import best_plan, supplied_option_count
from bow.explain import explain
from bow.format import COLUMNS, format_iso_date, format_minimal_amount
from bow.format.columns import format_payment_plan, format_spending_changes
from bow.perception.resolve import (
    apply_deltas,
    resolve_blank_amounts,
    resolve_messages,
)
from bow.providers.sarvam import SarvamClient
from bow.settings import load_decisions, load_settings, load_thresholds
from bow.validate import run_gates

__all__ = ["PipelineResult", "run_pipeline", "write_output"]


@dataclass
class _Stage:
    name: str
    seconds: float


@dataclass
class PipelineResult:
    rows: list[dict[str, str]]
    stages: list[_Stage] = field(default_factory=list)
    warnings: dict[str, int] = field(default_factory=dict)
    unresolved: list[UnresolvedItem] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    #: Per-role calls, tokens, pages, characters and wall time, structured. The
    #: cost report is generated from this, so it is a first-class part of the
    #: manifest rather than a flattened afterthought inside `warnings`.
    usage: dict[str, dict[str, float]] = field(default_factory=dict)

    def manifest(self) -> dict[str, Any]:
        return {
            "rows_written": len(self.rows),
            "stages": [{"stage": s.name, "seconds": round(s.seconds, 3)} for s in self.stages],
            "total_seconds": round(sum(s.seconds for s in self.stages), 3),
            "counts": self.counts,
            "usage": self.usage,
            "unresolved": [
                {
                    "reason": u.reason.value,
                    "source_type": u.source_type.value,
                    "source_id": u.source_id,
                    "field": u.field,
                    "detail": u.detail,
                }
                for u in self.unresolved
            ],
            "unresolved_total": len(self.unresolved),
            "warnings": self.warnings,
        }


def _decision_flags(decisions) -> dict[str, str]:
    return {entry.id: entry.default for entry in decisions.decisions}


def run_pipeline(
    dataset_dir: Path, *, targets: str = "requests", perception: bool = True
) -> tuple[PipelineResult, dict[str, Request]]:
    """Run every stage. ``targets`` selects ``requests`` or ``sample_requests``.

    ``perception`` wires in the vision and message layers. Turning it off runs
    the deterministic path alone, which is how the engine is tested without a
    single API call.
    """
    timings: list[_Stage] = []

    def stage(name: str, work):
        started = time.monotonic()
        value = work()
        timings.append(_Stage(name, time.monotonic() - started))
        return value

    loaded = stage("load", lambda: load_dataset(dataset_dir))
    dataset = loaded.dataset
    thresholds = load_thresholds()
    flags = _decision_flags(load_decisions())
    horizon = thresholds.forecast.horizon_days

    events = dataset.events
    perception_evidence = []
    perception_unresolved = []
    vision_paths: dict[str, str] = {}
    message_drops: dict[str, int] = {}
    message_stats = {"messages": 0, "deltas_kept": 0, "deltas_with_event": 0, "failed": 0}
    client = None

    if perception:
        from bow import paths as _paths

        settings = load_settings()
        client = SarvamClient(settings)

        def run_vision():
            nonlocal events
            events, outcome = resolve_blank_amounts(
                events, dataset.images, client,
                image_path=_paths.image_path,
                label_priority=thresholds.vision.label_priority,
                label_denylist=thresholds.vision.label_denylist,
                tolerance=thresholds.vision.reconciliation_tolerance,
                cache_dir=_paths.CACHE_DIR,
            )
            perception_evidence.extend(outcome.evidence)
            perception_unresolved.extend(outcome.unresolved)
            vision_paths.update(outcome.paths)

        stage("vision", run_vision)

        def run_messages():
            nonlocal events
            by_id = {e.event_id: e for e in events}
            by_user: dict[str, list] = {}
            for event in events:
                by_user.setdefault(event.user_id, []).append(event)
            wanted = {
                r.user_id
                for r in (
                    dataset.sample_requests
                    if targets == "sample_requests"
                    else dataset.requests
                )
            }
            relevant = [m for m in dataset.messages if m.user_id in wanted]
            outcomes, drops = resolve_messages(
                relevant, by_user, by_id, client,
                prompt_path=_paths.PROMPTS_DIR / "message_extract.v1.txt",
                in_set=settings.models.languages.text_in_set,
            )
            message_drops.update(drops)
            message_stats["messages"] = len(relevant)
            message_stats["failed"] = sum(1 for o in outcomes if o.failed)
            message_stats["deltas_kept"] = sum(len(o.kept) for o in outcomes)
            message_stats["deltas_with_event"] = sum(
                1 for o in outcomes for d in o.kept if d.target_event_id
            )
            events, evidence = apply_deltas(
                events, outcomes,
                max_quote_chars=thresholds.perception.max_evidence_quote_chars,
            )
            perception_evidence.extend(evidence)

        stage("messages", run_messages)

    normalized = stage(
        "normalize",
        lambda: normalize_events(
            events,
            profiles=dataset.profiles,
            rates=dataset.exchange_rates,
            images=dataset.images,
            basis=CashDateBasis(flags["Q16_cash_date_basis"]),
            pending_duplicate_policy=PendingDuplicatePolicy(
                flags["Q1_pending_duplicate_debit"]
            ),
        ),
    )

    profiles = {p.user_id: p for p in dataset.profiles}
    options = dataset.options_by_request()
    requests: Sequence[Request] = (
        dataset.sample_requests if targets == "sample_requests" else dataset.requests
    )
    by_user: dict[str, list] = {}
    for event in normalized.events:
        by_user.setdefault(event.user_id, []).append(event)

    blank_amount_users = {e.user_id: e.event_id for e in events if e.amount is None}

    rows: list[dict[str, str]] = []
    unresolved: list[UnresolvedItem] = []
    warnings: dict[str, int] = {w.name: w.count for w in normalized.report.warnings}
    counts = {
        "requests": len(requests),
        "events_kept": len(normalized.events),
        "events_dropped": len(normalized.drops.dropped),
        "conversions": normalized.conversion.converted,
        "conversions_deferred": len(normalized.conversion.deferred),
        "plans_considered": 0,
        "requests_with_no_safe_plan": 0,
        "requests_with_blank_amount_evidence": 0,
        "multiple_eligible_supplied_options": 0,
        "requests_with_change_candidates": 0,
        "requests_rescued_by_spending_changes": 0,
    }

    def decide_all() -> None:
        for request in requests:
            profile = profiles[request.user_id]
            events = by_user.get(request.user_id, [])

            projection = project(
                events, profile, request.request_date, horizon,
                thresholds=thresholds.recurrence,
                continue_income_series=(
                    flags["Q22_series_continuation"] == "continue_income_series"
                ),
                use_median_amount=(flags["Q20_projected_amount"] == "median_of_series"),
                income_continues_past_scheduled=(
                    flags["Q21_income_beyond_scheduled_row"] == "continue_inferred_cadence"
                ),
            )
            variable, rates = variable_spend_flows(
                projection.series, request.request_date, horizon,
                window_days=thresholds.forecast.variable_spend_window_days,
                min_occurrences_in_window=thresholds.forecast.min_occurrences_in_trailing_window,
                project_irregular=(
                    flags["Q26_project_irregular_series"] == "project_at_trailing_rate"
                ),
                project_single_occurrence=(
                    flags["Q27_project_single_occurrence_series"] == "project_at_trailing_rate"
                ),
                project_stale=(
                    flags["Q28_project_stale_series"] == "project_at_trailing_rate"
                ),
            )
            ledger = EvidenceLedger(request_id=request.request_id)
            forecast = build_forecast(
                ledger, profile, projection.events, request.request_date, horizon,
                balance_basis=BalanceAsOfBasis(flags["Q8_balance_as_of_semantics"]),
            )
            forecast = forecast.model_copy(
                update={"flows": tuple(sorted(forecast.flows + variable, key=lambda f: f.date))}
            )

            horizon_basis = HorizonEndBasis(flags["Q24_horizon_end_basis"])
            safe = amount_safe_to_pay(
                forecast, profile.minimum_balance_to_keep, request.requested_amount
            )
            earliest = earliest_date_for_full_payment(
                forecast, profile.minimum_balance_to_keep, request.requested_amount,
                horizon_basis=horizon_basis,
            )
            candidates = enumerate_plans(
                request, profile, options.get(request.request_id, ()), forecast,
                amount_safe=safe, earliest_full_payment=earliest,
                horizon_basis=horizon_basis,
            )
            counts["plans_considered"] += len(candidates)
            if supplied_option_count(candidates) > 1:
                counts["multiple_eligible_supplied_options"] += 1

            chosen = best_plan(candidates)
            changes: tuple[str, ...] = ()

            # Ranking rule 2 puts a plan needing no change ahead of one that
            # does, so changes are only searched when nothing is safe as-is.
            if chosen is None:
                pool = candidate_changes(
                    projection.series,
                    permitted=ChangePermission.from_profile(profile),
                    prefer_reduce=(flags["Q29_flexible_both_verb"] == "prefer_reduce"),
                )
                if pool:
                    counts["requests_with_change_candidates"] += 1

                    def safe_with(selected, _p=projection, _pr=profile, _rq=request,
                                  _v=variable, _lg=ledger, _hb=horizon_basis):
                        adjusted = apply_changes(_p.events, selected)
                        trial = build_forecast(
                            _lg, _pr, adjusted, _rq.request_date, horizon,
                            balance_basis=BalanceAsOfBasis(
                                flags["Q8_balance_as_of_semantics"]
                            ),
                        )
                        trial = trial.model_copy(
                            update={
                                "flows": tuple(
                                    sorted(trial.flows + _v, key=lambda f: f.date)
                                )
                            }
                        )
                        return bool(
                            enumerate_plans(
                                _rq, _pr, options.get(_rq.request_id, ()), trial,
                                amount_safe=amount_safe_to_pay(
                                    trial, _pr.minimum_balance_to_keep,
                                    _rq.requested_amount,
                                ),
                                earliest_full_payment=earliest_date_for_full_payment(
                                    trial, _pr.minimum_balance_to_keep,
                                    _rq.requested_amount, horizon_basis=_hb,
                                ),
                                horizon_basis=_hb,
                            )
                        )

                    found = search_changes(
                        pool, safe_with,
                        max_changes=thresholds.spending_changes.max_changes,
                    )
                    if found is not None:
                        counts["requests_rescued_by_spending_changes"] += 1
                        changes = found.rendered()
                        adjusted = apply_changes(projection.events, found.changes)
                        forecast = build_forecast(
                            ledger, profile, adjusted, request.request_date, horizon,
                            balance_basis=BalanceAsOfBasis(
                                flags["Q8_balance_as_of_semantics"]
                            ),
                        )
                        forecast = forecast.model_copy(
                            update={
                                "flows": tuple(
                                    sorted(forecast.flows + variable, key=lambda f: f.date)
                                )
                            }
                        )
                        changed_safe = amount_safe_to_pay(
                            forecast, profile.minimum_balance_to_keep,
                            request.requested_amount,
                        )
                        candidates = enumerate_plans(
                            request, profile, options.get(request.request_id, ()),
                            forecast, amount_safe=changed_safe,
                            earliest_full_payment=earliest_date_for_full_payment(
                                forecast, profile.minimum_balance_to_keep,
                                request.requested_amount, horizon_basis=horizon_basis,
                            ),
                            horizon_basis=horizon_basis,
                        )
                        chosen = best_plan(candidates)

            if chosen is None:
                counts["requests_with_no_safe_plan"] += 1
                changes = ()

            decision = decide(
                request, profile, chosen,
                amount_safe=safe, earliest_full_payment=earliest,
                spending_changes=changes,
            )

            # A blank-amount event is flagged, and the request still produces a
            # row. Dropping it would trade a wrong answer for no answer.
            blank_event = blank_amount_users.get(request.user_id)
            if blank_event is not None:
                counts["requests_with_blank_amount_evidence"] += 1
                unresolved.append(
                    UnresolvedItem(
                        reason="missing_amount",
                        source_type="event",
                        source_id=blank_event,
                        field="amount",
                        detail=(
                            f"{request.request_id} depends on an event with no amount; "
                            "the vision layer is not wired in yet"
                        ),
                    )
                )

            for name, count, _detail in variable_spend_warnings(
                forecast, rates,
                opening_balance=profile.current_available_balance,
                warn_fraction_of_opening=(
                    thresholds.forecast.variable_spend_warn_fraction_of_opening
                ),
                min_occurrences_in_window=(
                    thresholds.forecast.min_occurrences_in_trailing_window
                ),
            ):
                if name != "horizon_days":
                    warnings[name] = warnings.get(name, 0) + count

            rows.append(
                {
                    "request_id": decision.request_id,
                    "amount_safe_to_pay": format_minimal_amount(decision.amount_safe_to_pay),
                    "affordability_status": decision.affordability_status.value,
                    "recommended_payment_method": decision.recommended_payment_method.value,
                    "payment_plan": format_payment_plan(decision.payments),
                    "earliest_date_for_full_payment": format_iso_date(
                        decision.earliest_date_for_full_payment
                    ),
                    "spending_changes_needed": format_spending_changes(
                        decision.spending_changes
                    ),
                    "decision_explanation": explain(decision, request, profile),
                }
            )

    stage("decide", decide_all)
    unresolved.extend(perception_unresolved)
    counts.update(
        {
            "vision_reads": len(vision_paths),
            "vision_second_read": sum(
                1 for p in vision_paths.values() if p == "digitise_then_extract"
            ),
            "vision_unresolved": len(perception_unresolved),
            "messages_processed": message_stats["messages"],
            "message_deltas_kept": message_stats["deltas_kept"],
            "message_deltas_naming_an_event": message_stats["deltas_with_event"],
            "message_extractions_failed": message_stats["failed"],
            "perception_evidence": len(perception_evidence),
        }
    )
    for reason, count in message_drops.items():
        warnings[f"message_delta_dropped: {reason}"] = count
    usage: dict[str, dict[str, float]] = {}
    if client is not None:
        usage = {
            kind: dict(values)
            for kind, values in client.usage.as_dict().items()
            if values
        }
        client.close()
    result = PipelineResult(
        rows=rows, stages=timings, warnings=warnings, unresolved=unresolved,
        counts=counts, usage=usage,
    )
    return result, {r.request_id: r for r in requests}


def write_output(rows: list[dict[str, str]], path: Path) -> None:
    """Write the eight columns in the spec's order, with no index column."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(COLUMNS), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_manifest(result: PipelineResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.manifest(), indent=2) + "\n", encoding="utf-8")
