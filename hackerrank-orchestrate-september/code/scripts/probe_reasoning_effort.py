#!/usr/bin/env python3
"""Does `reasoning_effort` change what the message extractor produces?

`evaluation/usage_report.md` measures message extraction at roughly three
quarters of the run cost, almost all of it completion tokens. Reasoning tokens
bill as completion tokens, so the question is whether the reasoning budget is
buying anything on a schema-constrained extraction that returns a short typed
object.

This is an A/B, not a tuning knob. Both arms run the SAME message set, in the
same order, with the same prompt, the same pinned seed and the same temperature;
the only difference is the `reasoning_effort` field in the payload. Correctness
is the deciding criterion: the cheaper arm is adopted only if schema validity
and the deltas produced hold.

The message set, the prompt, the schema and the candidate-event block are all
imported from `probe_multilingual.py` rather than copied, so the two probes
cannot drift apart.

Usage:
    python scripts/probe_reasoning_effort.py --select-only   # detect + cache
    python scripts/probe_reasoning_effort.py --arm current
    python scripts/probe_reasoning_effort.py --arm low
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

_SUBMISSION_ROOT = Path(__file__).resolve().parent.parent
if str(_SUBMISSION_ROOT) not in sys.path:
    sys.path.insert(0, str(_SUBMISSION_ROOT))

from bow import paths  # noqa: E402
from bow.data.loaders import load_dataset  # noqa: E402
from bow.settings import load_settings  # noqa: E402

AUTH_HEADER = "api-subscription-key"
#: Sentinel for "send no reasoning_effort field at all", which is how the
#: current configuration expresses no reasoning: the API accepts only
#: low/medium/high, so absence is the only way to say none.
OMITTED = "current"


def _probe_multilingual():
    """Import the sibling probe so prompt, schema and set selection are shared."""
    path = Path(__file__).resolve().parent / "probe_multilingual.py"
    spec = importlib.util.spec_from_file_location("probe_multilingual", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["probe_multilingual"] = module
    spec.loader.exec_module(module)
    return module


def _detect_cache_path() -> Path:
    return paths.CACHE_DIR / "lang_detect" / "messages.json"


def select_messages(dataset, settings, probe, *, refresh: bool) -> list:
    """The non-English message set, detected once and cached on disk.

    Both arms must see the identical set, and detection is a billed call, so the
    verdicts are cached rather than re-detected per arm.
    """
    cache = _detect_cache_path()
    detected: dict[str, str] = {}
    if cache.is_file() and not refresh:
        detected = json.loads(cache.read_text(encoding="utf-8"))

    missing = [m for m in dataset.messages if m.message_id not in detected]
    if missing:
        from sarvamai import SarvamAI

        client = SarvamAI(api_subscription_key=settings.api_key.get_secret_value())
        limits = settings.models.rate_limits[settings.role("lang_detect").rate_limit]
        limiter = probe._rate_limiter(limits.requests_per_minute, limits.burst)
        print(f"detecting language on {len(missing)} message(s) ...", flush=True)
        for index, message in enumerate(missing, start=1):
            limiter.acquire()
            try:
                response = client.text.identify_language(input=message.message_text)
                detected[message.message_id] = str(response.language_code)
            except Exception as exc:  # noqa: BLE001
                detected[message.message_id] = f"<error: {type(exc).__name__}>"
            if index % 50 == 0:
                print(f"  {index}/{len(missing)}", flush=True)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(detected, indent=1), encoding="utf-8")

    counts = Counter(detected.values())
    english = {code for code in counts if code.startswith("en")}
    selected = [m for m in dataset.messages if detected[m.message_id] not in english]
    print(f"detector: {dict(counts)}", flush=True)
    print(f"selected {len(selected)} non-English message(s)", flush=True)
    return selected


def run_arm(arm: str, messages, dataset, settings, probe) -> dict[str, Any]:
    """One arm: identical payload except the reasoning_effort field."""
    role = settings.role("message_extract")
    provider = settings.models.provider
    limits = settings.models.rate_limits[role.rate_limit]
    limiter = probe._rate_limiter(limits.requests_per_minute, limits.burst)

    events_by_id = dataset.event_by_id()
    events_by_user: dict[str, list[Any]] = {}
    for event in dataset.events:
        events_by_user.setdefault(event.user_id, []).append(event)

    http = httpx.Client(
        timeout=httpx.Timeout(role.timeout_seconds),
        headers={
            AUTH_HEADER: settings.api_key.get_secret_value(),
            "Content-Type": "application/json",
        },
    )
    url = f"{provider.base_url.rstrip('/')}{role.endpoint}"

    records: list[dict[str, Any]] = []
    started_all = time.monotonic()
    print(
        f"[{arm}] {role.model} temp={role.temperature} seed={role.seed} "
        f"max_tokens={role.max_tokens} reasoning_effort="
        f"{'<omitted>' if arm == OMITTED else arm}",
        flush=True,
    )
    for index, message in enumerate(messages, start=1):
        candidates = probe.candidate_block(message, events_by_user, events_by_id)
        payload: dict[str, Any] = {
            "model": role.model,
            "messages": [
                {"role": "system", "content": probe.SYSTEM_PROMPT},
                {"role": "user", "content": probe.user_prompt(message, candidates)},
            ],
            "temperature": role.temperature,
            "top_p": role.top_p,
            "max_tokens": role.max_tokens,
            "seed": role.seed,
            "response_format": {"type": "json_object"},
        }
        if arm != OMITTED:
            payload["reasoning_effort"] = arm
        elif role.reasoning_effort is not None:
            payload["reasoning_effort"] = role.reasoning_effort

        record: dict[str, Any] = {"message_id": message.message_id}
        limiter.acquire()
        began = time.monotonic()
        try:
            response = http.post(url, json=payload)
            record["http"] = response.status_code
            if response.status_code != 200:
                record["failure"] = f"HTTP {response.status_code}"
            else:
                body = response.json()
                choice = (body.get("choices") or [{}])[0]
                content = (choice.get("message") or {}).get("content")
                record["usage"] = body.get("usage")
                record["finish_reason"] = choice.get("finish_reason")
                if not content or not content.strip():
                    record["failure"] = "empty content"
                else:
                    try:
                        extraction = probe.MessageExtraction.model_validate_json(content)
                        record["extraction"] = extraction.model_dump()
                        record["valid"] = True
                    except (ValidationError, ValueError) as exc:
                        record["failure"] = f"schema: {type(exc).__name__}"
        except Exception as exc:  # noqa: BLE001
            record["failure"] = f"{type(exc).__name__}: {exc}"
        record["elapsed_s"] = round(time.monotonic() - began, 2)
        records.append(record)
        if index % 5 == 0:
            print(f"  [{arm}] {index}/{len(messages)}", flush=True)
    http.close()

    valid = [r for r in records if r.get("valid")]
    deltas = [d for r in valid for d in r["extraction"]["deltas"]]
    resolvable = [
        d for d in deltas
        if d.get("target_event_id") and d["target_event_id"] in events_by_id
    ]
    completion = [
        (r.get("usage") or {}).get("completion_tokens", 0) for r in records
    ]
    return {
        "arm": arm,
        "reasoning_effort_sent": None if arm == OMITTED else arm,
        "messages": len(records),
        "schema_valid": len(valid),
        "schema_valid_rate": round(len(valid) / max(len(records), 1), 4),
        "failures": Counter(
            r["failure"].split(":")[0] for r in records if r.get("failure")
        ),
        "deltas_produced": len(deltas),
        "deltas_with_resolvable_target": len(resolvable),
        "deltas_claiming_new_fact": sum(
            1 for d in deltas if d.get("creates_new_fact")
        ),
        "effects": dict(Counter(d["effect"] for d in deltas)),
        "prompt_tokens": sum(
            (r.get("usage") or {}).get("prompt_tokens", 0) for r in records
        ),
        "completion_tokens": sum(completion),
        "completion_tokens_mean": round(sum(completion) / max(len(records), 1), 1),
        "completion_tokens_max": max(completion) if completion else 0,
        "finish_reasons": dict(
            Counter(str(r.get("finish_reason")) for r in records)
        ),
        "wall_seconds": round(time.monotonic() - started_all, 1),
        "per_message": [
            {
                "message_id": r["message_id"],
                "valid": bool(r.get("valid")),
                "deltas": len(r.get("extraction", {}).get("deltas", []))
                if r.get("valid") else None,
                "completion_tokens": (r.get("usage") or {}).get("completion_tokens"),
                "failure": r.get("failure"),
                # The deltas themselves, not just their count: two arms can
                # agree on how many they found and disagree on what they are,
                # and the guard-relevant question is which ones survive.
                "extracted": r.get("extraction", {}).get("deltas"),
            }
            for r in records
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", default=None, help=f"{OMITTED} | low | medium | high")
    parser.add_argument("--select-only", action="store_true")
    parser.add_argument("--refresh-detection", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    settings = load_settings()
    probe = _probe_multilingual()
    dataset = load_dataset(paths.require_dataset_dir()).dataset
    messages = select_messages(
        dataset, settings, probe, refresh=args.refresh_detection
    )
    if args.limit:
        messages = messages[: args.limit]
    if args.select_only:
        return 0
    if not args.arm:
        parser.error("--arm is required unless --select-only")

    result = run_arm(args.arm, messages, dataset, settings, probe)
    result["measured_at"] = datetime.now(timezone.utc).isoformat()
    result["model"] = settings.role("message_extract").model
    result["seed"] = settings.role("message_extract").seed

    destination = args.out or (
        paths.EVAL_DIR / f"reasoning_effort_{args.arm}.json"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(result, indent=1, default=str), encoding="utf-8"
    )
    summary = {k: v for k, v in result.items() if k != "per_message"}
    print(json.dumps(summary, indent=1, default=str), flush=True)
    print(f"wrote {destination}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
