"""The cost report is generated, and it satisfies AGENTS.md 6.5 clause by clause."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from bow import paths
from bow.settings import load_settings
from bow.usage import build_report, load_pricing, render, write_usage_report

MANIFEST = {
    "rows_written": 4,
    "total_seconds": 12.5,
    "counts": {"requests": 4},
    "unresolved_total": 0,
    "usage": {
        "calls": {"message_extract": 2, "lang_detect": 3, "vision_digitise": 1},
        "prompt_tokens": {"message_extract": 1000},
        "completion_tokens": {"message_extract": 2000},
        "characters": {"lang_detect": 20000},
        "pages": {"vision_digitise": 4},
        "seconds": {"message_extract": 9.0},
    },
}


@pytest.fixture
def pricing():
    return load_pricing(paths.CONFIG_DIR / "pricing.yaml")


@pytest.fixture
def settings():
    return load_settings()


def test_every_priced_unit_is_computed_from_the_manifest(pricing, settings):
    report = build_report(MANIFEST, pricing, settings)
    by_role = {r.role: r for r in report.roles}

    # tokens: 1000/1e6*29.28 + 2000/1e6*73.2
    assert by_role["message_extract"].cost == Decimal("1000") / Decimal(
        "1000000"
    ) * Decimal("29.28") + Decimal("2000") / Decimal("1000000") * Decimal("73.2")
    # characters: 20000/10000*3.50
    assert by_role["lang_detect"].cost == Decimal("7.00")
    # pages: 4/1*0.50
    assert by_role["vision_digitise"].cost == Decimal("2.00")
    assert report.calls == 6
    assert report.input_tokens == 1000
    assert report.output_tokens == 2000
    assert not report.unpriced


def test_a_role_billed_in_a_unit_the_run_did_not_record_is_named_not_zeroed(
    pricing, settings
):
    """Silently costing unmeasured usage at zero would understate the bill."""
    manifest = json.loads(json.dumps(MANIFEST))
    del manifest["usage"]["characters"]
    report = build_report(manifest, pricing, settings)
    assert any("lang_detect" in item for item in report.unpriced)
    assert "lang_detect" not in {r.role for r in report.roles}


def test_the_rendered_report_answers_every_clause_of_section_6_5(pricing, settings):
    text = render(build_report(MANIFEST, pricing, settings), manifest=MANIFEST)
    required = [
        "provider",                       # model providers
        "sarvam-105b",                    # model names
        "model calls",                    # model calls
        "input tokens",                   # input tokens
        "output tokens",                  # output tokens
        "total tokens",                   # total tokens
        "average tokens per request",     # average tokens per request
        "estimated total cost",           # estimated total cost
        "estimated cost per request",     # estimated per-request cost
        "Per-model totals",               # per-model AND overall in one file
        "overall",
    ]
    missing = [clause for clause in required if clause not in text]
    assert not missing, f"usage report does not answer: {missing}"


def test_the_report_carries_no_credential_or_endpoint_secret(pricing, settings):
    text = render(build_report(MANIFEST, pricing, settings), manifest=MANIFEST)
    forbidden = ["SARVAM_API_KEY", "api_key", "api-subscription-key", "Authorization"]
    assert not [f for f in forbidden if f in text]


@pytest.mark.dataset
def test_the_shipped_report_was_generated_and_matches_the_shipped_manifest():
    """Regenerating from the manifest on disk must reproduce the file byte for byte."""
    shipped = (paths.EVAL_DIR / "usage_report.md").read_text(encoding="utf-8")
    regenerated = write_usage_report(
        paths.EVAL_DIR / "run_manifest.json",
        paths.CONFIG_DIR / "pricing.yaml",
        load_settings(),
        paths.CACHE_DIR / "usage_report_check.md",
    ).read_text(encoding="utf-8")
    assert shipped == regenerated, (
        "evaluation/usage_report.md does not match what the shipped manifest "
        "generates; it has been hand-edited or the manifest has moved on"
    )
