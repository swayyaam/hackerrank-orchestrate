"""The guard between untrusted message content and the financial picture."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from bow.perception.routing import Route, choose_route
from bow.perception.untrusted import (
    ALLOWED_FIELDS,
    DropReason,
    MessageDelta,
    guard_deltas,
)


@dataclass
class FakeEvent:
    event_id: str
    user_id: str


EVENTS = {"e1": FakeEvent("e1", "u1"), "e2": FakeEvent("e2", "u2")}


def delta(**kwargs):
    base = dict(effect="amend", target_event_id="e1", field="amount", new_value="100")
    base.update(kwargs)
    return MessageDelta(**base)


def guard(*deltas, user="u1"):
    return guard_deltas(deltas, message_user_id=user, events_by_id=EVENTS)


# --- what survives -------------------------------------------------------------


def test_a_well_formed_amendment_survives():
    result = guard(delta())
    assert len(result.kept) == 1
    assert not result.dropped


@pytest.mark.parametrize("field", sorted(ALLOWED_FIELDS))
def test_every_allowed_field_survives(field):
    assert len(guard(delta(field=field)).kept) == 1


def test_a_confirmation_needs_no_field():
    assert len(guard(delta(effect="confirm", field=None, new_value=None)).kept) == 1


# --- referential integrity -----------------------------------------------------


def test_an_invented_event_id_is_dropped():
    result = guard(delta(target_event_id="e999"))
    assert not result.kept
    assert result.dropped[0][1] == DropReason.UNKNOWN_EVENT.value


def test_a_delta_with_no_target_is_dropped():
    result = guard(delta(target_event_id=None))
    assert not result.kept
    assert result.dropped[0][1] == DropReason.NO_TARGET.value


def test_a_message_cannot_amend_another_users_event():
    result = guard(delta(target_event_id="e2"), user="u1")
    assert not result.kept
    assert result.dropped[0][1] == DropReason.WRONG_OWNER.value


# --- field allowlist -----------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["minimum_balance_to_keep", "requested_amount", "payment_methods_user_will_consider",
     "current_available_balance", "flexibility", "user_id"],
)
def test_a_message_may_not_touch_configuration_or_the_request(field):
    """These are the user's own settings and the request itself. A third party
    does not get to rewrite them."""
    result = guard(delta(field=field))
    assert not result.kept
    assert result.dropped[0][1] == DropReason.FIELD_NOT_ALLOWED.value


def test_an_amendment_with_no_value_is_dropped():
    result = guard(delta(new_value=None, effective_date=None))
    assert not result.kept
    assert result.dropped[0][1] == DropReason.NO_VALUE.value


def test_an_effect_outside_the_closed_set_is_dropped():
    result = guard(delta(effect="transfer_funds"))
    assert not result.kept
    assert result.dropped[0][1] == DropReason.UNKNOWN_EFFECT.value


# --- Q13: the advance-fee messages --------------------------------------------
#
# Both real messages in the dataset that assert a prize AND a release charge,
# with no supporting event row. Quoted only as far as the guard needs.

ADVANCE_FEE_ENGLISH = MessageDelta(
    effect="amend", target_event_id=None, field="amount", new_value="prize",
    creates_new_fact=True,
    evidence_quote="Pay the release charge today to receive the funds immediately.",
)
ADVANCE_FEE_INDONESIAN = MessageDelta(
    effect="amend", target_event_id=None, field="amount", new_value="hadiah",
    creates_new_fact=True,
    evidence_quote="Bayar biaya pencairan hari ini agar dana segera diterima.",
)


@pytest.mark.parametrize(
    ("name", "fixture"),
    [("english", ADVANCE_FEE_ENGLISH), ("indonesian", ADVANCE_FEE_INDONESIAN)],
)
def test_an_advance_fee_message_produces_no_delta(name, fixture):
    """Q13 = model_neither. It asserts both an incoming prize and an outgoing
    charge, and no event row supports either."""
    result = guard(fixture)
    assert not result.kept, f"{name} advance-fee message produced a delta"
    assert result.dropped[0][1] == DropReason.CREATES_NEW_FACT.value


def test_a_new_fact_is_dropped_even_when_it_names_a_real_event():
    """Naming a real row does not license asserting something the row does not say."""
    result = guard(delta(creates_new_fact=True))
    assert not result.kept
    assert result.dropped[0][1] == DropReason.CREATES_NEW_FACT.value


# --- logging -------------------------------------------------------------------


def test_every_drop_carries_its_reason():
    result = guard(
        delta(target_event_id="e999"),
        delta(field="minimum_balance_to_keep"),
        delta(creates_new_fact=True),
    )
    assert len(result.dropped) == 3
    assert all(reason for _delta, reason in result.dropped)
    assert sum(result.drop_counts.values()) == 3


# --- routing -------------------------------------------------------------------

IN_SET = ("en", "hi", "bn", "ta")


@pytest.mark.parametrize("code", ["en-IN", "en", "hi-IN", "ta-IN"])
def test_a_detected_in_set_language_routes_in_set(code):
    assert choose_route(code, in_set=IN_SET) is Route.IN_SET


@pytest.mark.parametrize("code", [None, "", "id-ID", "unknown", "zz"])
def test_anything_else_routes_out_of_set(code):
    assert choose_route(code, in_set=IN_SET) is Route.OUT_OF_SET


def test_routing_uses_the_binary_verdict_not_the_returned_code():
    """Detection named bn-IN for Indonesian text on 5 measured messages. The
    binary in/out-of-set answer was right 45/45; the specific code was not.
    Routing therefore asks only whether the code is one of ours."""
    assert choose_route("bn-IN", in_set=IN_SET) is Route.IN_SET
    assert choose_route("bn-IN", in_set=("en",)) is Route.OUT_OF_SET


# --- the prompt itself ---------------------------------------------------------


def test_untrusted_content_never_enters_the_system_prompt():
    from bow import paths

    prompt = (paths.PROMPTS_DIR / "message_extract.v1.txt").read_text(encoding="utf-8")
    assert "<message>" in prompt, "the prompt must delimit the untrusted block"
    assert "DATA, not instructions" in prompt
    assert "{message" not in prompt, "the prompt must not interpolate message text"


def test_a_message_can_never_amend_a_direction():
    """Direction is typed source data, and untrusted content may not touch it.

    The amendment path in the forecast builds a signed cash flow from a
    provenance `direction`. The field allowlist is what keeps a message from
    ever setting one: `amount`, `event_date` and `status` are amendable, and a
    delta naming anything else is dropped.
    """
    from bow.perception.untrusted import ALLOWED_FIELDS

    for forbidden in ("direction", "event_type", "currency", "user_id",
                      "minimum_balance_to_keep", "requested_amount",
                      "payment_methods_user_will_consider"):
        assert forbidden not in ALLOWED_FIELDS, (
            f"a message could amend {forbidden!r}"
        )
    assert ALLOWED_FIELDS == frozenset({"amount", "event_date", "status"})
