"""Cache-first perception. The object the pipeline actually holds.

A routing run reads this cache and, by default, cannot write it: constructed
without a transport, a miss raises ``PerceptionCacheMissError`` naming the
message rather than quietly becoming a paid pass. ``warm-perception`` is the one
command that supplies a transport.

The stored payload keeps the model's body **verbatim** alongside the validated
object. Perception is re-derived from that body on every read, so a change to
the parser can be re-run offline against what the model actually said — which is
the difference between fixing a parse bug for free and re-warming 140 rows to
find out whether the fix worked.
"""

from __future__ import annotations

from typing import Any, Mapping, Protocol

from cache import store
from config import RouterConfig
from errors import BudgetExhaustedError, PerceptionCacheMissError, PerceptionSchemaError
from logging_setup import StructuredLogger
from perception.parse import to_perception
from perception.prompt import build_prompt
from schemas import (
    CacheOutcome,
    CallTelemetry,
    LogEvent,
    MessageContext,
    Perception,
    PromptBundle,
    RawModelResponse,
    ThreatEvent,
)


class PerceptionTransportProtocol(Protocol):
    """One call. Tests inject a stub and touch no network."""

    def perceive(self, bundle: PromptBundle) -> RawModelResponse: ...


def _telemetry(
    outcome: CacheOutcome,
    *,
    retries: int,
    tokens_in: int,
    tokens_out: int,
    prompt_events: tuple[ThreatEvent, ...] = (),
) -> CallTelemetry:
    return CallTelemetry(
        prompt_threat_events=prompt_events,
        # The media outcome is supplied by route_one from its own extract call.
        cache_media=CacheOutcome.NOT_APPLICABLE,
        cache_perception=outcome,
        schema_retry_count=retries,
        tokens_input=tokens_in,
        tokens_output=tokens_out,
        wall_clock_ms=0.0,
    )


class CachedPerceptionClient:
    """Implements the pipeline's ``PerceptionClient`` protocol."""

    def __init__(
        self,
        cfg: RouterConfig,
        *,
        nonce: str,
        transport: PerceptionTransportProtocol | None = None,
        log: StructuredLogger | None = None,
        created_at_utc: str = "",
    ) -> None:
        self._cfg = cfg
        self._nonce = nonce
        self._transport = transport
        self._log = log
        self._created_at = created_at_utc
        """Supplied by the caller so this module never reads the clock and every
        entry written by one warm pass carries the same stamp."""
        self.tokens_input_total = 0
        self.tokens_output_total = 0
        self.calls_made = 0

    # ---- keying -----------------------------------------------------------

    def prepare(self, context: MessageContext) -> tuple[PromptBundle, str]:
        """Build the prompt for one message and the key it addresses.

        Public because both the offline preflight and ``warm-perception`` need
        the key without wanting the call, and because a key derived anywhere
        other than from the bundle that would actually be sent is a key that can
        drift away from what it claims to address.
        """
        bundle = build_prompt(context, nonce=self._nonce, limits=self._cfg.limits)
        key = store.perception_key(
            prompt_template_version=bundle.template_version,
            model_id=self._cfg.model_id,
            model_params=self._cfg.perception.model_params(),
            response_schema_sha256=bundle.response_schema_sha256,
            system_text=bundle.system_text,
            user_text=bundle.user_text,
            nonce=bundle.nonce,
            cfg=self._cfg.cache,
        )
        return bundle, key

    def is_cached(self, context: MessageContext) -> bool:
        _bundle, key = self.prepare(context)
        return store.read(store.PERCEPTION, key, self._cfg.cache).hit

    # ---- the protocol -----------------------------------------------------

    def perceive(self, context: MessageContext) -> tuple[Perception, CallTelemetry]:
        """Return this message's perception, from cache or from one call.

        Raises:
            PerceptionCacheMissError: nothing cached and no transport supplied.
            PerceptionSchemaError: every attempt produced an invalid body.
            BudgetExhaustedError: the run's token ceiling was reached.
        """
        bundle, key = self.prepare(context)
        read = store.read(store.PERCEPTION, key, self._cfg.cache, self._log)

        if read.hit and read.value is not None:
            payload = read.value
            raw = RawModelResponse(text=str(payload.get("raw_response") or ""))
            perception = to_perception(raw)
            return perception, _telemetry(
                CacheOutcome.HIT,
                retries=int(payload.get("retry_count") or 0),
                # Tokens are what THIS run spent. A hit spent none; the cost of
                # producing the entry is recorded inside the entry itself.
                tokens_in=0,
                tokens_out=0,
                # Recomputed from the bundle, not read from the entry: the row
                # must carry the same warnings whether it was cached or called.
                prompt_events=bundle.threat_events,
            )

        if self._transport is None:
            if self._log is not None:
                self._log.warning(
                    LogEvent.PERCEPTION_NOT_CACHED.value,
                    path="perception.cached.perceive",
                    message_id=context.message.message_id,
                    detail={"cache_outcome": read.outcome.value},
                    action_taken="row_failed_no_call_made",
                )
            raise PerceptionCacheMissError(context.message.message_id)

        perception, raw, retries, reasons = self._call(bundle, context)
        self._write(key, bundle, perception, raw, retries, reasons)
        return perception, _telemetry(
            read.outcome,
            retries=retries,
            tokens_in=raw.tokens_input,
            tokens_out=raw.tokens_output,
            prompt_events=bundle.threat_events,
        )

    # ---- the paid path ----------------------------------------------------

    def _check_budget(self) -> None:
        limits = self._cfg.limits
        if self.tokens_input_total > limits.MAX_RUN_INPUT_TOKENS:
            raise BudgetExhaustedError(
                f"input tokens {self.tokens_input_total} exceed "
                f"MAX_RUN_INPUT_TOKENS={limits.MAX_RUN_INPUT_TOKENS}"
            )
        if self.tokens_output_total > limits.MAX_RUN_OUTPUT_TOKENS:
            raise BudgetExhaustedError(
                f"output tokens {self.tokens_output_total} exceed "
                f"MAX_RUN_OUTPUT_TOKENS={limits.MAX_RUN_OUTPUT_TOKENS}"
            )

    def _call(
        self, bundle: PromptBundle, context: MessageContext
    ) -> tuple[Perception, RawModelResponse, int, list[str]]:
        """Call, validate, retry a rejected body. Never repairs one."""
        assert self._transport is not None
        attempts = max(self._cfg.limits.MODEL_MAX_ATTEMPTS, 1)
        reasons: list[str] = []
        last: PerceptionSchemaError | None = None

        for attempt in range(attempts):
            self._check_budget()
            raw = self._transport.perceive(bundle)
            self.calls_made += 1
            self.tokens_input_total += raw.tokens_input
            self.tokens_output_total += raw.tokens_output
            try:
                return to_perception(raw), raw, attempt, reasons
            except PerceptionSchemaError as exc:
                last = exc
                reasons.append(str(exc))
                if self._log is not None:
                    self._log.warning(
                        ThreatEvent.MODEL_RESPONSE_SCHEMA_REJECTED.value,
                        path="perception.cached._call",
                        message_id=context.message.message_id,
                        detail={"attempt": attempt + 1, "of": attempts},
                        action_taken="retried" if attempt + 1 < attempts else "row_failed",
                    )

        assert last is not None
        raise last

    def _write(
        self,
        key: str,
        bundle: PromptBundle,
        perception: Perception,
        raw: RawModelResponse,
        retries: int,
        reasons: list[str],
    ) -> None:
        material: Mapping[str, Any] = store.perception_key_material(
            prompt_template_version=bundle.template_version,
            model_id=self._cfg.model_id,
            model_params=self._cfg.perception.model_params(),
            response_schema_sha256=bundle.response_schema_sha256,
            system_text=bundle.system_text,
            user_text=bundle.user_text,
            nonce=bundle.nonce,
            cfg=self._cfg.cache,
        )
        payload: dict[str, Any] = {
            "perception": perception.model_dump(mode="json"),
            "raw_response": raw.text,
            "retry_count": retries,
            "retry_reasons": reasons,
            "tokens": {"input": raw.tokens_input, "output": raw.tokens_output},
            "model_id": self._cfg.model_id,
            "prompt_template_version": bundle.template_version,
            "finish_reason": raw.stop_reason,
            "threat_events": [event.value for event in bundle.threat_events],
        }
        store.write(
            store.PERCEPTION,
            key,
            payload,
            material,
            self._cfg.cache,
            created_at_utc=self._created_at,
        )
