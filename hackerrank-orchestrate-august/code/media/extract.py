"""Produce a MediaArtifact for a message's attached media.

Cache-first. A routing run **reads** the media cache and never writes it: if an
entry is missing during routing the row degrades to a named failure state rather
than quietly becoming a paid pass. Only ``warm-media`` writes entries.

Failure is a first-class state, never an exception to swallow. A file that
cannot be read yields ``readable=False`` with a ``failure_reason``, which becomes
``F.media_failed`` -- and ``F.media_unread`` only when the message has no text,
because a captioned image whose file failed still has its caption.
"""

from __future__ import annotations

from typing import Mapping

from cache import store
from config import RouterConfig
from errors import MediaDecodeError, MediaPathError, PerceptionTransportError
from logging_setup import StructuredLogger
from media.decode import AUDIO_CONTAINERS, IMAGE_CONTAINERS, decode
from media.locate import resolve_media_path
from perception.client import ExtractionResult, MediaExtractionClient
from schemas import CacheOutcome, IncomingMessage, MediaArtifact, MediaKind, ThreatEvent

MEDIA_EXTRACTION_VERSION = "1.0.0"
"""Bumped when the extraction prompt or post-processing changes. Part of the
media cache key, so a bump produces new keys rather than invalidating anything."""

# Named failure reasons. These land on the row record and are counted by the
# metrics engine's media.failures_by_cause. Provider-specific causes come from
# the raised PerceptionTransportError's own ``cause``.
FILE_MISSING = "file_missing"
PATH_REJECTED = "path_rejected"
LIMIT_EXCEEDED = "limit_exceeded"
CACHE_MISSING = "cache_missing"
EXTRACTION_UNAVAILABLE = "extraction_unavailable"
UNSUPPORTED_CONTAINER = "unsupported_container"
DURATION_EXCEEDED = "duration_exceeded"
"""The file is longer than the transcription endpoint accepts. Detected before
the call from the decoded duration, so an over-long file spends no request; a
provider 422 maps to its own cause when the duration was not knowable (no
ffprobe). The audio is never truncated to fit -- half a voice note routed as if
it were whole is worse than a named failure."""

# extraction_status values on the cache envelope. Three of the four are terminal
# verdicts about the file itself; DECODED_ONLY is the one that is not.
COMPLETE = "complete"
UNSUPPORTED = "unsupported"
DECODED_ONLY = "decoded_only"
"""Container facts were cached but extraction was never *attempted*, because no
client was configured. Distinct from every other status in exactly one way: it
is a statement about the run, not about the file, so it is the only status a
later pass will retry."""

_EXTENSION_ALIASES: dict[str, set[str]] = {
    "jpeg": {"jpg", "jpeg"},
    "m4a": {"m4a", "mp4", "aac"},
    "mp3": {"mp3"},
    "wav": {"wav"},
}
"""Extensions that legitimately name a container. Used only to record whether a
file's extension matched what its bytes say -- never to choose a decoder."""


def extractor_material(kind: MediaKind, cfg: RouterConfig) -> dict[str, object]:
    """Who extracts this kind of media, and under exactly which settings.

    Goes into the media cache key. Kind-specific on purpose: the Anthropic model
    has no bearing on a transcript, and the Sarvam mode has none on an image
    description, so keying both on one flat identifier would make a mode change
    invisible and attribute every result to the wrong provider.
    """
    e = cfg.extraction
    if kind is MediaKind.IMAGE:
        return {
            "provider": e.VISION_PROVIDER,
            "model_id": cfg.model_id,
            "prompt_version": e.VISION_PROMPT_VERSION,
        }
    return {
        "provider": e.VOICE_PROVIDER,
        "model_id": e.VOICE_MODEL_ID,
        "mode": e.VOICE_MODE,
        "language_code": e.VOICE_LANGUAGE_CODE,
    }


def _artifact(
    message: IncomingMessage,
    *,
    readable: bool,
    container: str | None = None,
    duration: float | None = None,
    content: str | None = None,
    failure_reason: str | None = None,
) -> MediaArtifact:
    assert message.media_id is not None
    return MediaArtifact(
        media_id=message.media_id,
        kind=message.media_kind,
        container_format=container,
        duration_seconds=duration,
        readable=readable,
        content=content,
        failure_reason=failure_reason,
    )


def _content_of(payload: Mapping[str, object]) -> str | None:
    voice = payload.get("voice")
    if isinstance(voice, Mapping) and voice.get("transcript"):
        return str(voice["transcript"])
    image = payload.get("image")
    if isinstance(image, Mapping):
        parts = [str(image.get("visual_observation") or ""), str(image.get("extracted_text") or "")]
        joined = "\n".join(part for part in parts if part.strip())
        return joined or None
    return None


def extract(
    message: IncomingMessage,
    cfg: RouterConfig,
    *,
    media_paths: Mapping[str, str],
    log: StructuredLogger | None = None,
) -> tuple[MediaArtifact | None, CacheOutcome]:
    """Return the artifact for this message's media, and the cache outcome.

    Runs before ``build_context`` and takes only the message row -- it needs the
    media id and kind, not an assembled context. Returns ``(None, n/a)`` when the
    message has no media. Never raises: one bad file cannot fail the run.
    """
    if message.media_id is None or message.media_kind is MediaKind.NONE:
        return None, CacheOutcome.NOT_APPLICABLE

    relative = media_paths.get(message.media_id)
    if relative is None:
        return _artifact(message, readable=False, failure_reason=FILE_MISSING), CacheOutcome.MISS

    try:
        path = resolve_media_path(cfg.dataset_root, relative, message.media_id, message.media_kind)
    except MediaPathError:
        if log is not None:
            log.warning(
                ThreatEvent.MEDIA_PATH_ESCAPED_ROOT.value,
                path="media.extract.extract",
                message_id=message.message_id,
                media_id=message.media_id,
                detail={"relative_path": relative},
                action_taken="media_failed",
            )
        return _artifact(message, readable=False, failure_reason=PATH_REJECTED), CacheOutcome.MISS

    if not path.exists():
        return _artifact(message, readable=False, failure_reason=FILE_MISSING), CacheOutcome.MISS

    try:
        decoded = decode(path, cfg.limits)
    except MediaDecodeError as exc:
        if log is not None:
            log.warning(
                ThreatEvent.MEDIA_LIMIT_EXCEEDED.value,
                path="media.extract.extract",
                message_id=message.message_id,
                media_id=message.media_id,
                detail={"reason": str(exc)},
                action_taken="media_failed",
            )
        return _artifact(message, readable=False, failure_reason=LIMIT_EXCEEDED), CacheOutcome.MISS

    key = store.media_key(
        store.content_sha256(path),
        MEDIA_EXTRACTION_VERSION,
        extractor_material(message.media_kind, cfg),
        cfg.cache,
    )
    cached = store.read(store.MEDIA, key, cfg.cache, log)
    if cached.hit and cached.value is not None:
        payload = cached.value
        content = _content_of(payload)
        duration = payload.get("duration_seconds")
        # The warm pass named why extraction did not produce content. Carry that
        # name through rather than flattening every unreadable row to
        # "extraction_unavailable" -- a file the endpoint is too short for and a
        # provider that was rate-limited are different problems with different
        # fixes, and the metrics engine counts them separately.
        stored_failure = payload.get("extraction_failure")
        return (
            _artifact(
                message,
                readable=bool(content),
                container=str(payload.get("container_format") or decoded.container_format),
                duration=float(duration) if isinstance(duration, (int, float)) else None,
                content=content,
                failure_reason=(
                    None
                    if content
                    else (str(stored_failure) if stored_failure else EXTRACTION_UNAVAILABLE)
                ),
            ),
            CacheOutcome.HIT,
        )

    # A routing run never calls out and never writes the cache.
    if log is not None:
        log.warning(
            ThreatEvent.MEDIA_DECODE_FAILED.value,
            path="media.extract.extract",
            message_id=message.message_id,
            media_id=message.media_id,
            detail={"container": decoded.container_format, "reason": CACHE_MISSING},
            action_taken="media_failed",
        )
    return (
        _artifact(
            message,
            readable=False,
            container=decoded.container_format,
            duration=decoded.duration_seconds,
            failure_reason=CACHE_MISSING,
        ),
        cached.outcome,
    )


# --------------------------------------------------------------------------
# Warm path -- the only writer
# --------------------------------------------------------------------------


def warm_one(
    media_id: str,
    relative_path: str,
    kind: MediaKind,
    cfg: RouterConfig,
    client: MediaExtractionClient | None,
    now_utc: str,
    log: StructuredLogger | None = None,
) -> tuple[CacheOutcome, str | None]:
    """Populate one media cache entry. Returns (outcome, failure_reason).

    ``HIT`` means the entry already existed and no call was made -- which is what
    makes a second ``warm-media`` a complete no-op. ``now_utc`` is supplied by the
    caller so every entry from one pass carries the same stamp and this module
    never reads the clock.

    Raises:
        MediaPathError: the path escapes the dataset root.
        MediaDecodeError: the file exceeds a resource cap.
    """
    path = resolve_media_path(cfg.dataset_root, relative_path, media_id, kind)
    decoded = decode(path, cfg.limits)
    content_hash = store.content_sha256(path)
    extractor = extractor_material(kind, cfg)
    key = store.media_key(content_hash, MEDIA_EXTRACTION_VERSION, extractor, cfg.cache)

    cached = store.read(store.MEDIA, key, cfg.cache, log)
    if cached.hit and cached.value is not None:
        stored_status = cached.value.get("extraction_status")
        # DECODED_ONLY means extraction was never *attempted* -- there was no
        # client. Treating that as a hit would let an offline warm pass poison
        # the cache and silently turn the next --allow-network pass into a
        # no-op. Every other status is a real verdict and stays a hit.
        retry = stored_status == DECODED_ONLY and client is not None
        if not retry:
            failure = cached.value.get("extraction_failure")
            return CacheOutcome.HIT, str(failure) if failure else None

    expected = IMAGE_CONTAINERS if kind is MediaKind.IMAGE else AUDIO_CONTAINERS
    supported = decoded.container_format in expected
    too_long = (
        kind is MediaKind.VOICE
        and decoded.duration_seconds is not None
        and decoded.duration_seconds > cfg.extraction.VOICE_MAX_SECONDS
    )

    result: ExtractionResult | None = None
    failure: str | None = None
    if not supported:
        # Cached like any other outcome. The verdict is a deterministic function
        # of the file's bytes, so recomputing it on every warm would make the
        # command non-idempotent for no benefit.
        failure = UNSUPPORTED_CONTAINER
    elif too_long:
        # Known-unacceptable before the call, so no request is spent proving it.
        failure = DURATION_EXCEEDED
    elif client is None:
        failure = EXTRACTION_UNAVAILABLE
    else:
        data = path.read_bytes()
        try:
            result = (
                client.describe_image(data, decoded.container_format)
                if kind is MediaKind.IMAGE
                else client.transcribe_audio(
                    data, decoded.container_format, filename=path.name
                )
            )
        except PerceptionTransportError as exc:
            # Each provider status keeps its own name here rather than
            # collapsing into one bucket, so a rate limit and a rejected
            # credential are distinguishable in failures_by_cause.
            failure = getattr(exc, "cause", EXTRACTION_UNAVAILABLE)

    status = COMPLETE
    if result is None:
        status = UNSUPPORTED if not supported else (DURATION_EXCEEDED if too_long else DECODED_ONLY)

    payload: dict[str, object] = {
        "content_sha256": content_hash,
        "kind": kind.value,
        "container_format": decoded.container_format,
        "container_detected_from": "magic_bytes",
        "byte_length": decoded.byte_length,
        "duration_seconds": decoded.duration_seconds,
        "codec_name": decoded.codec_name,
        "width": decoded.width,
        "height": decoded.height,
        "extraction_status": status,
        "extraction_failure": failure,
        "declared_extension": path.suffix.lstrip("."),
        "extension_matches_container": path.suffix.lstrip(".").lower()
        in _EXTENSION_ALIASES.get(decoded.container_format, {decoded.container_format}),
        "image": None,
        "voice": None,
    }
    if kind is MediaKind.IMAGE:
        extracted = result.secondary_content if result else None
        payload["image"] = {
            "visual_observation": result.content if result else None,
            "extracted_text": extracted or None,
            "extracted_text_present": bool(extracted),
        }
    else:
        payload["voice"] = {
            "transcript": result.content if result else None,
            "language_code": result.language_code if result else None,
            "language_probability": result.language_probability if result else None,
            "confidence_kind": result.confidence_kind if result else "unavailable",
            "transcription_mode": cfg.extraction.VOICE_MODE,
            "duration_seconds": decoded.duration_seconds,
            "sample_rate_hz": decoded.sample_rate_hz,
            "channels": decoded.channels,
        }

    material = store.media_key_material(
        content_hash, MEDIA_EXTRACTION_VERSION, extractor, cfg.cache
    )
    store.write(
        store.MEDIA,
        key,
        payload,
        material,
        cfg.cache,
        created_at_utc=now_utc,
    )
    return CacheOutcome.MISS, failure
