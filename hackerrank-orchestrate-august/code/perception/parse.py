"""Validate a raw model reply into ``Perception``. Strict, never lenient.

The endpoint is asked to produce a constrained shape, but a constraint imposed
on the far end of a network call is not a guarantee on this end. Everything that
comes back is validated here against ``Perception``, whose ``extra="forbid"``
rejects a response carrying a field the model was told not to emit -- a
``confidence``, a ``reason``, an evidence id.

The rejection is deliberate rather than a convenience. Trimming an unexpected
field would mean the model had started answering a different question and the
system would carry on as though it had not. A response that does not match the
contract is not repaired; it is refused, and the caller retries or fails loudly.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

from errors import PerceptionSchemaError
from schemas import Perception, RawModelResponse


def _payload(raw: RawModelResponse) -> dict[str, Any]:
    text = raw.text.strip()
    if not text:
        raise PerceptionSchemaError("the model returned an empty body")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PerceptionSchemaError(f"the model body is not JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise PerceptionSchemaError(
            f"the model body is a {type(parsed).__name__}, not an object"
        )
    return parsed


def to_perception(raw: RawModelResponse) -> Perception:
    """Validate one reply into ``Perception``.

    Raises:
        PerceptionSchemaError: the body is not JSON, is not an object, omits a
            required field, or carries a field ``Perception`` forbids. Never a
            silent trim, and never a partially populated object.
    """
    payload = _payload(raw)

    # The schema asks for a plain string so the wire shape stays inside the
    # supported subset; "no attachment" arrives as "" and becomes None here.
    if payload.get("media_summary") == "":
        payload["media_summary"] = None

    try:
        return Perception.model_validate(payload)
    except ValidationError as exc:
        offenders = "; ".join(
            f"{'.'.join(str(p) for p in error['loc']) or '<root>'}: {error['type']}"
            for error in exc.errors()
        )
        raise PerceptionSchemaError(f"response does not satisfy Perception -- {offenders}") from exc
