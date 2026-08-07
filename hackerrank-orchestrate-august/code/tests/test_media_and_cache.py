"""Phase 1: container sniffing, the cache substrate, and the extract contract.

No network. Fixtures are synthesised in-process so the suite is standalone and
runs from the submission archive with no dataset present.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from cache import store
from config import CacheConfig, ExtractionConfig, LimitsConfig, RouterConfig
from errors import (
    MediaDecodeError,
    MediaPathError,
    PerceptionTransportError,
    ProviderCredentialsRejectedError,
    ProviderRateLimitedError,
    ProviderRejectedMediaError,
    ProviderUnavailableError,
)
from media.decode import decode, ffprobe_available, image_dimensions, sniff_container
from media.extract import (
    MEDIA_EXTRACTION_VERSION,
    extract,
    extractor_material,
    warm_one,
)
from perception.client import (
    AnthropicMediaClient,
    CredentialsMissingError,
    ExtractionResult,
    build_media_client,
    credentials_present,
)
from schemas import CacheOutcome, ConversationType, IncomingMessage, MediaKind

PKG = Path(__file__).resolve().parents[1]

IMAGE_EXTRACTOR = extractor_material(MediaKind.IMAGE, RouterConfig())
VOICE_EXTRACTOR = extractor_material(MediaKind.VOICE, RouterConfig())


# --------------------------------------------------------------------------
# Synthetic media, so no test depends on the dataset
# --------------------------------------------------------------------------

JPEG = bytes.fromhex("ffd8ff") + b"\x00" * 32
PNG_HEADER = b"\x89PNG\r\n\x1a\n"


def png_bytes(width: int = 4, height: int = 3) -> bytes:
    return PNG_HEADER + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", width, height) + b"\x00" * 32


def wav_bytes() -> bytes:
    return b"RIFF" + b"\x00\x00\x00\x00" + b"WAVE" + b"\x00" * 32


def m4a_bytes() -> bytes:
    return b"\x00\x00\x00\x20" + b"ftyp" + b"M4A " + b"\x00" * 32


def mp3_bytes() -> bytes:
    return b"ID3\x03\x00" + b"\x00" * 32


def adts_aac_bytes() -> bytes:
    return b"\xff\xf1" + b"\x00" * 32


def webp_bytes() -> bytes:
    return b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 32


# --------------------------------------------------------------------------
# Container sniffing -- the extension is never consulted
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload,expected",
    [
        (JPEG, "jpeg"),
        (png_bytes(), "png"),
        (wav_bytes(), "wav"),
        (m4a_bytes(), "m4a"),
        (mp3_bytes(), "mp3"),
        (adts_aac_bytes(), "aac"),
        (webp_bytes(), "webp"),
        (b"GIF89a" + b"\x00" * 32, "gif"),
        (b"not media at all", "unknown"),
    ],
)
def test_sniff_container_from_bytes(payload: bytes, expected: str) -> None:
    assert sniff_container(payload) == expected


def test_extension_is_never_consulted(tmp_path: Path) -> None:
    """The Phase 1 headline: a .mp3 that is really a WAV decodes as a WAV."""
    lying = tmp_path / "voice_note.mp3"
    lying.write_bytes(wav_bytes())
    assert decode(lying, LimitsConfig()).container_format == "wav"

    also_lying = tmp_path / "photo.jpg"
    also_lying.write_bytes(png_bytes())
    assert decode(also_lying, LimitsConfig()).container_format == "png"


def test_image_dimensions_without_an_image_library() -> None:
    assert image_dimensions("png", png_bytes(1920, 1080)) == (1920, 1080)
    assert image_dimensions("unknown", b"\x00" * 64) == (None, None)


def test_decode_rejects_an_oversized_file(tmp_path: Path) -> None:
    big = tmp_path / "big.bin"
    big.write_bytes(JPEG + b"\x00" * 4096)
    cfg = LimitsConfig(MAX_MEDIA_BYTES=64)
    with pytest.raises(MediaDecodeError, match="MAX_MEDIA_BYTES"):
        decode(big, cfg)


def test_decode_rejects_a_gigapixel_image(tmp_path: Path) -> None:
    huge = tmp_path / "huge.png"
    huge.write_bytes(png_bytes(60_000, 60_000))
    with pytest.raises(MediaDecodeError, match="MAX_IMAGE_PIXELS"):
        decode(huge, LimitsConfig())


# --------------------------------------------------------------------------
# Cache substrate
# --------------------------------------------------------------------------


@pytest.fixture()
def cache_cfg(tmp_path: Path) -> CacheConfig:
    return CacheConfig(
        root=tmp_path / ".cache",
        media_dir=tmp_path / ".cache" / "media",
        perception_dir=tmp_path / ".cache" / "perception",
    )


def test_media_key_is_content_addressed_not_path_addressed(tmp_path: Path, cache_cfg) -> None:
    """The same bytes under two names share one entry; a rename cannot go stale."""
    a, b = tmp_path / "a.jpg", tmp_path / "b.jpg"
    a.write_bytes(JPEG)
    b.write_bytes(JPEG)
    key_a = store.media_key(store.content_sha256(a), "1.0.0", IMAGE_EXTRACTOR, cache_cfg)
    key_b = store.media_key(store.content_sha256(b), "1.0.0", IMAGE_EXTRACTOR, cache_cfg)
    assert key_a == key_b

    c = tmp_path / "c.jpg"
    c.write_bytes(JPEG + b"different")
    assert store.media_key(store.content_sha256(c), "1.0.0", IMAGE_EXTRACTOR, cache_cfg) != key_a


def test_extraction_version_bump_produces_a_new_key(tmp_path: Path, cache_cfg) -> None:
    path = tmp_path / "a.jpg"
    path.write_bytes(JPEG)
    content = store.content_sha256(path)
    assert store.media_key(content, "1.0.0", IMAGE_EXTRACTOR, cache_cfg) != store.media_key(
        content, "2.0.0", IMAGE_EXTRACTOR, cache_cfg
    )


def test_write_then_read_round_trips(cache_cfg) -> None:
    material = store.media_key_material("abc", "1.0.0", IMAGE_EXTRACTOR, cache_cfg)
    store.write(store.MEDIA, "k" * 64, {"hello": "world"}, material, cache_cfg, created_at_utc="t")
    read = store.read(store.MEDIA, "k" * 64, cache_cfg)
    assert read.outcome is CacheOutcome.HIT
    assert read.value == {"hello": "world"}


def test_missing_entry_is_a_miss(cache_cfg) -> None:
    assert store.read(store.MEDIA, "z" * 64, cache_cfg).outcome is CacheOutcome.MISS


def test_corrupt_entry_is_a_miss_and_never_raises(cache_cfg) -> None:
    key = "c" * 64
    material = store.media_key_material("abc", "1.0.0", IMAGE_EXTRACTOR, cache_cfg)
    path = store.write(store.MEDIA, key, {"ok": True}, material, cache_cfg, created_at_utc="t")
    path.write_text("{ truncated", encoding="utf-8")

    read = store.read(store.MEDIA, key, cache_cfg)
    assert read.outcome is CacheOutcome.CORRUPT
    assert read.outcome is not CacheOutcome.MISS  # reported separately
    assert read.value is None


def test_key_mismatch_is_corrupt(cache_cfg) -> None:
    key = "d" * 64
    material = store.media_key_material("abc", "1.0.0", IMAGE_EXTRACTOR, cache_cfg)
    path = store.write(store.MEDIA, key, {"ok": True}, material, cache_cfg, created_at_utc="t")
    envelope = json.loads(path.read_text())
    envelope["key"] = "e" * 64
    path.write_text(json.dumps(envelope), encoding="utf-8")
    assert store.read(store.MEDIA, key, cache_cfg).outcome is CacheOutcome.CORRUPT


def test_schema_version_bump_makes_entries_unreachable(cache_cfg) -> None:
    key = "f" * 64
    material = store.media_key_material("abc", "1.0.0", IMAGE_EXTRACTOR, cache_cfg)
    store.write(store.MEDIA, key, {"ok": True}, material, cache_cfg, created_at_utc="t")
    bumped = CacheConfig(
        root=cache_cfg.root,
        media_dir=cache_cfg.media_dir,
        perception_dir=cache_cfg.perception_dir,
        schema_version=cache_cfg.schema_version + 1,
    )
    assert store.read(store.MEDIA, key, bumped).outcome is CacheOutcome.CORRUPT


def test_writes_are_atomic_leaving_no_tmp_file(cache_cfg) -> None:
    material = store.media_key_material("abc", "1.0.0", IMAGE_EXTRACTOR, cache_cfg)
    store.write(store.MEDIA, "a" * 64, {"ok": True}, material, cache_cfg, created_at_utc="t")
    assert list(cache_cfg.media_dir.rglob("*.json.tmp")) == []


def test_nonce_is_normalised_out_of_the_perception_key(cache_cfg) -> None:
    """Without this the key changes every run and the hit rate is exactly zero."""
    common = dict(
        prompt_template_version="1.0.0",
        model_id="m",
        model_params={"temperature": 0.0},
        response_schema_sha256="s",
        cfg=cache_cfg,
    )
    first = store.perception_key(
        system_text="guard abc123 end", user_text="body abc123", nonce="abc123", **common
    )
    second = store.perception_key(
        system_text="guard zzz999 end", user_text="body zzz999", nonce="zzz999", **common
    )
    assert first == second


def test_perception_key_ignores_policy_config(cache_cfg) -> None:
    """Changing a prompt must miss; changing policy must not."""
    common = dict(
        model_id="m",
        model_params={"temperature": 0.0},
        response_schema_sha256="s",
        system_text="system",
        user_text="user",
        nonce="",
        cfg=cache_cfg,
    )
    base = store.perception_key(prompt_template_version="1.0.0", **common)
    same = store.perception_key(prompt_template_version="1.0.0", **common)
    changed = store.perception_key(prompt_template_version="1.0.1", **common)
    assert base == same
    assert base != changed
    material = store.perception_key_material(prompt_template_version="1.0.0", **common)
    assert not any("policy" in key.lower() for key in material)


# --------------------------------------------------------------------------
# extract() -- the read path
# --------------------------------------------------------------------------


def _message(media_id: str | None, kind: MediaKind, text: str = "caption") -> IncomingMessage:
    from datetime import datetime

    return IncomingMessage(
        message_id="m1",
        user_id="user",
        conversation_type=ConversationType.GROUP,
        group_id="g",
        business_id=None,
        sender_user_id="s",
        created_at=datetime(2026, 7, 20, 10, 0),
        message_text=text,
        media_kind=kind,
        media_id=media_id,
        forwarded_count=0,
    )


def _router_cfg(tmp_path: Path) -> RouterConfig:
    return RouterConfig(
        dataset_root=tmp_path,
        cache=CacheConfig(
            root=tmp_path / ".cache",
            media_dir=tmp_path / ".cache" / "media",
            perception_dir=tmp_path / ".cache" / "perception",
        ),
    )


def test_no_media_returns_none_and_not_applicable(tmp_path: Path) -> None:
    artifact, outcome = extract(_message(None, MediaKind.NONE), _router_cfg(tmp_path), media_paths={})
    assert artifact is None
    assert outcome is CacheOutcome.NOT_APPLICABLE


def test_routing_run_never_writes_the_cache(tmp_path: Path) -> None:
    """A cold cache degrades the row; it does not become a paid pass."""
    cfg = _router_cfg(tmp_path)
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "x.jpg").write_bytes(JPEG)

    artifact, outcome = extract(
        _message("img_x", MediaKind.IMAGE), cfg, media_paths={"img_x": "media/x.jpg"}
    )
    assert artifact is not None and not artifact.readable
    assert artifact.failure_reason == "cache_missing"
    assert outcome is CacheOutcome.MISS
    assert not cfg.cache.media_dir.exists()


def test_warm_then_extract_hits(tmp_path: Path) -> None:
    cfg = _router_cfg(tmp_path)
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "x.jpg").write_bytes(JPEG)

    outcome, failure = warm_one("img_x", "media/x.jpg", MediaKind.IMAGE, cfg, client=None, now_utc="2026-01-01T00:00:00Z")
    assert outcome is CacheOutcome.MISS  # first warm writes
    assert failure == "extraction_unavailable"

    again, _ = warm_one("img_x", "media/x.jpg", MediaKind.IMAGE, cfg, client=None, now_utc="2026-01-01T00:00:00Z")
    assert again is CacheOutcome.HIT  # idempotent

    artifact, read_outcome = extract(
        _message("img_x", MediaKind.IMAGE), cfg, media_paths={"img_x": "media/x.jpg"}
    )
    assert read_outcome is CacheOutcome.HIT
    assert artifact is not None
    assert artifact.container_format == "jpeg"


def test_unsupported_container_is_cached_so_warm_stays_idempotent(tmp_path: Path) -> None:
    """A video behind a .jpg name: recorded once, not re-decided every run."""
    cfg = _router_cfg(tmp_path)
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "v.jpg").write_bytes(b"\x00\x00\x00\x20ftypmp42" + b"\x00" * 32)

    first, failure = warm_one("img_v", "media/v.jpg", MediaKind.IMAGE, cfg, client=None, now_utc="2026-01-01T00:00:00Z")
    assert first is CacheOutcome.MISS
    assert failure == "unsupported_container"
    second, _ = warm_one("img_v", "media/v.jpg", MediaKind.IMAGE, cfg, client=None, now_utc="2026-01-01T00:00:00Z")
    assert second is CacheOutcome.HIT


def test_path_escape_degrades_rather_than_raising(tmp_path: Path) -> None:
    cfg = _router_cfg(tmp_path)
    (tmp_path.parent / "outside.jpg").write_bytes(JPEG)
    artifact, _ = extract(
        _message("img_x", MediaKind.IMAGE), cfg, media_paths={"img_x": "../outside.jpg"}
    )
    assert artifact is not None and not artifact.readable
    assert artifact.failure_reason == "path_rejected"


def test_warm_raises_on_a_path_escape(tmp_path: Path) -> None:
    """The warm path is allowed to raise -- it is not routing a row."""
    cfg = _router_cfg(tmp_path)
    (tmp_path.parent / "outside2.jpg").write_bytes(JPEG)
    with pytest.raises(MediaPathError):
        warm_one("img_x", "../outside2.jpg", MediaKind.IMAGE, cfg, client=None, now_utc="2026-01-01T00:00:00Z")


# --------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------


def test_client_refuses_to_build_without_credentials(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ROUTER_MODEL_ID", raising=False)
    assert not credentials_present()
    with pytest.raises(CredentialsMissingError):
        build_media_client("", LimitsConfig(), ExtractionConfig())


def test_client_refuses_without_a_model_id(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    with pytest.raises(CredentialsMissingError, match="ROUTER_MODEL_ID"):
        AnthropicMediaClient("", LimitsConfig(), ExtractionConfig())


def test_extraction_version_is_declared() -> None:
    assert MEDIA_EXTRACTION_VERSION


# --------------------------------------------------------------------------
# Per-kind extractor keying
#
# Images go to Anthropic, voice notes go to Sarvam. One flat model id cannot
# express that: it would attribute a transcript to a vision model and make a
# transcription-mode change invisible.
# --------------------------------------------------------------------------


def test_the_two_media_kinds_key_separately(cache_cfg) -> None:
    """Identical bytes under two extractors are two entries, not one."""
    assert store.media_key("abc", "1.0.0", IMAGE_EXTRACTOR, cache_cfg) != store.media_key(
        "abc", "1.0.0", VOICE_EXTRACTOR, cache_cfg
    )


def test_transcription_mode_is_part_of_the_key(cache_cfg) -> None:
    """A transcript produced in one mode must never be served for another."""
    other = extractor_material(
        MediaKind.VOICE, RouterConfig(extraction=ExtractionConfig(VOICE_MODE="transcribe"))
    )
    assert other["mode"] != VOICE_EXTRACTOR["mode"]
    assert store.media_key("abc", "1.0.0", other, cache_cfg) != store.media_key(
        "abc", "1.0.0", VOICE_EXTRACTOR, cache_cfg
    )


def test_language_code_is_part_of_the_key(cache_cfg) -> None:
    """Pinning a language suppresses language_probability, so it is a new result."""
    pinned = extractor_material(
        MediaKind.VOICE, RouterConfig(extraction=ExtractionConfig(VOICE_LANGUAGE_CODE="hi-IN"))
    )
    assert VOICE_EXTRACTOR["language_code"] is None
    assert store.media_key("abc", "1.0.0", pinned, cache_cfg) != store.media_key(
        "abc", "1.0.0", VOICE_EXTRACTOR, cache_cfg
    )


def test_the_anthropic_model_keys_images_but_not_voice_notes(cache_cfg) -> None:
    """Switching the vision model must not invalidate transcripts it never made."""
    swapped = RouterConfig(model_id="some-other-model")
    assert extractor_material(MediaKind.IMAGE, swapped) != IMAGE_EXTRACTOR
    assert extractor_material(MediaKind.VOICE, swapped) == VOICE_EXTRACTOR


def test_the_vision_prompt_version_is_part_of_the_image_key(cache_cfg) -> None:
    bumped = extractor_material(
        MediaKind.IMAGE, RouterConfig(extraction=ExtractionConfig(VISION_PROMPT_VERSION="2.0.0"))
    )
    assert store.media_key("abc", "1.0.0", bumped, cache_cfg) != store.media_key(
        "abc", "1.0.0", IMAGE_EXTRACTOR, cache_cfg
    )


# --------------------------------------------------------------------------
# Extraction outcomes
# --------------------------------------------------------------------------


class _RecordingClient:
    """Counts calls so a test can assert that none was made."""

    def __init__(self, result: ExtractionResult | None = None, raises: Exception | None = None):
        self.result = result or ExtractionResult(content="x")
        self.raises = raises
        self.image_calls: list[str] = []
        self.audio_calls: list[tuple[str, str]] = []

    def describe_image(self, image_bytes: bytes, container: str) -> ExtractionResult:
        self.image_calls.append(container)
        if self.raises is not None:
            raise self.raises
        return self.result

    def transcribe_audio(
        self, audio_bytes: bytes, container: str, *, filename: str
    ) -> ExtractionResult:
        self.audio_calls.append((container, filename))
        if self.raises is not None:
            raise self.raises
        return self.result


def _cached_payload(cfg: RouterConfig, media_id: str, relative: str, kind: MediaKind) -> dict:
    key = store.media_key(
        store.content_sha256(cfg.dataset_root / relative),
        MEDIA_EXTRACTION_VERSION,
        extractor_material(kind, cfg),
        cfg.cache,
    )
    read = store.read(store.MEDIA, key, cfg.cache)
    assert read.value is not None
    return read.value


@pytest.mark.skipif(not ffprobe_available(), reason="duration is unknowable without ffprobe")
def test_audio_over_the_endpoint_ceiling_is_named_and_never_truncated(tmp_path: Path) -> None:
    """A file longer than the endpoint accepts spends no request and is not cut."""
    cfg = _router_cfg(tmp_path)
    (tmp_path / "media").mkdir()
    seconds, rate = 31, 8_000
    body = b"\x80" * (seconds * rate)
    header = (
        b"RIFF" + struct.pack("<I", 36 + len(body)) + b"WAVEfmt " + struct.pack("<I", 16)
        + struct.pack("<HHIIHH", 1, 1, rate, rate, 1, 8)
        + b"data" + struct.pack("<I", len(body))
    )
    (tmp_path / "media" / "long.mp3").write_bytes(header + body)

    client = _RecordingClient()
    outcome, failure = warm_one(
        "vn_long", "media/long.mp3", MediaKind.VOICE, cfg, client, "2026-01-01T00:00:00Z"
    )

    assert outcome is CacheOutcome.MISS
    assert failure == "duration_exceeded"
    assert client.audio_calls == []  # the ceiling was enforced before the call

    payload = _cached_payload(cfg, "vn_long", "media/long.mp3", MediaKind.VOICE)
    assert payload["extraction_status"] == "duration_exceeded"
    assert payload["voice"]["transcript"] is None
    assert payload["duration_seconds"] > cfg.extraction.VOICE_MAX_SECONDS

    # Cached like any other verdict, so a second warm is still a no-op.
    again, _ = warm_one(
        "vn_long", "media/long.mp3", MediaKind.VOICE, cfg, client, "2026-01-01T00:00:00Z"
    )
    assert again is CacheOutcome.HIT


def test_transcript_and_language_signals_are_persisted(tmp_path: Path) -> None:
    """language_probability is an external signal; it must survive the cache."""
    cfg = _router_cfg(tmp_path)
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "v.mp3").write_bytes(wav_bytes())

    client = _RecordingClient(
        ExtractionResult(
            content="mera phone number hai 12345",
            language_code="hi-IN",
            language_probability=0.87,
            confidence_kind="language_probability",
        )
    )
    outcome, failure = warm_one(
        "vn_a", "media/v.mp3", MediaKind.VOICE, cfg, client, "2026-01-01T00:00:00Z"
    )
    assert outcome is CacheOutcome.MISS and failure is None

    voice = _cached_payload(cfg, "vn_a", "media/v.mp3", MediaKind.VOICE)["voice"]
    assert voice["transcript"] == "mera phone number hai 12345"
    assert voice["language_code"] == "hi-IN"
    assert voice["language_probability"] == 0.87
    assert voice["confidence_kind"] == "language_probability"
    assert voice["transcription_mode"] == cfg.extraction.VOICE_MODE


def test_the_lying_extension_reaches_the_provider_verbatim(tmp_path: Path) -> None:
    """The file object carries the on-disk name; the provider sniffs the bytes.

    Every voice note in this corpus is named .mp3 and a third are not. Passing
    our own guess would only give the provider a second chance to be wrong.
    """
    cfg = _router_cfg(tmp_path)
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "v.mp3").write_bytes(m4a_bytes())

    client = _RecordingClient()
    warm_one("vn_b", "media/v.mp3", MediaKind.VOICE, cfg, client, "2026-01-01T00:00:00Z")
    assert client.audio_calls == [("m4a", "v.mp3")]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ProviderRejectedMediaError("422"), "unprocessable_media"),
        (ProviderCredentialsRejectedError("403"), "credentials_rejected"),
        (ProviderRateLimitedError("429"), "rate_limited"),
        (ProviderUnavailableError("503"), "provider_unavailable"),
        (PerceptionTransportError("something else"), "extraction_unavailable"),
    ],
)
def test_each_provider_status_keeps_its_own_failure_cause(
    tmp_path: Path, error: Exception, expected: str
) -> None:
    """A rate limit and a refused credential must not collapse into one bucket."""
    cfg = _router_cfg(tmp_path)
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "v.mp3").write_bytes(wav_bytes())

    client = _RecordingClient(raises=error)
    outcome, failure = warm_one(
        "vn_c", "media/v.mp3", MediaKind.VOICE, cfg, client, "2026-01-01T00:00:00Z"
    )
    assert outcome is CacheOutcome.MISS
    assert failure == expected
    assert _cached_payload(cfg, "vn_c", "media/v.mp3", MediaKind.VOICE)["extraction_failure"] == expected


def test_image_text_is_extracted_alongside_the_description(tmp_path: Path) -> None:
    cfg = _router_cfg(tmp_path)
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "p.jpg").write_bytes(JPEG)

    client = _RecordingClient(
        ExtractionResult(content="a poster on a wall", secondary_content="FLAT 40% OFF")
    )
    warm_one("img_p", "media/p.jpg", MediaKind.IMAGE, cfg, client, "2026-01-01T00:00:00Z")

    image = _cached_payload(cfg, "img_p", "media/p.jpg", MediaKind.IMAGE)["image"]
    assert image["visual_observation"] == "a poster on a wall"
    assert image["extracted_text"] == "FLAT 40% OFF"
    assert image["extracted_text_present"] is True

    artifact, _ = extract(
        _message("img_p", MediaKind.IMAGE), cfg, media_paths={"img_p": "media/p.jpg"}
    )
    assert artifact is not None and artifact.readable
    assert artifact.content is not None
    assert "poster" in artifact.content and "40%" in artifact.content


def test_a_credential_never_appears_in_the_error_it_raises(monkeypatch) -> None:
    monkeypatch.setenv("SARVAM_API_KEY", "")
    from perception.client import require_sarvam_credentials

    with pytest.raises(CredentialsMissingError) as caught:
        require_sarvam_credentials()
    assert "SARVAM_API_KEY" in str(caught.value)

    monkeypatch.setenv("SARVAM_API_KEY", "secret-value-do-not-leak")
    assert require_sarvam_credentials() == "secret-value-do-not-leak"


def test_an_offline_warm_does_not_block_a_later_networked_one(tmp_path: Path) -> None:
    """A content-free entry must not turn the next paid pass into a no-op.

    This is the one status that is a statement about the run rather than about
    the file, so it is the only one a later pass retries.
    """
    cfg = _router_cfg(tmp_path)
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "v.mp3").write_bytes(wav_bytes())

    first, failure = warm_one(
        "vn_d", "media/v.mp3", MediaKind.VOICE, cfg, client=None, now_utc="2026-01-01T00:00:00Z"
    )
    assert first is CacheOutcome.MISS and failure == "extraction_unavailable"
    assert _cached_payload(cfg, "vn_d", "media/v.mp3", MediaKind.VOICE)["extraction_status"] == "decoded_only"

    client = _RecordingClient(ExtractionResult(content="now transcribed"))
    second, failure = warm_one(
        "vn_d", "media/v.mp3", MediaKind.VOICE, cfg, client, "2026-01-01T00:00:00Z"
    )
    assert second is CacheOutcome.MISS  # retried, not skipped
    assert failure is None
    assert len(client.audio_calls) == 1

    payload = _cached_payload(cfg, "vn_d", "media/v.mp3", MediaKind.VOICE)
    assert payload["extraction_status"] == "complete"
    assert payload["voice"]["transcript"] == "now transcribed"

    third, _ = warm_one("vn_d", "media/v.mp3", MediaKind.VOICE, cfg, client, "2026-01-01T00:00:00Z")
    assert third is CacheOutcome.HIT
    assert len(client.audio_calls) == 1  # still idempotent once complete


def test_a_terminal_verdict_is_never_retried_even_with_a_client(tmp_path: Path) -> None:
    """unsupported and duration_exceeded are facts about the file, not the run."""
    cfg = _router_cfg(tmp_path)
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "v.jpg").write_bytes(b"\x00\x00\x00\x20ftypmp42" + b"\x00" * 32)

    warm_one("img_v", "media/v.jpg", MediaKind.IMAGE, cfg, client=None, now_utc="2026-01-01T00:00:00Z")
    client = _RecordingClient()
    outcome, failure = warm_one(
        "img_v", "media/v.jpg", MediaKind.IMAGE, cfg, client, "2026-01-01T00:00:00Z"
    )
    assert outcome is CacheOutcome.HIT
    assert failure == "unsupported_container"  # the verdict is reported, not lost
    assert client.image_calls == []


def test_the_duration_guard_does_not_depend_on_ffprobe(tmp_path: Path, monkeypatch) -> None:
    """With duration unknowable, call anyway and let the provider's 422 name it.

    ffprobe is optional. The pre-check is an optimisation that avoids spending a
    request we know will fail -- it is not the mechanism that enforces the
    ceiling. When duration is None the file must still be attempted, or an
    environment without ffprobe would transcribe nothing at all.
    """
    from media import extract as extract_module
    from media.decode import DecodedMedia

    cfg = _router_cfg(tmp_path)
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "v.mp3").write_bytes(wav_bytes())

    monkeypatch.setattr(
        extract_module,
        "decode",
        lambda path, limits: DecodedMedia(
            container_format="wav",
            byte_length=path.stat().st_size,
            duration_seconds=None,  # exactly what an absent ffprobe yields
            sample_rate_hz=None,
            channels=None,
            width=None,
            height=None,
            codec_name=None,
        ),
    )

    client = _RecordingClient(raises=ProviderRejectedMediaError("422"))
    outcome, failure = warm_one(
        "vn_e", "media/v.mp3", MediaKind.VOICE, cfg, client, "2026-01-01T00:00:00Z"
    )
    assert len(client.audio_calls) == 1, "an unknown duration must not suppress the call"
    assert failure == "unprocessable_media"  # the provider named it, not the pre-check
    assert outcome is CacheOutcome.MISS


def test_a_named_failure_cause_survives_the_read_path(tmp_path: Path) -> None:
    """The routing read must report the cause the warm pass recorded.

    Flattening every unreadable row to `extraction_unavailable` would hide which
    files are recoverable (retry the provider) and which are not (too long).
    """
    cfg = _router_cfg(tmp_path)
    (tmp_path / "media").mkdir()
    (tmp_path / "media" / "v.jpg").write_bytes(b"\x00\x00\x00\x20ftypmp42" + b"\x00" * 32)

    warm_one("img_v", "media/v.jpg", MediaKind.IMAGE, cfg, client=None, now_utc="2026-01-01T00:00:00Z")
    artifact, outcome = extract(
        _message("img_v", MediaKind.IMAGE), cfg, media_paths={"img_v": "media/v.jpg"}
    )
    assert outcome is CacheOutcome.HIT
    assert artifact is not None and not artifact.readable
    assert artifact.failure_reason == "unsupported_container"
