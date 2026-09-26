#!/usr/bin/env python3
"""Live capability probe for the Sarvam provider.

Makes one minimal call per capability and writes docs/PROVIDER_CAPABILITIES.md
with a verdict and the raw response shape for each. This exists so the resolver
design rests on measured behaviour rather than on an assumption about tool
calling, JSON mode, determinism, or token accounting.

Every probe is independently guarded: one failure never aborts the rest. The
credential is read through bow.settings and is never printed, never written to
the report, and redacted out of any response body that echoes it back.

Usage:
    python scripts/probe_sarvam.py [--skip-doc-ai] [--image PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

_SUBMISSION_ROOT = Path(__file__).resolve().parent.parent
if str(_SUBMISSION_ROOT) not in sys.path:
    sys.path.insert(0, str(_SUBMISSION_ROOT))

from bow import paths  # noqa: E402
from bow.settings import ConfigError, load_settings  # noqa: E402

AUTH_HEADER = "api-subscription-key"
PRICING_URLS = [
    "https://docs.sarvam.ai/api-reference-docs/pricing",
    "https://www.sarvam.ai/pricing",
    "https://dashboard.sarvam.ai/pricing",
]
MAX_BODY_CHARS = 1400
# Completion budget used by the capability probes. Must sit above the reliable
# floor probe 6 measures, or a 200 with empty content is misread as a failure
# of the capability under test.
SAFE_MAX_TOKENS = 2048


class RateLimiter:
    """Token bucket. Document AI is capped at 10 requests/minute on all plans."""

    def __init__(self, requests_per_minute: int, burst: int) -> None:
        self._interval = 60.0 / float(requests_per_minute)
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._updated = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(
                    self._capacity,
                    self._tokens + (now - self._updated) / self._interval,
                )
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                time.sleep((1.0 - self._tokens) * self._interval)


@dataclass
class ProbeResult:
    number: int
    name: str
    verdict: str = "FAIL"
    detail: str = ""
    shape: str = ""
    elapsed_s: float = 0.0
    notes: list[str] = field(default_factory=list)


class Probe:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.provider = self.settings.models.provider
        self._secret = self.settings.api_key.get_secret_value()
        self.base = self.provider.base_url.rstrip("/")
        self.client = httpx.Client(
            timeout=httpx.Timeout(180.0),
            headers={AUTH_HEADER: self._secret, "Content-Type": "application/json"},
        )
        self.doc_limiter = RateLimiter(
            self.settings.models.rate_limits["doc_ai"].requests_per_minute,
            self.settings.models.rate_limits["doc_ai"].burst,
        )
        self.results: list[ProbeResult] = []

    # -- helpers ----------------------------------------------------------
    def redact(self, text: str) -> str:
        return text.replace(self._secret, "<redacted>") if self._secret else text

    def trim(self, text: str) -> str:
        text = self.redact(text)
        return text if len(text) <= MAX_BODY_CHARS else text[:MAX_BODY_CHARS] + " …[truncated]"

    def post(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        return self.client.post(f"{self.base}{path}", json=payload)

    @staticmethod
    def shape_of(obj: Any, depth: int = 0) -> str:
        """Describe a JSON value's structure without echoing its content."""
        if depth > 3:
            return "…"
        if isinstance(obj, dict):
            inner = ", ".join(
                f"{k}: {Probe.shape_of(v, depth + 1)}" for k, v in list(obj.items())[:12]
            )
            return "{" + inner + "}"
        if isinstance(obj, list):
            head = Probe.shape_of(obj[0], depth + 1) if obj else "?"
            return f"[{len(obj)} × {head}]"
        return type(obj).__name__

    def record(self, result: ProbeResult) -> ProbeResult:
        self.results.append(result)
        flag = {"PASS": "\u2713", "PARTIAL": "~", "FAIL": "\u2717"}.get(result.verdict, "?")
        print(f"  {flag} probe {result.number}: {result.verdict} — {result.detail}")
        return result

    def chat(self, path: str, payload: dict[str, Any]) -> tuple[httpx.Response, float]:
        start = time.monotonic()
        response = self.post(path, payload)
        return response, time.monotonic() - start

    # -- probes 1 & 2 -----------------------------------------------------
    def probe_chat_endpoint(self, number: int, path: str) -> ProbeResult:
        result = ProbeResult(number, f"sarvam-105b on {path}, plain completion, temperature 0")
        # Sized above the floor probe 6 measures. A smaller budget is consumed
        # entirely by reasoning tokens and returns a 200 with empty content,
        # which would make this probe silently measure the wrong thing.
        payload = {
            "model": "sarvam-105b",
            "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
            "temperature": 0,
            "top_p": 1,
            "max_tokens": SAFE_MAX_TOKENS,
            "reasoning_effort": "low",
        }
        try:
            response, result.elapsed_s = self.chat(path, payload)
            if response.status_code != 200:
                result.detail = f"HTTP {response.status_code}"
                result.shape = self.trim(response.text)
                return self.record(result)
            body = response.json()
            result.shape = self.shape_of(body)
            choice = body.get("choices", [{}])[0]
            content = (choice.get("message") or {}).get("content")
            usage = body.get("usage") or {}
            result.verdict = "PASS" if content else "PARTIAL"
            result.detail = (
                f"HTTP 200, content {'present' if content else 'EMPTY'}, "
                f"usage={json.dumps(usage)}"
            )
            if not content:
                result.notes.append(
                    "200 with empty content — reasoning tokens consumed the budget."
                )
        except Exception as exc:  # noqa: BLE001
            result.detail = f"{type(exc).__name__}: {self.redact(str(exc))}"
        return self.record(result)

    # -- probe 3 ----------------------------------------------------------
    def probe_tools(self, number: int) -> ProbeResult:
        result = ProbeResult(number, "tool calling with a trivial two-tool schema")
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "pick_left",
                    "description": "Choose the left option.",
                    "parameters": {
                        "type": "object",
                        "properties": {"why": {"type": "string"}},
                        "required": ["why"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "pick_right",
                    "description": "Choose the right option.",
                    "parameters": {
                        "type": "object",
                        "properties": {"why": {"type": "string"}},
                        "required": ["why"],
                    },
                },
            },
        ]
        payload_base = {
            "model": "sarvam-105b",
            "messages": [
                {"role": "user", "content": "Choose the right option. Use a tool."}
            ],
            "temperature": 0,
            "top_p": 1,
            "max_tokens": 1024,
            "reasoning_effort": "low",
            "tools": tools,
            "tool_choice": "required",
        }
        observed: list[str] = []
        emitted_on: list[str] = []
        for path in (self.provider.chat_v1_path, self.provider.chat_v2_path):
            try:
                response, elapsed = self.chat(path, dict(payload_base))
                result.elapsed_s += elapsed
                if response.status_code != 200:
                    observed.append(f"{path}: HTTP {response.status_code} {self.trim(response.text)[:200]}")
                    continue
                body = response.json()
                message = (body.get("choices") or [{}])[0].get("message") or {}
                calls = message.get("tool_calls")
                finish = (body.get("choices") or [{}])[0].get("finish_reason")
                if calls:
                    emitted_on.append(path)
                    observed.append(
                        f"{path}: tool_calls present, finish_reason={finish}, "
                        f"shape={self.shape_of(calls)}"
                    )
                else:
                    observed.append(
                        f"{path}: HTTP 200 but NO tool_calls, finish_reason={finish}, "
                        f"message keys={sorted(message)}"
                    )
            except Exception as exc:  # noqa: BLE001
                observed.append(f"{path}: {type(exc).__name__}: {self.redact(str(exc))}")
        result.shape = "\n".join(observed)
        if emitted_on:
            result.verdict = "PASS"
            result.detail = f"tool call emitted on {', '.join(emitted_on)}"
        else:
            result.detail = "no tool call emitted on either endpoint"
        return self.record(result)

    # -- probe 4 ----------------------------------------------------------
    def probe_json_mode(self, number: int) -> ProbeResult:
        result = ProbeResult(number, "response_format json_object")
        observed: list[str] = []
        supported: list[str] = []
        for path in (self.provider.chat_v1_path, self.provider.chat_v2_path):
            payload = {
                "model": "sarvam-105b",
                "messages": [
                    {"role": "user", "content": 'Return JSON: {"ok": true}'}
                ],
                "temperature": 0,
                "top_p": 1,
                "max_tokens": SAFE_MAX_TOKENS,
                "reasoning_effort": "low",
                "response_format": {"type": "json_object"},
            }
            try:
                response, elapsed = self.chat(path, payload)
                result.elapsed_s += elapsed
                if response.status_code != 200:
                    observed.append(
                        f"{path}: HTTP {response.status_code} {self.trim(response.text)[:220]}"
                    )
                    continue
                body = response.json()
                content = ((body.get("choices") or [{}])[0].get("message") or {}).get("content")
                parsed = None
                if content:
                    try:
                        parsed = json.loads(content)
                    except json.JSONDecodeError:
                        parsed = None
                observed.append(
                    f"{path}: HTTP 200, content parses as JSON: {parsed is not None}"
                )
                if parsed is not None:
                    supported.append(path)
            except Exception as exc:  # noqa: BLE001
                observed.append(f"{path}: {type(exc).__name__}: {self.redact(str(exc))}")
        result.shape = "\n".join(observed)
        result.verdict = "PASS" if supported else "FAIL"
        result.detail = (
            f"accepted and produced valid JSON on {', '.join(supported)}"
            if supported
            else "not usable on either endpoint"
        )
        return self.record(result)

    # -- probe 5 ----------------------------------------------------------
    def probe_determinism(self, number: int) -> ProbeResult:
        result = ProbeResult(number, "determinism at temperature 0, and seed support")
        prompt = "List exactly three prime numbers, comma separated, nothing else."
        outputs: list[str] = []
        try:
            for _ in range(3):
                payload = {
                    "model": "sarvam-105b",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                    "top_p": 1,
                    "max_tokens": SAFE_MAX_TOKENS,
                    "reasoning_effort": "low",
                }
                response, elapsed = self.chat(self.provider.chat_v1_path, payload)
                result.elapsed_s += elapsed
                if response.status_code != 200:
                    outputs.append(f"<HTTP {response.status_code}>")
                    continue
                body = response.json()
                outputs.append(
                    ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
                )
            identical = len(set(outputs)) == 1 and outputs[0] != ""
            # Does the API accept a seed parameter?
            seed_payload = {
                "model": "sarvam-105b",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "top_p": 1,
                "max_tokens": SAFE_MAX_TOKENS,
                "reasoning_effort": "low",
                "seed": 7,
            }
            seeded: list[str] = []
            seed_status = 0
            for _ in range(3):
                seed_response, elapsed = self.chat(self.provider.chat_v1_path, seed_payload)
                result.elapsed_s += elapsed
                seed_status = seed_response.status_code
                if seed_status != 200:
                    seeded.append(f"<HTTP {seed_status}>")
                    continue
                body = seed_response.json()
                seeded.append(
                    ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
                )
            seed_ok = seed_status == 200
            seed_identical = len(set(seeded)) == 1 and seeded[0] not in ("", None)
            result.verdict = "PASS" if (identical or seed_identical) else "FAIL"
            result.detail = (
                f"temperature 0 alone, 3 samples byte-identical: {identical}; "
                f"seed accepted: {seed_ok}; "
                f"temperature 0 + fixed seed, 3 samples byte-identical: {seed_identical}"
            )
            result.shape = (
                f"unseeded: {len(set(outputs))} distinct of 3\n"
                + "\n".join(f"  [{i}] len={len(o)} {self.trim(o)[:120]!r}" for i, o in enumerate(outputs))
                + f"\nseeded (seed=7): {len(set(seeded))} distinct of 3\n"
                + "\n".join(f"  [{i}] len={len(o)} {self.trim(o)[:120]!r}" for i, o in enumerate(seeded))
            )
            result.notes.append(
                "Reproducibility of the final run depends on this. If neither column is "
                "byte-identical, the deterministic engine still reproduces exactly; only "
                "the free-text explanation and the perception layer vary, which is why "
                "both are cached by content hash."
            )
        except Exception as exc:  # noqa: BLE001
            result.detail = f"{type(exc).__name__}: {self.redact(str(exc))}"
        return self.record(result)

    # -- probe 6 ----------------------------------------------------------
    def probe_reasoning_floor(self, number: int) -> ProbeResult:
        result = ProbeResult(
            number,
            "reasoning_effort low: token consumption and a RELIABLE max_tokens floor",
        )
        rows: list[str] = []
        trials = 3
        floors: dict[str, int | None] = {}
        try:
            for path in (self.provider.chat_v1_path, self.provider.chat_v2_path):
                rows.append(f"  {path}  ({trials} trials per level)")
                floor: int | None = None
                for max_tokens in (64, 256, 512, 1024, 2048):
                    visible = 0
                    completions: list[int] = []
                    reasoning_seen: list[Any] = []
                    for _ in range(trials):
                        payload = {
                            "model": "sarvam-105b",
                            "messages": [
                                {
                                    "role": "user",
                                    "content": "What is 2+2? Answer with the number only.",
                                }
                            ],
                            "temperature": 0,
                            "top_p": 1,
                            "max_tokens": max_tokens,
                            "reasoning_effort": "low",
                        }
                        response, elapsed = self.chat(path, payload)
                        result.elapsed_s += elapsed
                        if response.status_code != 200:
                            continue
                        body = response.json()
                        choice = (body.get("choices") or [{}])[0]
                        content = (choice.get("message") or {}).get("content") or ""
                        usage = body.get("usage") or {}
                        completions.append(usage.get("completion_tokens") or 0)
                        reasoning_seen.append(
                            usage.get("reasoning_tokens")
                            or (usage.get("completion_tokens_details") or {}).get(
                                "reasoning_tokens"
                            )
                        )
                        if content.strip():
                            visible += 1
                    rows.append(
                        f"    max_tokens={max_tokens:<5} visible {visible}/{trials}  "
                        f"completion_tokens={completions}  reasoning_tokens={reasoning_seen}"
                    )
                    if visible == trials and floor is None:
                        floor = max_tokens
                floors[path] = floor
            v1_floor = floors.get(self.provider.chat_v1_path)
            v2_floor = floors.get(self.provider.chat_v2_path)
            result.verdict = "PASS" if (v1_floor or v2_floor) else "FAIL"
            result.detail = (
                f"lowest max_tokens visible on ALL {trials} trials — "
                f"v1: {v1_floor}, v2: {v2_floor}"
            )
            result.shape = "\n".join(rows)
            result.notes.append(
                "A level that is visible on some trials but not all is NOT a safe floor; "
                "reasoning length varies per call. Role max_tokens in models.yaml must sit "
                "above the measured floor with headroom."
            )
        except Exception as exc:  # noqa: BLE001
            result.detail = f"{type(exc).__name__}: {self.redact(str(exc))}"
            result.shape = "\n".join(rows)
        return self.record(result)

    # -- probe 7 ----------------------------------------------------------
    def probe_max_tokens_ceiling(self, number: int) -> ProbeResult:
        result = ProbeResult(number, "effective max_tokens ceiling on this plan")
        rows: list[str] = []
        accepted: list[int] = []
        rejected: list[int] = []
        try:
            for candidate in (4096, 8192, 16384, 32768, 131072):
                payload = {
                    "model": "sarvam-105b",
                    "messages": [{"role": "user", "content": "Say ok."}],
                    "temperature": 0,
                    "top_p": 1,
                    "max_tokens": candidate,
                    "reasoning_effort": "low",
                }
                response, elapsed = self.chat(self.provider.chat_v1_path, payload)
                result.elapsed_s += elapsed
                ok = response.status_code == 200
                (accepted if ok else rejected).append(candidate)
                note = "" if ok else f" {self.trim(response.text)[:160]}"
                rows.append(f"  max_tokens={candidate:<7} HTTP {response.status_code}{note}")
            result.verdict = "PASS" if accepted else "FAIL"
            ceiling = max(accepted) if accepted else None
            configured = self.provider.max_tokens_ceiling
            result.detail = (
                f"highest accepted: {ceiling}; lowest rejected: "
                f"{min(rejected) if rejected else 'none'}; config says {configured}"
            )
            if ceiling and ceiling != configured:
                result.notes.append(
                    f"config `provider.max_tokens_ceiling` is {configured}; "
                    f"measured acceptance goes to {ceiling}. Update config."
                )
            result.shape = "\n".join(rows)
        except Exception as exc:  # noqa: BLE001
            result.detail = f"{type(exc).__name__}: {self.redact(str(exc))}"
        return self.record(result)

    # -- probe 8 ----------------------------------------------------------
    def probe_doc_ai(self, number: int, image: Path) -> ProbeResult:
        result = ProbeResult(number, "doc_ai.digitise and doc_ai.extract end to end through polling")
        rows: list[str] = []
        try:
            from sarvamai import SarvamAI

            client = SarvamAI(api_subscription_key=self._secret)
            digitise_role = self.settings.role("vision_digitise")
            extract_role = self.settings.role("vision_extract")

            # Observation-shaped: it transcribes candidate labelled figures.
            # It does not ask the model which figure is the answer.
            # The API rejects any field without a non-empty description
            # (400 SCHEMA_INVALID), so every property carries one.
            schema = json.dumps(
                {
                    "type": "object",
                    "properties": {
                        "total_label": {
                            "type": "string",
                            "description": "The label printed beside the final total.",
                        },
                        "total_value": {
                            "type": "string",
                            "description": "The figure printed beside that label, verbatim.",
                        },
                        "currency_marker": {
                            "type": "string",
                            "description": "Any currency symbol or code shown on the document.",
                        },
                    },
                }
            )

            outcomes: list[str] = []
            for label, role, kwargs in (
                (
                    "digitise",
                    digitise_role,
                    {
                        "language": digitise_role.language,
                        "output_format": digitise_role.output_format,
                    },
                ),
                (
                    "extract",
                    extract_role,
                    {
                        "language": extract_role.language,
                        "output_format": extract_role.output_format,
                        "schema": schema,
                    },
                ),
            ):
                self.doc_limiter.acquire()
                t0 = time.monotonic()
                with image.open("rb") as handle:
                    method = getattr(client.doc_ai, label)
                    job = method(file=[handle], **kwargs)
                submitted = time.monotonic() - t0
                rows.append(
                    f"  {label}: job_id={job.job_id[:8]}… initial status={job.status} "
                    f"(submit {submitted:.2f}s)"
                )
                terminal = self.settings.models.doc_ai_job_states
                deadline = time.monotonic() + (role.poll_timeout_seconds or 300.0)
                status = job.status
                polls = 0
                while time.monotonic() < deadline:
                    self.doc_limiter.acquire()
                    polls += 1
                    state = client.doc_ai.get_status(job.job_id)
                    status = state.status
                    if status in (*terminal.success, *terminal.partial, *terminal.failure):
                        break
                    time.sleep(role.poll_interval_seconds or 3.0)
                total = time.monotonic() - t0
                rows.append(
                    f"    terminal status={status} after {polls} poll(s), {total:.1f}s total"
                )
                if status in terminal.success or status in terminal.partial:
                    try:
                        self.doc_limiter.acquire()
                        payload = client.doc_ai.get_results(job.job_id)
                        rows.append(f"    get_results type={getattr(payload, 'type', '?')}")
                        rows.append(f"    result shape={self.shape_of(json.loads(payload.json()))}")
                    except Exception as exc:  # noqa: BLE001
                        rows.append(f"    get_results failed: {type(exc).__name__}: {self.redact(str(exc))}")
                    try:
                        self.doc_limiter.acquire()
                        link = client.doc_ai.get_download_url(job.job_id)
                        rows.append(f"    get_download_url ok, fields={sorted(json.loads(link.json()))}")
                    except Exception as exc:  # noqa: BLE001
                        rows.append(f"    get_download_url failed: {type(exc).__name__}")
                outcomes.append(f"{label}={status}")

            succeeded = all(
                o.split("=", 1)[1] in self.settings.models.doc_ai_job_states.success
                for o in outcomes
            )
            partial = any(
                o.split("=", 1)[1] in self.settings.models.doc_ai_job_states.partial
                for o in outcomes
            )
            result.verdict = "PASS" if succeeded else ("PARTIAL" if partial else "FAIL")
            result.detail = ", ".join(outcomes) or "no job reached a terminal state"
            result.shape = "\n".join(rows)
        except Exception as exc:  # noqa: BLE001
            result.detail = f"{type(exc).__name__}: {self.redact(str(exc))}"
            result.shape = "\n".join(rows)
        return self.record(result)

    # -- probe 9 ----------------------------------------------------------
    def probe_language_detection(self, number: int) -> ProbeResult:
        result = ProbeResult(number, "language detection on an English and a non-English string")
        rows: list[str] = []
        try:
            from sarvamai import SarvamAI

            client = SarvamAI(api_subscription_key=self._secret)
            samples = {
                "english": "Your payroll details have changed this month.",
                "non_english": "Rincian penggajian Anda telah berubah bulan ini.",
            }
            detected: list[str] = []
            for name, text in samples.items():
                t0 = time.monotonic()
                response = client.text.identify_language(input=text)
                result.elapsed_s += time.monotonic() - t0
                rows.append(
                    f"  {name}: language_code={response.language_code!r} "
                    f"script_code={response.script_code!r}"
                )
                detected.append(str(response.language_code))
            result.verdict = "PASS" if all(detected) else "PARTIAL"
            result.detail = f"detected {detected}"
            result.shape = "\n".join(rows)
            result.notes.append(
                "Routing compares the detected code against languages.text_in_set / "
                "languages.document_in_set in models.yaml. A null or unrecognised "
                "code routes out-of-set."
            )
        except Exception as exc:  # noqa: BLE001
            result.detail = f"{type(exc).__name__}: {self.redact(str(exc))}"
            result.shape = "\n".join(rows)
        return self.record(result)

    # -- probe 10 ---------------------------------------------------------
    def list_models(self, path: str) -> list[str]:
        """Enumerate model ids the provider actually serves on an endpoint."""
        try:
            url = f"{self.base}{path.rsplit('/', 2)[0]}/models"
            response = self.client.get(url)
            if response.status_code != 200:
                return []
            payload = response.json()
            items = payload.get("data", payload) if isinstance(payload, dict) else payload
            return [
                m.get("id") for m in items if isinstance(m, dict) and m.get("id")
            ] if isinstance(items, list) else []
        except Exception:  # noqa: BLE001
            return []

    def probe_out_of_set(self, number: int, image: Path) -> ProbeResult:
        result = ProbeResult(
            number, "out-of-set text and vision models on /v2/chat/completions"
        )
        rows: list[str] = []
        working: list[str] = []
        text_model = self.settings.role("message_extract_oos").model
        vision_model = self.settings.role("vision_fallback").model

        served = self.list_models(self.provider.chat_v2_path)
        rows.append(f"  /v2 model catalogue: {served or '<unavailable>'}")
        for configured, label in ((text_model, "message_extract_oos"), (vision_model, "vision_fallback")):
            if served and configured not in served:
                rows.append(
                    f"  !! config {label}.model={configured!r} is NOT in the catalogue"
                )
                result.notes.append(
                    f"models.yaml role {label} names {configured!r}, which the provider "
                    f"does not serve. Correct it to one of: {', '.join(served)}."
                )

        # Text route: whatever config selected, plus every other catalogue entry.
        candidates = [text_model] + [m for m in served if m != text_model]
        for model in candidates:
            if not model:
                continue
            payload = {
                "model": model,
                "messages": [{"role": "user", "content": "Reply with the single word: ok"}],
                "temperature": 0,
                "top_p": 1,
                "max_tokens": 1024,
            }
            try:
                response, elapsed = self.chat(self.provider.chat_v2_path, payload)
                result.elapsed_s += elapsed
                if response.status_code == 200:
                    body = response.json()
                    content = (
                        (body.get("choices") or [{}])[0].get("message") or {}
                    ).get("content") or ""
                    working.append(model)
                    rows.append(
                        f"  text {model}: HTTP 200, content "
                        f"{'present' if content.strip() else 'EMPTY'}"
                    )
                else:
                    rows.append(
                        f"  text {model}: HTTP {response.status_code} "
                        f"{self.trim(response.text)[:160]}"
                    )
            except Exception as exc:  # noqa: BLE001
                rows.append(f"  text {model}: {type(exc).__name__}: {self.redact(str(exc))}")

        # Vision route: one image as a data URL, against every catalogue entry
        # so the report says which of them actually accept image input.
        if image.is_file():
            import base64

            encoded = base64.b64encode(image.read_bytes()).decode("ascii")
            vision_candidates = [vision_model] + [m for m in served if m != vision_model]
            for model in vision_candidates:
                if not model:
                    continue
                payload = {
                    "model": model,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "Transcribe any total shown."},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/png;base64,{encoded}"
                                    },
                                },
                            ],
                        }
                    ],
                    "temperature": 0,
                    "top_p": 1,
                    "max_tokens": 1024,
                }
                try:
                    response, elapsed = self.chat(self.provider.chat_v2_path, payload)
                    result.elapsed_s += elapsed
                    if response.status_code == 200:
                        working.append(f"{model} (vision)")
                        rows.append(f"  vision {model}: HTTP 200 — accepts image input")
                    else:
                        rows.append(
                            f"  vision {model}: HTTP {response.status_code} "
                            f"{self.trim(response.text)[:160]}"
                        )
                except Exception as exc:  # noqa: BLE001
                    rows.append(
                        f"  vision {model}: {type(exc).__name__}: {self.redact(str(exc))}"
                    )

        result.verdict = "PASS" if working else "FAIL"
        result.detail = (
            f"reachable: {', '.join(working)}" if working else "no out-of-set model reachable"
        )
        result.shape = "\n".join(rows)
        return self.record(result)

    # -- pricing ----------------------------------------------------------
    @staticmethod
    def extract_rates(page: str) -> list[str]:
        """Pull rate-shaped rows out of a fetched pricing page.

        Pattern-driven, not keyed to any particular model name, so a new model
        or a renamed row still shows up. Rows that survive as pipe-delimited
        table rows are preferred because they carry their own label.
        """
        import html as _html
        import re as _re

        text = page
        for _ in range(3):
            unescaped = (
                text.replace("\\u003c", "<")
                .replace("\\u003e", ">")
                .replace('\\"', '"')
                .replace("\\n", "\n")
                .replace("\\/", "/")
            )
            if unescaped == text:
                break
            text = unescaped
        text = _html.unescape(text)
        text = _re.sub(r"<[^>]+>", " ", text)

        currency = r"(?:\u20b9|Rs\.?|INR|\$|USD)"
        unit = r"(?:per\s+1M\s+tokens|per\s+page|/\s*page|per\s+10K\s+characters|/\s*10K\s+characters)"
        row = _re.compile(
            rf"([^\n]{{0,170}}?{currency}\s?[\d,]+(?:\.\d+)?[^\n]{{0,130}}?{unit})",
            _re.IGNORECASE,
        )

        tabular: list[str] = []
        loose: list[str] = []
        for match in row.finditer(text):
            cleaned = _re.sub(r"\s+", " ", match.group(1)).strip(" |*-")
            cleaned = _re.sub(r"^[^A-Za-z\u20b9$]*", "", cleaned)
            if not cleaned:
                continue
            bucket = tabular if cleaned.count("|") >= 2 else loose
            if cleaned not in bucket:
                bucket.append(cleaned)
        return tabular or loose

    def fetch_pricing(self) -> tuple[str, list[str], int]:
        for url in PRICING_URLS:
            try:
                response = httpx.get(url, timeout=40.0, follow_redirects=True)
                if response.status_code == 200 and response.text.strip():
                    return url, self.extract_rates(response.text), len(response.text)
            except Exception:  # noqa: BLE001
                continue
        return "", [], 0

    def close(self) -> None:
        self.client.close()


def render_report(probe: Probe, pricing_url: str, rates: list[str], page_chars: int) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    passes = sum(1 for r in probe.results if r.verdict == "PASS")
    partials = sum(1 for r in probe.results if r.verdict == "PARTIAL")
    fails = sum(1 for r in probe.results if r.verdict == "FAIL")

    lines = [
        "# Provider capabilities — Sarvam",
        "",
        f"Generated by `scripts/probe_sarvam.py` at {now}.",
        "",
        "Every row below is a live call against the configured provider, not a",
        "documentation claim. The credential is never printed and is redacted out",
        "of any echoed response body.",
        "",
        f"**Verdict: {passes} pass, {partials} partial, {fails} fail "
        f"(of {len(probe.results)} probes).**",
        "",
        f"- base URL: `{probe.provider.base_url}`",
        f"- auth header: `{AUTH_HEADER}`",
        f"- configured plan: `{probe.provider.plan}`, "
        f"`max_tokens_ceiling` {probe.provider.max_tokens_ceiling}",
        "",
        "## Summary",
        "",
        "| # | capability | verdict | detail |",
        "|---|---|---|---|",
    ]
    for r in probe.results:
        detail = r.detail.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {r.number} | {r.name} | **{r.verdict}** | {detail} |")

    lines += ["", "## Raw response shapes", ""]
    for r in probe.results:
        lines += [
            f"### Probe {r.number} — {r.name}",
            "",
            f"- verdict: **{r.verdict}**",
            f"- detail: {r.detail}",
            f"- wall time: {r.elapsed_s:.2f}s",
            "",
        ]
        if r.shape:
            lines += ["```text", r.shape, "```", ""]
        for note in r.notes:
            lines.append(f"> {note}")
        if r.notes:
            lines.append("")

    lines += [
        "## Pricing",
        "",
    ]
    if pricing_url and rates:
        lines += [
            f"Fetched live from `{pricing_url}` ({page_chars:,} characters).",
            "Rows below are extracted by rate-shape pattern, not by model name, so a",
            "renamed or newly added row still appears.",
            "",
            "| rate row (verbatim) |",
            "|---|",
        ]
        lines += [f"| {r.replace('|', ' · ')} |" for r in rates]
        lines += [
            "",
            "`evaluation/usage_report.md` must cite these figures for the final",
            "full-dataset run. Note that reasoning tokens are billed as output tokens,",
            "so the reasoning_effort setting on each role has a direct cost consequence.",
        ]
    elif pricing_url:
        lines += [
            f"Fetched `{pricing_url}` ({page_chars:,} characters) but no rate row could be",
            "extracted. Transcribe the rates from the dashboard by hand; do not guess.",
        ]
    else:
        lines += [
            "**Pricing could not be fetched automatically.** None of the candidate",
            "URLs returned a usable page:",
            "",
        ]
        lines += [f"- `{u}`" for u in PRICING_URLS]
        lines += [
            "",
            "`evaluation/usage_report.md` requires real per-page and per-token rates.",
            "Fill them in from the dashboard before the final run; do not guess.",
        ]
    lines += ["", "---", "", "*Generated report. Re-run the script to refresh.*", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Sarvam provider capabilities.")
    parser.add_argument("--skip-doc-ai", action="store_true", help="skip probe 8")
    parser.add_argument("--image", type=Path, default=None, help="image for probes 8 and 10")
    args = parser.parse_args()

    try:
        probe = Probe()
    except ConfigError as exc:
        print(f"probe_sarvam: {exc}", file=sys.stderr)
        return 2

    image = args.image
    if image is None:
        candidates = sorted(paths.IMAGES_DIR.glob("*.png"))
        image = candidates[0] if candidates else Path("/nonexistent")

    print(f"probing {probe.provider.base_url} …")
    print(f"  image for document probes: {image if image.is_file() else '<none found>'}\n")

    probe.probe_chat_endpoint(1, probe.provider.chat_v1_path)
    probe.probe_chat_endpoint(2, probe.provider.chat_v2_path)
    probe.probe_tools(3)
    probe.probe_json_mode(4)
    probe.probe_determinism(5)
    probe.probe_reasoning_floor(6)
    probe.probe_max_tokens_ceiling(7)
    if args.skip_doc_ai or not image.is_file():
        probe.record(
            ProbeResult(8, "doc_ai.digitise and doc_ai.extract end to end", "FAIL", "skipped")
        )
    else:
        probe.probe_doc_ai(8, image)
    probe.probe_language_detection(9)
    probe.probe_out_of_set(10, image)

    pricing_url, rates, page_chars = probe.fetch_pricing()
    print(f"\n  pricing: {len(rates)} rate row(s) from {pricing_url or '<unavailable>'}")

    report = render_report(probe, pricing_url, rates, page_chars)
    target = paths.DOCS_DIR / "PROVIDER_CAPABILITIES.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report, encoding="utf-8")
    probe.close()
    print(f"\nwrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
