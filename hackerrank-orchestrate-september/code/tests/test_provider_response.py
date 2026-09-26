"""Empty content is never a valid empty result."""

from __future__ import annotations

import pytest

from bow.providers.response import (
    ResponseOutcome,
    classify_response,
)

RETRYABLE = frozenset({429, 500, 502, 503, 504})


def response(content, finish_reason="stop", completion_tokens=10):
    return {
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        "usage": {"completion_tokens": completion_tokens},
    }


def test_content_present_is_usable():
    verdict = classify_response(response('{"deltas": []}'), 200)
    assert verdict.outcome is ResponseOutcome.USABLE
    assert verdict.is_usable
    assert not verdict.must_flag


def test_an_explicit_empty_structure_is_a_real_result():
    """`{"deltas": []}` is the model saying 'nothing found'. That is content."""
    assert classify_response(response('{"deltas": []}'), 200).is_usable


@pytest.mark.parametrize("content", [None, "", "   ", "\n\t "])
def test_empty_content_is_never_a_valid_empty_result(content):
    verdict = classify_response(response(content), 200)
    assert verdict.outcome is ResponseOutcome.EMPTY_CONTENT
    assert not verdict.is_usable
    assert verdict.must_flag


def test_truncated_response_is_retryable_while_attempts_remain():
    verdict = classify_response(
        response("", finish_reason="length", completion_tokens=2048),
        200,
        attempts_remaining=2,
    )
    assert verdict.outcome is ResponseOutcome.EMPTY_CONTENT
    assert verdict.retryable
    assert "truncated response" in verdict.detail.lower()
    assert verdict.finish_reason == "length"
    assert verdict.completion_tokens == 2048


def test_truncated_response_becomes_a_hard_flag_once_attempts_run_out():
    verdict = classify_response(
        response("", finish_reason="length"), 200, attempts_remaining=0
    )
    assert verdict.outcome is ResponseOutcome.EMPTY_CONTENT
    assert not verdict.retryable
    assert verdict.must_flag


def test_the_measured_production_shape_is_caught():
    """Exactly what the provider returned on 37 of 45 non-English messages."""
    measured = {
        "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
        "usage": {"completion_tokens": 2048, "prompt_tokens": 900},
    }
    verdict = classify_response(measured, 200, attempts_remaining=1)
    assert not verdict.is_usable, "a truncated response must never be consumed"
    assert verdict.retryable


def test_missing_choices_is_not_usable():
    assert not classify_response({}, 200).is_usable


@pytest.mark.parametrize("status", [429, 500, 503])
def test_retryable_status_is_transient(status):
    verdict = classify_response({}, status, retryable_status=RETRYABLE, attempts_remaining=1)
    assert verdict.outcome is ResponseOutcome.TRANSIENT_ERROR
    assert verdict.retryable


@pytest.mark.parametrize("status", [400, 401, 404, 422])
def test_non_retryable_status_is_permanent(status):
    verdict = classify_response({}, status, retryable_status=RETRYABLE, attempts_remaining=3)
    assert verdict.outcome is ResponseOutcome.PERMANENT_ERROR
    assert not verdict.retryable


def test_no_outcome_other_than_usable_is_ever_consumable():
    for outcome in ResponseOutcome:
        if outcome is ResponseOutcome.USABLE:
            continue
        verdict = classify_response({}, 400)
        assert verdict.must_flag or verdict.outcome is ResponseOutcome.USABLE
