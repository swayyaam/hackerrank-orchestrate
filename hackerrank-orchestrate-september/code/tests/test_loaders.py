"""Loader behaviour, on hand-built CSVs. Nothing here reads ``dataset/``."""

from __future__ import annotations

import ast
from datetime import date, timezone
from decimal import Decimal

import pytest

from _treewalk import PACKAGE_ROOT, parse, rel
from bow.contracts import Flexibility, PaymentMethod
from bow.data.loaders import (
    SchemaViolationError,
    load_events,
    load_messages,
    load_profiles,
    load_requests,
)

EVENT_HEADER = (
    "event_id,user_id,event_type,description,category,direction,amount,currency,"
    "event_date,settlement_date,status,linked_event_id,flexibility,"
    "minimum_allowed_amount"
)


def write(tmp_path, name: str, *lines: str):
    path = tmp_path / name
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def event_csv(tmp_path, *rows: str):
    return write(tmp_path, "financial_events.csv", EVENT_HEADER, *rows)


# --- money --------------------------------------------------------------------


def test_amounts_are_decimal_never_float(tmp_path):
    path = event_csv(
        tmp_path,
        "e1,u1,expense,d,groceries,debit,0.1,INR,2026-01-01,2026-01-01,settled,,fixed,",
        "e2,u1,expense,d,groceries,debit,0.2,INR,2026-01-02,2026-01-02,settled,,fixed,",
    )
    events, _ = load_events(path)
    assert all(isinstance(e.amount, Decimal) for e in events)
    # The whole point: 0.1 + 0.2 is exact here, and would not be via float.
    assert events[0].amount + events[1].amount == Decimal("0.3")


def test_high_precision_amount_is_not_rounded_on_the_way_in(tmp_path):
    path = event_csv(
        tmp_path,
        "e1,u1,expense,d,groceries,debit,1163530.49,IDR,2026-01-01,2026-01-01,settled,,fixed,",
    )
    events, _ = load_events(path)
    assert events[0].amount == Decimal("1163530.49")
    assert str(events[0].amount) == "1163530.49"


def test_blank_amount_loads_as_none_never_zero(tmp_path):
    path = event_csv(
        tmp_path,
        "e1,u1,expense,d,groceries,debit,,INR,2026-01-01,2026-01-01,settled,,fixed,",
    )
    events, _ = load_events(path)
    assert events[0].amount is None
    assert events[0].amount != Decimal(0)
    assert not events[0].has_amount


# --- explicit parsing ---------------------------------------------------------


def test_allows_partial_payment_is_parsed_explicitly(tmp_path):
    header = (
        "request_id,user_id,request_date,request_type,requested_amount,"
        "desired_completion_date,allows_partial_payment,request_text"
    )
    path = write(
        tmp_path,
        "requests.csv",
        header,
        "r1,u1,2026-01-01,purchase,100,2026-02-01,true,text",
        "r2,u1,2026-01-01,purchase,100,2026-02-01,false,text",
    )
    requests, _ = load_requests(path)
    assert [r.allows_partial_payment for r in requests] == [True, False]
    assert all(isinstance(r.allows_partial_payment, bool) for r in requests)


@pytest.mark.parametrize("value", ["True", "FALSE", "1", "0", "yes", ""])
def test_non_lowercase_boolean_is_rejected(tmp_path, value):
    header = (
        "request_id,user_id,request_date,request_type,requested_amount,"
        "desired_completion_date,allows_partial_payment,request_text"
    )
    path = write(
        tmp_path,
        "requests.csv",
        header,
        f"r1,u1,2026-01-01,purchase,100,2026-02-01,{value},text",
    )
    with pytest.raises(SchemaViolationError):
        load_requests(path)


def test_pipe_lists_split_into_tuples(tmp_path):
    header = (
        "user_id,home_currency,current_available_balance,minimum_balance_to_keep,"
        "financial_priorities,expense_categories_to_protect,"
        "expense_categories_user_is_willing_to_reduce,"
        "expense_categories_user_is_willing_to_stop,"
        "payment_methods_user_will_consider,max_installment_months"
    )
    path = write(
        tmp_path,
        "financial_profiles.csv",
        header,
        "u1,INR,1000,100,education|travel,rent|groceries,dining,,"
        "full_payment|installments,6",
    )
    profiles, _ = load_profiles(path)
    profile = profiles[0]
    assert profile.financial_priorities == ("education", "travel")
    assert profile.expense_categories_to_protect == ("rent", "groceries")
    # A blank list column is an empty tuple, not a tuple containing "".
    assert profile.expense_categories_user_is_willing_to_stop == ()
    assert profile.payment_methods_user_will_consider == (
        PaymentMethod.FULL_PAYMENT,
        PaymentMethod.INSTALLMENTS,
    )
    assert profile.max_installment_months == 6


def test_blank_max_installment_months_is_none_not_zero(tmp_path):
    header = (
        "user_id,home_currency,current_available_balance,minimum_balance_to_keep,"
        "financial_priorities,expense_categories_to_protect,"
        "expense_categories_user_is_willing_to_reduce,"
        "expense_categories_user_is_willing_to_stop,"
        "payment_methods_user_will_consider,max_installment_months"
    )
    path = write(
        tmp_path,
        "financial_profiles.csv",
        header,
        "u1,INR,1000,100,education,rent,dining,,full_payment,",
    )
    profiles, _ = load_profiles(path)
    assert profiles[0].max_installment_months is None
    assert not profiles[0].considers_installments


def test_sent_at_is_timezone_aware_and_not_a_date(tmp_path):
    header = "message_id,user_id,request_id,related_event_id,sent_at,source_type,message_text"
    path = write(
        tmp_path,
        "messages.csv",
        header,
        "m1,u1,,,2026-01-05T09:30:00Z,employer,some text",
    )
    messages, _ = load_messages(path)
    sent = messages[0].sent_at
    assert sent.tzinfo is not None
    assert sent.utcoffset() == timezone.utc.utcoffset(None)
    assert not isinstance(messages[0].sent_at, date) or hasattr(sent, "hour")
    assert messages[0].request_id is None
    assert messages[0].related_event_id is None


# --- enums --------------------------------------------------------------------


def test_unknown_flexibility_raises(tmp_path):
    path = event_csv(
        tmp_path,
        "e1,u1,expense,d,groceries,debit,100,INR,2026-01-01,2026-01-01,settled,,semi_fixed,",
    )
    with pytest.raises(SchemaViolationError) as caught:
        load_events(path)
    assert "flexibility" in str(caught.value)
    assert "semi_fixed" in str(caught.value)


def test_unknown_flexibility_is_never_coerced_to_fixed(tmp_path):
    """An unrecognised flexibility silently read as fixed would change which
    spending changes are legal. It must raise instead."""
    path = event_csv(
        tmp_path,
        "e1,u1,expense,d,groceries,debit,100,INR,2026-01-01,2026-01-01,settled,,,",
    )
    with pytest.raises(SchemaViolationError):
        load_events(path)
    assert Flexibility.FIXED.value == "fixed"


def test_three_enum_violations_raise_once_naming_all_three(tmp_path):
    path = event_csv(
        tmp_path,
        "e1,u1,expense,d,groceries,debit,100,INR,2026-01-01,2026-01-01,settled,,fixed,",
        "e2,u1,teleport,d,groceries,debit,100,INR,2026-01-01,2026-01-01,settled,,fixed,",
        "e3,u1,expense,d,groceries,sideways,100,INR,2026-01-01,2026-01-01,settled,,fixed,",
        "e4,u1,expense,d,groceries,debit,100,INR,2026-01-01,2026-01-01,perhaps,,fixed,",
    )
    with pytest.raises(SchemaViolationError) as caught:
        load_events(path)

    error = caught.value
    assert len(error.violations) == 3, "must not die on the first bad row"
    located = {(v.row_index, v.column, v.value) for v in error.violations}
    assert located == {
        (2, "event_type", "teleport"),
        (3, "direction", "sideways"),
        (4, "status", "perhaps"),
    }
    message = str(error)
    for token in ("teleport", "sideways", "perhaps", "financial_events.csv", "e2", "e3", "e4"):
        assert token in message


def test_violation_names_file_row_column_and_value(tmp_path):
    path = event_csv(
        tmp_path,
        "e1,u1,expense,d,groceries,debit,not-a-number,INR,2026-01-01,2026-01-01,settled,,fixed,",
    )
    with pytest.raises(SchemaViolationError) as caught:
        load_events(path)
    violation = caught.value.violations[0]
    assert violation.file == "financial_events.csv"
    assert violation.row_index == 1
    assert violation.row_id == "e1"
    assert violation.column == "amount"
    assert violation.value == "not-a-number"


def test_unknown_column_is_rejected(tmp_path):
    path = write(
        tmp_path,
        "financial_events.csv",
        EVENT_HEADER + ",surprise_column",
        "e1,u1,expense,d,groceries,debit,100,INR,2026-01-01,2026-01-01,settled,,fixed,,x",
    )
    with pytest.raises(SchemaViolationError) as caught:
        load_events(path)
    assert "surprise_column" in str(caught.value)


# --- report -------------------------------------------------------------------


def test_row_and_null_counts_reach_the_report(tmp_path):
    path = event_csv(
        tmp_path,
        "e1,u1,expense,d,groceries,debit,100,INR,2026-01-01,2026-01-01,settled,,fixed,",
        "e2,u1,expense,d,groceries,debit,,INR,2026-01-02,,settled,,fixed,",
        "e3,u1,expense,d,groceries,debit,50,INR,2026-01-03,2026-01-03,settled,e1,fixed,25",
    )
    _, stats = load_events(path)
    assert stats.file == "financial_events.csv"
    assert stats.rows == 3
    assert stats.null_counts["amount"] == 1
    assert stats.null_counts["settlement_date"] == 1
    assert stats.null_counts["linked_event_id"] == 2
    assert stats.null_counts["minimum_allowed_amount"] == 2
    assert stats.null_counts["event_id"] == 0


def test_missing_file_raises_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_events(tmp_path / "nope.csv")


# --- the money path is float-free ---------------------------------------------


#: Modules that carry money. Config modules legitimately use floats for
#: timeouts and backoff seconds, which are not money and never become money.
MONEY_PATH = ("contracts/dataset.py", "contracts/scalars.py", "contracts/evidence.py")
MONEY_PACKAGES = ("data", "engine")


def _in_money_path(path) -> bool:
    relative = path.relative_to(PACKAGE_ROOT).as_posix()
    return relative in MONEY_PATH or relative.split("/", 1)[0] in MONEY_PACKAGES


def test_no_float_in_the_money_path():
    """No float may appear in a module that carries money.

    A money value that has been through binary floating point is already wrong,
    and silently so. The one permitted use of the name is an ``isinstance``
    guard, which exists precisely to keep floats out.
    """
    offenders: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts or not _in_money_path(path):
            continue
        tree = parse(path)
        guarded = {
            id(argument)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "isinstance"
            for argument in ast.walk(node)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                offenders.append(f"{rel(path)}:{node.lineno} float literal")
            elif (
                isinstance(node, ast.Name)
                and node.id == "float"
                and id(node) not in guarded
            ):
                offenders.append(f"{rel(path)}:{node.lineno} references float")
    assert not offenders, "float in the money path:\n" + "\n".join(offenders)


def test_the_float_check_is_actually_looking_at_something():
    """Guard against the scope filter silently matching no files."""
    inspected = [
        p for p in PACKAGE_ROOT.rglob("*.py")
        if "__pycache__" not in p.parts and _in_money_path(p)
    ]
    assert len(inspected) >= len(MONEY_PATH), f"money path matched only {inspected}"


def test_money_type_refuses_a_float_outright():
    from bow.contracts.scalars import MoneyError, parse_money

    assert parse_money("1163530.49") == Decimal("1163530.49")
    assert parse_money(5) == Decimal(5)
    with pytest.raises(MoneyError):
        parse_money(0.1)
    with pytest.raises(MoneyError):
        parse_money(True)
    with pytest.raises(MoneyError):
        parse_money("")
