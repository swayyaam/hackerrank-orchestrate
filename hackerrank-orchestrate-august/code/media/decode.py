"""Identify media by content and read its technical properties. No network.

The extension is never consulted. In this corpus all 13 audio files carry a
``.mp3`` suffix and only 4 are MP3 -- 5 are WAV and 4 are AAC -- so a decoder
that trusts the filename fails on 9 of 13, and 6 of the 8 target voice notes
would route on no content at all.

Container detection is pure Python over magic bytes, so it works with no system
dependency. Duration and stream detail come from ``ffprobe`` when it is
available; when it is not, they are ``None`` rather than guessed, and the
container is still identified.
"""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

from config import LimitsConfig

UNKNOWN = "unknown"

FFPROBE_TIMEOUT_SECONDS = 20.0
"""SOURCE (b): a probe of a local file that has not answered in twenty seconds
has failed; the container is already known from magic bytes either way."""


@dataclass(frozen=True)
class DecodedMedia:
    """What can be learned about a media file without a model."""

    container_format: str
    byte_length: int
    duration_seconds: float | None
    sample_rate_hz: int | None
    channels: int | None
    width: int | None
    height: int | None
    codec_name: str | None

    @property
    def pixel_count(self) -> int | None:
        if self.width is None or self.height is None:
            return None
        return self.width * self.height


# --------------------------------------------------------------------------
# Magic-byte container detection
# --------------------------------------------------------------------------


def sniff_container(head: bytes) -> str:
    """Identify the container from the leading bytes. Never reads the filename."""
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:2] == b"BM":
        return "bmp"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand[:3] == b"qt ":
            return "mov"
        if brand in (b"M4A ", b"M4B ", b"mp42", b"isom", b"iso2", b"M4V "):
            return "m4a"
        return "mp4"
    if head.startswith(b"ID3"):
        return "mp3"
    if head.startswith(b"OggS"):
        return "ogg"
    if head.startswith(b"fLaC"):
        return "flac"
    if len(head) >= 2 and head[0] == 0xFF:
        # MPEG audio frame sync: 11 set bits. Layer bits distinguish MP3 from
        # ADTS AAC, which uses the same sync word.
        second = head[1]
        if second & 0xE0 == 0xE0:
            layer = (second >> 1) & 0x03
            return "aac" if layer == 0 else "mp3"
    return UNKNOWN


def _png_dimensions(head: bytes) -> tuple[int, int] | None:
    if len(head) < 24 or head[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", head[16:24])
    return int(width), int(height)


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    """Walk JPEG segments to the first SOF marker."""
    index = 2
    end = len(data)
    while index + 9 < end:
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        if index + 4 > end:
            return None
        length = struct.unpack(">H", data[index + 2 : index + 4])[0]
        # SOF0..SOF15, excluding the non-frame markers DHT/JPG/DAC.
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if index + 9 > end:
                return None
            height, width = struct.unpack(">HH", data[index + 5 : index + 9])
            return int(width), int(height)
        index += 2 + length
    return None


def image_dimensions(container: str, data: bytes) -> tuple[int | None, int | None]:
    """Width and height from the header alone -- no image library."""
    if container == "png":
        found = _png_dimensions(data)
    elif container == "jpeg":
        found = _jpeg_dimensions(data)
    else:
        found = None
    return found if found else (None, None)


# --------------------------------------------------------------------------
# ffprobe (optional)
# --------------------------------------------------------------------------


def ffprobe_available() -> bool:
    return shutil.which("ffprobe") is not None


def _ffprobe(path: Path) -> dict[str, object]:
    """Duration and first-stream detail. Returns an empty dict on any failure."""
    if not ffprobe_available():
        return {}
    try:
        completed = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-show_entries",
                "stream=codec_name,sample_rate,channels,width,height",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=FFPROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if completed.returncode != 0 or not completed.stdout.strip():
        return {}
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {}


def _first(streams: list[dict[str, object]], key: str) -> object | None:
    for stream in streams:
        if key in stream:
            return stream[key]
    return None


def decode(path: Path, cfg: LimitsConfig) -> DecodedMedia:
    """Identify the file by content and read what is knowable without a model.

    Raises:
        MediaDecodeError: the file exceeds a resource cap, checked before any
            probe runs.
    """
    from errors import MediaDecodeError

    byte_length = path.stat().st_size
    if byte_length > cfg.MAX_MEDIA_BYTES:
        raise MediaDecodeError(
            f"{path.name}: {byte_length} bytes exceeds MAX_MEDIA_BYTES={cfg.MAX_MEDIA_BYTES}"
        )

    with path.open("rb") as handle:
        head = handle.read(1 << 16)
    container = sniff_container(head)
    width, height = image_dimensions(container, head)

    if width is not None and height is not None and width * height > cfg.MAX_IMAGE_PIXELS:
        raise MediaDecodeError(
            f"{path.name}: {width}x{height} exceeds MAX_IMAGE_PIXELS={cfg.MAX_IMAGE_PIXELS}"
        )

    probed = _ffprobe(path)
    streams = probed.get("streams") or []  # type: ignore[assignment]
    raw_duration = (probed.get("format") or {}).get("duration")  # type: ignore[union-attr]
    duration = float(raw_duration) if raw_duration not in (None, "N/A") else None

    if duration is not None and duration > cfg.MAX_AUDIO_SECONDS and container in AUDIO_CONTAINERS:
        raise MediaDecodeError(
            f"{path.name}: {duration:.1f}s exceeds MAX_AUDIO_SECONDS={cfg.MAX_AUDIO_SECONDS}"
        )

    sample_rate = _first(streams, "sample_rate")
    channels = _first(streams, "channels")
    if width is None:
        probe_width = _first(streams, "width")
        width = int(probe_width) if probe_width is not None else None
    if height is None:
        probe_height = _first(streams, "height")
        height = int(probe_height) if probe_height is not None else None

    return DecodedMedia(
        container_format=container,
        byte_length=byte_length,
        duration_seconds=duration,
        sample_rate_hz=int(sample_rate) if sample_rate is not None else None,
        channels=int(channels) if channels is not None else None,
        width=width,
        height=height,
        codec_name=str(_first(streams, "codec_name")) if _first(streams, "codec_name") else None,
    )


AUDIO_CONTAINERS = frozenset({"wav", "mp3", "aac", "m4a", "ogg", "flac"})
IMAGE_CONTAINERS = frozenset({"jpeg", "png", "gif", "webp", "bmp"})
