"""Phase 6: the archive is what gets submitted, so the checks on it must bite.

Two properties, and the second is the one that matters:

1. The archive contains what a reviewer needs -- including `.cache/`, without
   which `route` cannot produce a single row offline.
2. **The secret scan can fail.** A clean scan over an archive that happens to
   contain no secrets proves nothing about the scanner. Every pattern is
   exercised against a planted example, because "the check passed" is only
   information if the check was capable of not passing -- the same defect class
   as a metric that reads rule metadata instead of the sentence it measures.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest

import make_zip

PKG = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def archive(tmp_path_factory) -> Path:
    """Build a real archive into a temp dir. Never touches the committed zip."""
    out = tmp_path_factory.mktemp("archive") / "code.zip"
    make_zip.build(PKG, out)
    return out


# --------------------------------------------------------------------------
# 1. Contents
# --------------------------------------------------------------------------


def test_the_archive_verifies_clean(archive: Path) -> None:
    assert make_zip.verify(archive) == []


def test_the_archive_ships_the_cache(archive: Path) -> None:
    """Without this the offline-reproducibility claim is false and nothing says so."""
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
    perception = [n for n in names if n.startswith("code/.cache/perception/")]
    media = [n for n in names if n.startswith("code/.cache/media/")]
    assert perception, "no perception cache: a reviewer without keys cannot route"
    assert media, "no media cache: a reviewer without keys cannot route"


def test_the_archive_excludes_the_real_env_file(archive: Path) -> None:
    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
    assert "code/.env" not in names
    assert "code/.env.example" in names, "the template must ship; only the real file must not"


def test_no_dataset_or_run_directories_leak(archive: Path) -> None:
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
    assert not [n for n in names if "/dataset/" in n or n.startswith("code/runs/")]


def test_the_shipped_cache_is_readable_with_no_environment_at_all() -> None:
    """The archive's headline claim, asserted rather than assumed.

    The model id is part of the perception cache key, so an unset
    `ROUTER_MODEL_ID` gives a different key for every row and the shipped cache
    becomes unreadable -- 140 misses and a hard failure, for a reviewer following
    a README that correctly says no credentials are needed offline. The
    clean-room test found this; this keeps it found.

    Deliberately checks the *resolution*, not a live route: the route itself is
    exercised end to end in the clean-room verification, and this must stay
    runnable with no dataset present.
    """
    import os
    from unittest import mock

    import config

    with mock.patch.dict(os.environ, {}, clear=True):
        assert config.load_model_id() == config.DEFAULT_MODEL_ID
    with mock.patch.dict(os.environ, {"ROUTER_MODEL_ID": ""}, clear=True):
        assert config.load_model_id() == config.DEFAULT_MODEL_ID, "empty must not key the cache"
    with mock.patch.dict(os.environ, {"ROUTER_MODEL_ID": "some-other-model"}, clear=True):
        assert config.load_model_id() == "some-other-model", "an explicit id must still override"


def test_the_default_model_id_is_the_one_the_shipped_cache_was_built_with() -> None:
    """A default that names a different model than the cache holds would be worse
    than no default: every row would miss with no indication why."""
    import json

    import config

    manifests = sorted((PKG.parent / "runs").glob("*/manifest.json"))
    if not manifests:
        pytest.skip("no run directories present; nothing to check the default against")
    recorded = {json.loads(m.read_text()).get("model_id") for m in manifests}
    recorded.discard(None)
    recorded.discard("")
    recorded.discard("none (rules-only)")
    assert config.DEFAULT_MODEL_ID in recorded, (
        f"default {config.DEFAULT_MODEL_ID!r} is not a model any run used: {sorted(recorded)}"
    )


# --------------------------------------------------------------------------
# 2. The scan can fail
# --------------------------------------------------------------------------

# Every planted shape is ASSEMBLED FROM FRAGMENTS so the matchable literal never
# appears in this file's own bytes. This file ships inside the archive, and the
# scan reads every member -- writing the shapes out whole would make the scanner
# flag its own test suite, and the tempting fix (waiving `tests/`) would carve a
# hole exactly where a pasted key is most likely to go unnoticed. The first run
# of these tests failed this way, which is the evidence the scan reads contents.
PLANTED: tuple[tuple[str, str, bytes], ...] = (
    ("anthropic api key", "code/docs/leak.md", b"key: sk-" + b"ant-api03-" + b"A" * 40),
    ("openai-style api key", "code/leak_openai.txt", b"sk" + b"-" + b"a" * 40),
    ("sarvam subscription key", "code/leak_sarvam.txt", b"sk" + b"_abcd1234_" + b"Z" * 20),
    ("aws access key id", "code/leak_aws.txt", b"AKI" + b"A" + b"ABCDEFGHIJKLMNOP"),
    ("google api key", "code/leak_google.txt", b"AIz" + b"a" + b"b" * 35),
    ("bearer token", "code/leak_bearer.txt", b"Authorization: Bear" + b"er " + b"c" * 30),
    ("private key block", "code/id_rsa", b"-----BEGIN " + b"RSA PRIVATE" + b" KEY-----\n"),
    ("populated credential assignment", "code/settings.py", b"ANTHROPIC_API" + b'_KEY = "x"'),
)


def test_every_declared_pattern_is_exercised() -> None:
    """A pattern with no planted example is an untested pattern."""
    declared = {label for label, _ in make_zip.SECRET_PATTERNS}
    exercised = {label for label, _, _ in PLANTED}
    assert declared == exercised, f"unexercised patterns: {declared - exercised}"


@pytest.mark.parametrize("label,member,blob", PLANTED, ids=[p[0] for p in PLANTED])
def test_the_scan_catches_a_planted_secret(
    archive: Path, tmp_path: Path, label: str, member: str, blob: bytes
) -> None:
    tainted = tmp_path / f"tainted-{label.replace(' ', '_')}.zip"
    shutil.copy(archive, tainted)
    with zipfile.ZipFile(tainted, "a") as zf:
        zf.writestr(member, blob)

    findings = make_zip.scan_for_secrets(tainted)
    assert any(member in f for f in findings), f"{label} planted in {member} went undetected"


def test_the_scan_never_echoes_the_secret_it_found(
    archive: Path, tmp_path: Path
) -> None:
    """A finding that quotes the key has published it into the log it was meant
    to keep it out of."""
    secret = b"sk-" + b"ant-api03-" + b"S" * 40
    tainted = tmp_path / "tainted-echo.zip"
    shutil.copy(archive, tainted)
    with zipfile.ZipFile(tainted, "a") as zf:
        zf.writestr("code/leak.txt", secret)

    for finding in make_zip.scan_for_secrets(tainted):
        assert secret.decode() not in finding
        assert "SSSS" not in finding


def test_the_env_example_template_does_not_trip_the_scan(archive: Path) -> None:
    """The waiver is narrow: the template lists variable NAMES with empty values.
    If someone fills one in, the other seven patterns still have to catch it."""
    with zipfile.ZipFile(archive) as zf:
        template = zf.read("code/.env.example")
    assert b"ANTHROPIC_API" + b"_KEY=" in template, "template no longer names the variable"
    assert not make_zip.scan_for_secrets(archive)

    filled = b"ANTHROPIC_API" + b"_KEY=" + b"sk-" + b"ant-api03-" + b"A" * 40
    tainted_body = template.replace(b"ANTHROPIC_API" + b"_KEY=", filled)
    tainted = archive.parent / "tainted-template.zip"
    shutil.copy(archive, tainted)
    with zipfile.ZipFile(tainted, "a") as zf:
        zf.writestr("code/.env.example.filled", tainted_body)
    assert make_zip.scan_for_secrets(tainted), "a filled-in template must still be caught"
