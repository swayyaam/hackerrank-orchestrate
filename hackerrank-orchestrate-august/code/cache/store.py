"""The only module that reads or writes the cache directory.

Two namespaces, distinct keys, distinct lifetimes (CACHING.md):

* **media** -- keyed by the sha256 of the file's bytes plus the extraction
  version and model id. Never invalidated: file content is immutable, and a
  changed extraction version simply produces a new key.
* **perception** -- keyed by everything sent to the model, nonce-normalised.
  Landed in Phase 2; the key builder lives here so both namespaces share one
  envelope, one integrity check, and one atomic-write path.

This module computes **no metrics**. It returns a ``CacheOutcome`` per lookup;
the pipeline puts that on the row record and ``metrics/`` derives hit rates post
hoc, which is what makes them computable for past runs.

A corrupt entry is a miss. It is logged once and never raised: one bad file must
not fail a 140-row run, and ``CORRUPT`` is a distinct outcome from ``MISS`` so
the two are reported separately.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, Mapping, TypeVar

from config import CacheConfig
from logging_setup import StructuredLogger
from schemas import CacheOutcome, LogEvent

T = TypeVar("T")

INLINE_VALUE_MAX_CHARS = 64
"""Key-material values longer than this are stored as a digest rather than
inline, so the manifest stays small. Presentation only -- no decision reads it."""

MEDIA = "media"
PERCEPTION = "perception"


@dataclass(frozen=True)
class CacheRead(Generic[T]):
    """The result of one lookup. ``value`` is populated iff ``outcome`` is HIT."""

    outcome: CacheOutcome
    value: T | None = None

    @property
    def hit(self) -> bool:
        return self.outcome is CacheOutcome.HIT


# --------------------------------------------------------------------------
# Key construction
# --------------------------------------------------------------------------


def _canonical(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_sha256(path: Path) -> str:
    """sha256 of a file's bytes. The identity of a media file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def media_key_material(
    content_hash: str,
    extraction_version: str,
    extractor: Mapping[str, Any],
    cfg: CacheConfig,
) -> dict[str, Any]:
    """Everything that determines a media result.

    The path is not here and neither is the media_id -- only the bytes. So the
    same image referenced by two ids is analysed once, and renaming a file can
    never produce a stale hit.

    ``extractor`` is the *kind-specific* description of who produced the result
    and how: for an image the vision provider, its model id and prompt version;
    for a voice note the ASR provider, its model id, mode and language_code. A
    single flat ``model_id`` cannot express this -- an Anthropic model has no
    bearing on a Sarvam transcript, and a mode change must not be invisible.
    """
    return {
        "cache_schema_version": cfg.schema_version,
        "content_sha256": content_hash,
        "media_extraction_version": extraction_version,
        "extractor": dict(sorted(extractor.items())),
    }


def media_key(
    content_hash: str,
    extraction_version: str,
    extractor: Mapping[str, Any],
    cfg: CacheConfig,
) -> str:
    return _digest(_canonical(media_key_material(content_hash, extraction_version, extractor, cfg)))


def normalise_for_key(rendered: str, nonce: str) -> str:
    """Replace the run nonce with a stable placeholder so keys are run-independent.

    The fence nonce is regenerated every run for security. If it entered the
    hash, every key would be unique and the hit rate would be exactly zero. The
    text that is *sent* keeps the real nonce; the text that is *hashed* does not.
    """
    return rendered.replace(nonce, "{NONCE}") if nonce else rendered


def perception_key_material(
    *,
    prompt_template_version: str,
    model_id: str,
    model_params: Mapping[str, Any],
    response_schema_sha256: str,
    system_text: str,
    user_text: str,
    nonce: str,
    cfg: CacheConfig,
) -> dict[str, Any]:
    """Everything sent to the model, plus what shapes the request.

    Deliberately absent: PolicyConfig, ConfidenceConfig, EvidenceConfig. Policy
    iteration must not miss the cache -- that is the whole point of the split.
    """
    return {
        "cache_schema_version": cfg.schema_version,
        "prompt_template_version": prompt_template_version,
        "model_id": model_id,
        "model_params": dict(sorted(model_params.items())),
        "response_schema_sha256": response_schema_sha256,
        "system_text": normalise_for_key(system_text, nonce),
        "user_text": normalise_for_key(user_text, nonce),
    }


def perception_key(**kwargs: Any) -> str:
    return _digest(_canonical(perception_key_material(**kwargs)))


def key_material_digest(material: Mapping[str, Any]) -> dict[str, str]:
    """Per-component hashes, so an unexpected miss can be diagnosed by comparing
    which component changed -- without storing a second copy of every prompt."""
    return {
        key: (_digest(value) if isinstance(value, str) and len(value) > INLINE_VALUE_MAX_CHARS else _canonical({key: value}))
        for key, value in sorted(material.items())
    }


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------


def _namespace_dir(namespace: str, cfg: CacheConfig) -> Path:
    return cfg.media_dir if namespace == MEDIA else cfg.perception_dir


def entry_path(namespace: str, key: str, cfg: CacheConfig) -> Path:
    """Sharded by the first characters of the key so no directory grows unbounded."""
    return _namespace_dir(namespace, cfg) / key[: cfg.shard_prefix_length] / f"{key}.json"


# --------------------------------------------------------------------------
# Read / write
# --------------------------------------------------------------------------


def _load_and_validate(path: Path, key: str, namespace: str, cfg: CacheConfig) -> dict[str, Any]:
    """Parse and check the envelope. Raises on anything unexpected."""
    envelope = json.loads(path.read_text(encoding="utf-8"))
    if envelope.get("cache_schema_version") != cfg.schema_version:
        raise ValueError(
            f"schema version {envelope.get('cache_schema_version')} != {cfg.schema_version}"
        )
    if envelope.get("key") != key:
        raise ValueError(f"key {envelope.get('key')!r} does not match filename {key!r}")
    if envelope.get("kind") != namespace:
        raise ValueError(f"kind {envelope.get('kind')!r} != {namespace!r}")
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("payload is missing or not an object")
    return payload


def read(
    namespace: str, key: str, cfg: CacheConfig, log: StructuredLogger | None = None
) -> CacheRead[dict[str, Any]]:
    """Read one entry. A corrupt entry is a logged MISS, never an exception."""
    path = entry_path(namespace, key, cfg)
    if not path.exists():
        return CacheRead(CacheOutcome.MISS)
    try:
        payload = _load_and_validate(path, key, namespace, cfg)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        if log is not None:
            log.warning(
                LogEvent.CACHE_ENTRY_CORRUPT.value,
                path="cache.store.read",
                detail={"namespace": namespace, "key": key, "error_type": type(exc).__name__},
                action_taken="treated_as_miss",
            )
        return CacheRead(CacheOutcome.CORRUPT)
    return CacheRead(CacheOutcome.HIT, payload)


def write(
    namespace: str,
    key: str,
    payload: Mapping[str, Any],
    material: Mapping[str, Any],
    cfg: CacheConfig,
    *,
    created_at_utc: str,
) -> Path:
    """Write one entry atomically.

    Writes to ``<key>.json.tmp`` in the same shard directory, fsyncs, then
    replaces. A killed run can leave a .tmp file but never a truncated .json.
    """
    path = entry_path(namespace, key, cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = {
        "cache_schema_version": cfg.schema_version,
        "kind": namespace,
        "key": key,
        "created_at_utc": created_at_utc,
        "key_material_digest": key_material_digest(material),
        "payload": dict(payload),
    }
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(envelope, handle, indent=cfg.json_indent, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    return path


def missing_keys(namespace: str, keys: Mapping[str, str], cfg: CacheConfig) -> list[str]:
    """Ids whose cache entries are absent or unreadable. Used by the offline preflight."""
    missing: list[str] = []
    for identifier, key in sorted(keys.items()):
        if not read(namespace, key, cfg).hit:
            missing.append(identifier)
    return missing


def stats(namespace: str, cfg: CacheConfig) -> dict[str, int]:
    """Entry count on disk. For the warm command's summary line only -- this is
    a directory listing, not a metric over a run."""
    root = _namespace_dir(namespace, cfg)
    if not root.exists():
        return {"entries": 0}
    return {"entries": sum(1 for _ in root.rglob("*.json"))}
