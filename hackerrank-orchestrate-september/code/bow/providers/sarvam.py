"""The only place in the package that makes a network call.

Owns the rate limiter, the job poller, retries, and the usage meter. Everything
else that needs a model goes through here.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from bow.providers.response import classify_response

__all__ = ["DocAiJob", "ProviderError", "RateLimiter", "SarvamClient", "UsageMeter"]

AUTH_HEADER = "api-subscription-key"


class ProviderError(RuntimeError):
    """A provider call failed in a way retrying will not fix."""


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
                    self._capacity, self._tokens + (now - self._updated) / self._interval
                )
                self._updated = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                time.sleep((1.0 - self._tokens) * self._interval)


@dataclass
class UsageMeter:
    """Pages, tokens, calls and wall time per role, for the cost report."""

    calls: dict[str, int] = field(default_factory=dict)
    prompt_tokens: dict[str, int] = field(default_factory=dict)
    completion_tokens: dict[str, int] = field(default_factory=dict)
    pages: dict[str, int] = field(default_factory=dict)
    #: Input size for services billed by character rather than by token. The
    #: cost report needs one input measure per role, whatever the unit is.
    characters: dict[str, int] = field(default_factory=dict)
    seconds: dict[str, float] = field(default_factory=dict)

    def record(self, role: str, *, seconds: float, usage: dict[str, Any] | None = None,
               pages: int = 0, characters: int = 0) -> None:
        self.calls[role] = self.calls.get(role, 0) + 1
        self.seconds[role] = round(self.seconds.get(role, 0.0) + seconds, 3)
        if pages:
            self.pages[role] = self.pages.get(role, 0) + pages
        if characters:
            self.characters[role] = self.characters.get(role, 0) + characters
        if usage:
            self.prompt_tokens[role] = self.prompt_tokens.get(role, 0) + (
                usage.get("prompt_tokens") or 0
            )
            self.completion_tokens[role] = self.completion_tokens.get(role, 0) + (
                usage.get("completion_tokens") or 0
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "pages": self.pages,
            "characters": self.characters,
            "seconds": self.seconds,
        }


@dataclass
class DocAiJob:
    job_id: str
    status: str
    payload: dict[str, Any] | None = None
    polls: int = 0
    seconds: float = 0.0


class SarvamClient:
    """Thin wrapper over the vendor SDK and the OpenAI-compatible endpoints."""

    def __init__(self, settings) -> None:
        from sarvamai import SarvamAI

        self._settings = settings
        self._secret = settings.api_key.get_secret_value()
        self._sdk = SarvamAI(api_subscription_key=self._secret)
        self._provider = settings.models.provider
        # Derived from configuration, not assumed: the client must outlive the
        # most patient role it will ever carry.
        longest = max(role.timeout_seconds for role in settings.models.roles.values())
        self._http = httpx.Client(
            timeout=httpx.Timeout(longest),
            headers={AUTH_HEADER: self._secret, "Content-Type": "application/json"},
        )
        self._limiters = {
            name: RateLimiter(limit.requests_per_minute, limit.burst)
            for name, limit in settings.models.rate_limits.items()
        }
        self.usage = UsageMeter()

    def close(self) -> None:
        self._http.close()

    # -- document AI ----------------------------------------------------------

    def _await_job(self, job_id: str, role) -> DocAiJob:
        states = self._settings.models.doc_ai_job_states
        terminal = (*states.success, *states.partial, *states.failure)
        started = time.monotonic()
        # Guaranteed present: the config validator rejects a doc_ai role without it.
        deadline = started + role.poll_timeout_seconds
        polls, status = 0, "pending"
        while time.monotonic() < deadline:
            self._limiters[role.rate_limit].acquire()
            polls += 1
            status = self._sdk.doc_ai.get_status(job_id).status
            if status in terminal:
                break
            time.sleep(role.poll_interval_seconds)
        return DocAiJob(job_id=job_id, status=status, polls=polls,
                        seconds=time.monotonic() - started)

    def digitise(self, image: Path, role_name: str = "vision_digitise") -> DocAiJob:
        """One read of one document. Returns the job with its results attached."""
        import json

        role = self._settings.role(role_name)
        self._limiters[role.rate_limit].acquire()
        started = time.monotonic()
        with image.open("rb") as handle:
            started_job = self._sdk.doc_ai.digitise(
                file=[handle], language=role.language, output_format=role.output_format
            )
        job = self._await_job(started_job.job_id, role)
        states = self._settings.models.doc_ai_job_states
        if job.status in (*states.success, *states.partial):
            self._limiters[role.rate_limit].acquire()
            job.payload = json.loads(self._sdk.doc_ai.get_results(job.job_id).json())
        pages = ((job.payload or {}).get("usage") or {}).get("pages_processed") or 1
        self.usage.record(role_name, seconds=time.monotonic() - started, pages=pages)
        return job

    def extract(self, image: Path, schema: str, role_name: str = "vision_extract") -> DocAiJob:
        """The second, schema-constrained read. Called only on reconciliation failure."""
        import json

        role = self._settings.role(role_name)
        self._limiters[role.rate_limit].acquire()
        started = time.monotonic()
        with image.open("rb") as handle:
            started_job = self._sdk.doc_ai.extract(
                file=[handle], schema=schema, language=role.language,
                output_format=role.output_format,
            )
        job = self._await_job(started_job.job_id, role)
        states = self._settings.models.doc_ai_job_states
        if job.status in (*states.success, *states.partial):
            self._limiters[role.rate_limit].acquire()
            job.payload = json.loads(self._sdk.doc_ai.get_results(job.job_id).json())
        pages = ((job.payload or {}).get("usage") or {}).get("pages_processed") or 1
        self.usage.record(role_name, seconds=time.monotonic() - started, pages=pages)
        return job

    # -- chat -----------------------------------------------------------------

    def detect_language(self, text: str) -> str | None:
        role = self._settings.role("lang_detect")
        self._limiters[role.rate_limit].acquire()
        started = time.monotonic()
        try:
            response = self._sdk.text.identify_language(input=text)
            return response.language_code
        except Exception:  # noqa: BLE001 — detection is advisory; failure routes out-of-set
            return None
        finally:
            self.usage.record(
                "lang_detect",
                seconds=time.monotonic() - started,
                characters=len(text),
            )

    def complete(
        self, role_name: str, system: str, user: str, *, attempts: int = 2
    ) -> tuple[str | None, str]:
        """One completion. Returns ``(content, detail)``; content is ``None`` on failure.

        Empty content is retried while attempts remain and is then a hard flag.
        It is never returned as a valid empty result.
        """
        role = self._settings.role(role_name)
        policy = self._settings.models.retry_policies[role.retry_policy]
        url = f"{self._provider.base_url.rstrip('/')}{role.endpoint}"
        payload: dict[str, Any] = {
            "model": role.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": role.temperature,
            "top_p": role.top_p,
            "max_tokens": role.max_tokens,
            "seed": role.seed,
            "response_format": {"type": "json_object"},
        }
        if role.reasoning_effort is not None:
            payload["reasoning_effort"] = role.reasoning_effort

        last = "not attempted"
        for attempt in range(attempts):
            self._limiters[role.rate_limit].acquire()
            started = time.monotonic()
            try:
                response = self._http.post(url, json=payload)
                body = response.json() if response.content else {}
            except Exception as exc:  # noqa: BLE001
                self.usage.record(role_name, seconds=time.monotonic() - started)
                last = f"{type(exc).__name__}"
                continue
            self.usage.record(
                role_name, seconds=time.monotonic() - started, usage=body.get("usage")
            )
            verdict = classify_response(
                body, response.status_code,
                retryable_status=frozenset(policy.retry_on_status),
                attempts_remaining=attempts - attempt - 1,
            )
            if verdict.is_usable:
                return (body["choices"][0]["message"]["content"], verdict.detail)
            last = verdict.detail
            if not verdict.retryable:
                break
        return None, last
