"""Vision: parsing, label priority, reconciliation, and gold-set isolation.

Every test here is offline. The provider call is the only part not exercised,
which is the point: the rule that decides *which figure is the answer* is code,
so it can be verified without spending anything.
"""

from __future__ import annotations

import ast
import json
from decimal import Decimal
from pathlib import Path

import pytest

from _treewalk import PACKAGE_ROOT, SUBMISSION_ROOT, parse, rel
from bow.contracts.observation import DocumentObservation, LabelledFigure, LineItem
from bow.perception.parsing import (
    ParseError,
    infer_decimal_separator,
    parse_amount,
    reconciles,
    select_amount,
)
from bow.perception.vision import build_observation, geometric_pairs, table_pairs
from bow.settings import load_thresholds

GOLD = SUBMISSION_ROOT / "tests" / "fixtures" / "vision_gold.json"


@pytest.fixture(scope="module")
def priority():
    vision = load_thresholds().vision
    return vision.label_priority, vision.label_denylist, vision.reconciliation_tolerance


# --- the gold set is a fixture, not an input ---------------------------------


def test_the_gold_set_exists_and_covers_every_image():
    data = json.loads(GOLD.read_text())["images"]
    assert len(data) == 16
    assert {v["set"] for v in data.values()} == {"sample", "eval"}


def test_the_runtime_path_cannot_read_the_gold_set():
    """No module under bow/ may reference the gold file, by name or by path.

    It is hand-derived ground truth. A runtime path that could read it would be
    scoring itself against its own answer key.
    """
    offenders = []
    needles = ("vision_gold", "fixtures")
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            if needle in text:
                offenders.append(f"{rel(path)} mentions {needle!r}")
    assert not offenders, "the runtime path can see the gold set:\n" + "\n".join(offenders)


def _docstring_nodes(tree):
    """Every string constant that is a docstring, which may mention anything."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                found.add(id(body[0].value))
    return found


def test_no_module_reaches_into_the_tests_directory():
    """No runtime string builds a path into tests/.

    Docstrings are exempt: prose that names a test file is documentation, not a
    path the code can open.
    """
    offenders = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = parse(path)
        docstrings = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if id(node) in docstrings:
                continue
            if "tests" in node.value and ("/" in node.value or "\\" in node.value):
                offenders.append(f"{rel(path)}:{node.lineno} {node.value!r}")
    assert not offenders, "bow/ references a test path:\n" + "\n".join(offenders)


# --- no vendor, layout or image id anywhere in code or prompts ---------------

BANNED = (
    "airtel", "indigo", "icici", "paytm", "grofers", "blink", "citycab",
    "jeevan", "riddhi", "siddhi", "nagarjuna", "dailyobjects", "interglobe",
    "bank central asia", "summit bank", "cedar", "buybox",
    "top-right", "bottom-right", "last column", "rightmost", "right-most",
    "second column", "first row", "last row", "bottom of the page",
    "rent receipt", "pay slip", "payslip", "tax invoice", "boarding pass",
)


@pytest.mark.parametrize("needle", BANNED)
def test_no_vendor_or_layout_knowledge_in_code_or_prompts(needle):
    offenders = []
    roots = [PACKAGE_ROOT, SUBMISSION_ROOT / "prompts", SUBMISSION_ROOT / "config"]
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if path.suffix not in (".py", ".yaml", ".yml", ".txt", ".md", ".json"):
                continue
            if needle in path.read_text(encoding="utf-8", errors="ignore").lower():
                offenders.append(rel(path))
    assert not offenders, f"{needle!r} appears in {offenders}"


def test_no_image_id_literal_in_code_or_prompts():
    import re

    pattern = re.compile(r"\bimage_\d+\b")
    offenders = []
    for root in (PACKAGE_ROOT, SUBMISSION_ROOT / "prompts"):
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            if pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
                offenders.append(rel(path))
    assert not offenders, f"an image id appears in {offenders}"


# --- locale-aware parsing -----------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("$33,50", "33.50"),          # comma as the decimal point
        ("$28.50", "28.50"),          # dot, in the SAME document
        ("1,00,000.00", "100000.00"), # lakh grouping
        ("79,679.26", "79679.26"),
        ("4543", "4543"),
        ("2,298", "2298"),
        ("15,339.00", "15339.00"),
        ("1.234.567,89", "1234567.89"),
    ],
)
def test_separator_is_inferred_per_value(raw, expected):
    assert parse_amount(raw) == Decimal(expected)


def test_a_document_may_mix_separators():
    """The taxi receipt prints $28.50 and $5,00 on adjacent lines."""
    assert parse_amount("$28.50") == Decimal("28.50")
    assert parse_amount("$5,00") == Decimal("5.00")


@pytest.mark.parametrize("raw", ["", "   ", "abc", "-", "$"])
def test_an_unreadable_figure_raises_rather_than_becoming_zero(raw):
    with pytest.raises(ParseError):
        parse_amount(raw)


def test_parsing_never_routes_through_float():
    source = (PACKAGE_ROOT / "perception" / "parsing.py").read_text(encoding="utf-8")
    assert "float(" not in source


# --- table geometry -----------------------------------------------------------


def test_a_row_labelled_table_pairs_by_row():
    html = ("<table><tbody>"
            "<tr><td>Salary</td><td>: IDR</td><td>4,500,000</td></tr>"
            "<tr><td>Net Pay</td><td>: IDR</td><td>4,365,000</td></tr>"
            "</tbody></table>")
    assert ("Net Pay", "4,365,000") in table_pairs(html)


def test_a_column_labelled_table_pairs_by_column_header():
    html = ("<table><thead><tr><th>Description</th><th>Taxable</th>"
            "<th>Total Incl Taxes</th></tr></thead><tbody>"
            "<tr><td>Grand Total</td><td>9,124.00</td><td>9,968.00</td></tr>"
            "</tbody></table>")
    pairs = table_pairs(html)
    assert ("Total Incl Taxes", "9,968.00") in pairs
    assert ("Grand Total", "9,124.00") in pairs


def test_column_pairing_is_skipped_when_a_row_does_not_align_with_the_header():
    """A spanning row shifts every index, and a header paired with the wrong
    column's figure is worse than no pairing."""
    html = ("<table><thead><tr><th>A</th><th>B</th><th>C</th></tr></thead><tbody>"
            "<tr><td>Label</td><td>7</td></tr></tbody></table>")
    pairs = table_pairs(html)
    assert ("C", "7") not in pairs
    assert ("A", "7") not in pairs


# --- block geometry -----------------------------------------------------------


def _block(text, x1, y1, x2, y2, order=1):
    return {
        "text": text, "reading_order": order,
        "coordinates": {"x1": x1, "y1": y1, "x2": x2, "y2": y2},
    }


def test_a_lone_value_takes_the_label_to_its_left():
    blocks = [_block("Total Due", 0, 100, 80, 120), _block("1,250.00", 200, 100, 280, 120)]
    assert ("Total Due", "1,250.00") in geometric_pairs(blocks)


def test_a_lone_value_takes_the_label_directly_above_it():
    blocks = [_block("Balance", 0, 10, 80, 30), _block("990.00", 0, 40, 80, 60)]
    assert ("Balance", "990.00") in geometric_pairs(blocks)


def test_the_nearest_label_wins():
    blocks = [
        _block("Far Away", 0, 100, 40, 120),
        _block("Near", 150, 100, 190, 120),
        _block("42.00", 200, 100, 240, 120),
    ]
    pairs = dict((v, k) for k, v in geometric_pairs(blocks))
    assert pairs["42.00"] == "Near"


# --- label priority -----------------------------------------------------------


def observation(*pairs, words=None):
    return DocumentObservation(
        labelled_figures=tuple(LabelledFigure(label=l, raw_value=v) for l, v in pairs),
        line_items=tuple(LineItem(description=l, raw_amount=v) for l, v in pairs),
        amount_in_words=words,
    )


def test_balance_due_outranks_total(priority):
    order, deny, _ = priority
    amount, label = select_amount(
        observation(("Total Amount to be Received", "2,00,000"), ("Balance Due", "1,00,000")),
        label_priority=order, label_denylist=deny,
    )
    assert amount == Decimal("100000")
    assert "balance" in label.lower()


def test_a_tendered_amount_is_never_chosen(priority):
    order, deny, _ = priority
    amount, _ = select_amount(
        observation(("Cash Paid", "40,00"), ("Total", "33,50")),
        label_priority=order, label_denylist=deny,
    )
    assert amount == Decimal("33.50")


def test_amount_paid_zero_on_an_unpaid_bill_is_never_chosen(priority):
    order, deny, _ = priority
    amount, _ = select_amount(
        observation(("Amount Paid", "0.00"), ("Balance", "3650.00")),
        label_priority=order, label_denylist=deny,
    )
    assert amount == Decimal("3650.00")


def test_the_tightest_label_wins_when_several_match(priority):
    """A garbled capture containing the word must lose to a clean label."""
    order, deny, _ = priority
    amount, label = select_amount(
        observation(
            ("ices Charged Include all Taxes E and O.E Signature TOTAL 4543", "00"),
            ("TOTAL", "4543"),
        ),
        label_priority=order, label_denylist=deny,
    )
    assert amount == Decimal("4543")
    assert label == "TOTAL"


# --- reconciliation -----------------------------------------------------------


def test_line_items_summing_to_the_figure_reconciles(priority):
    _, _, tolerance = priority
    obs = observation(("Item A", "13,880.00"), ("Item B", "1,050.00"), ("Item C", "409.00"))
    ok, _ = reconciles(Decimal("15339.00"), obs, tolerance=tolerance)
    assert ok


def test_amount_in_words_naming_the_figure_reconciles(priority):
    _, _, tolerance = priority
    # Line items that do NOT sum to the figure, so only the words block can
    # confirm it and the test exercises the path it names.
    obs = observation(
        ("Line A", "400.00"), ("Line B", "100.00"),
        words="Rupees Seven Hundred Twenty Three Only",
    )
    ok, why = reconciles(Decimal("723.00"), obs, tolerance=tolerance)
    assert ok and "words" in why


def test_the_line_item_fallback_can_never_verify_itself(priority):
    """Summing the lines and checking the sum against the lines is tautological."""
    _, _, tolerance = priority
    obs = observation(("a", "10"), ("b", "20"))
    ok, why = reconciles(Decimal("30"), obs, tolerance=tolerance, from_line_item_sum=True)
    assert not ok
    assert "not self-verifiable" in why


def test_a_figure_nothing_supports_does_not_reconcile(priority):
    _, _, tolerance = priority
    obs = observation(("Item A", "10.00"), ("Item B", "20.00"))
    ok, _ = reconciles(Decimal("999.00"), obs, tolerance=tolerance)
    assert not ok


# --- fail closed --------------------------------------------------------------


def test_an_empty_document_raises_rather_than_guessing(priority):
    order, deny, _ = priority
    with pytest.raises(ParseError):
        select_amount(DocumentObservation(), label_priority=order, label_denylist=deny)


def test_build_observation_parses_no_numbers():
    """Every value it returns is a string. Parsing happens later, in code."""
    payload = {"documents": [{"pages": [{"blocks": [
        {"text": "Total: 1,234.56", "reading_order": 1,
         "coordinates": {"x1": 0, "y1": 0, "x2": 10, "y2": 10}},
    ]}]}]}
    obs = build_observation(payload)
    assert all(isinstance(f.raw_value, str) for f in obs.labelled_figures)
    assert all(isinstance(i.raw_amount, str) for i in obs.line_items)


# --- direction is not the vision layer's to assert ----------------------------
#
# Two of the sixteen documents print their total as "Total Amount Received",
# which is the biller's side of the transaction: the user paid, the society and
# the water board received. Both events are typed `expense` / `debit`. These
# tests hold the line that makes that wording harmless.


def figures(*pairs, words=None):
    """A document with labelled figures ONLY — no line items to fall back on.

    The shared `observation()` helper mirrors every pair into `line_items`, so a
    label that matches nothing still produces the same number via the fallback.
    That would make a wording test pass without proving anything.
    """
    return DocumentObservation(
        labelled_figures=tuple(LabelledFigure(label=l, raw_value=v) for l, v in pairs),
        amount_in_words=words,
    )


def test_the_extraction_contract_cannot_express_a_direction():
    """No field in any vision contract can carry a sign, side or polarity."""
    forbidden = {
        "direction", "sign", "signed", "credit", "debit", "polarity", "inflow",
        "outflow", "is_credit", "is_debit", "side", "type",
    }
    for model in (DocumentObservation, LabelledFigure, LineItem):
        overlap = forbidden & set(model.model_fields)
        assert not overlap, f"{model.__name__} can express a direction: {overlap}"


@pytest.mark.parametrize(
    "wording",
    ["Total Amount Received", "Total Paid", "Grand Total", "Total", "Net Amount"],
)
def test_biller_side_wording_does_not_change_the_extracted_amount(priority, wording):
    """Identical documents differing only in that wording extract the same amount.

    "Received" is the biller's word for the same event the payer calls "paid".
    Which of the two a document happens to print must change nothing but the
    label recorded in the evidence trail.
    """
    order, deny, _ = priority
    amount, label = select_amount(
        figures((wording, "15,339.00")), label_priority=order, label_denylist=deny
    )
    assert amount == Decimal("15339.00")
    assert label == wording  # the wording travels as evidence, and nowhere else


def test_every_priority_wording_extracts_the_same_figure(priority):
    """The whole priority list, one figure, one answer."""
    order, deny, _ = priority
    amounts = {
        select_amount(figures((phrase, "723.00")),
                      label_priority=order, label_denylist=deny)[0]
        for phrase in order
        if phrase != "line_item_sum"
    }
    assert amounts == {Decimal("723.00")}, (
        f"a priority wording changed the extracted amount: {amounts}"
    )


@pytest.mark.parametrize("printed", ["-15,339.00", "- 15,339.00", "−15,339.00"])
def test_a_selected_figure_is_always_a_magnitude(priority, printed):
    """A minus glyph on the page is a direction claim, and is discarded.

    `CashFlow.signed` negates a DEBIT, so a negative amount would invert one
    cash flow: an expense would add to the balance instead of reducing it.
    """
    order, deny, _ = priority
    amount, _ = select_amount(
        figures(("Total Amount Received", printed)),
        label_priority=order, label_denylist=deny,
    )
    assert amount == Decimal("15339.00")
    assert amount > 0


# --- the priority ordering itself ---------------------------------------------


def test_the_priority_ordering_is_pinned(priority):
    """A future edit cannot silently reorder the list.

    Only the relationships that carry meaning are asserted, not the whole list
    verbatim: adding a new wording should be easy, moving a paid-style label
    above an obligation label should not.
    """
    order, deny, _ = priority
    rank = {phrase: index for index, phrase in enumerate(order)}

    obligations = ("balance due", "amount due", "amount payable")
    paid_style = ("total paid", "total amount received")
    for phrase in obligations + paid_style + ("line_item_sum",):
        assert phrase in rank, f"{phrase!r} has left the priority list"

    # An outstanding obligation outranks a figure describing what has been
    # settled: on an unpaid bill the paid-to-date figure can be zero.
    for obligation in obligations:
        for paid in paid_style:
            assert rank[obligation] < rank[paid], (
                f"{paid!r} now outranks {obligation!r}; a zero paid-to-date line "
                f"can beat a real balance due"
            )

    # Summing the lines yourself is the last resort, always.
    assert rank["line_item_sum"] == len(order) - 1
    assert all(rank["subtotal"] > rank[p] for p in ("grand total", "total"))


def test_tendered_and_paid_wordings_cannot_be_selected_on_their_own_label(priority):
    """A tendered or paid-to-date figure is never the cost. Q12."""
    order, deny, _ = priority
    for wording in ("Amount Tendered", "Cash Paid", "Amount Paid", "Change"):
        with pytest.raises(ParseError):
            select_amount(
                figures((wording, "20,000.00")),
                label_priority=order, label_denylist=deny,
            )


def test_a_zero_paid_line_never_beats_a_real_balance_due(priority):
    """The case the denylist and the ordering exist for."""
    order, deny, _ = priority
    amount, label = select_amount(
        figures(("Amount Paid", "0.00"), ("Balance Due", "79,679.26")),
        label_priority=order, label_denylist=deny,
    )
    assert amount == Decimal("79679.26")
    assert "balance" in label.lower()


def test_a_vision_evidence_entry_can_never_become_a_cash_flow():
    """The sign path reads only AMEND entries; vision writes EXTRACT.

    `_amendment_flows` is the one place an evidence entry can turn into a
    movement of money with a direction attached. It requires `effect=AMEND` and
    a `direction` in provenance. Vision writes `effect=EXTRACT` and a provenance
    dict with no direction key, so both gates are closed — this asserts the
    first gate holds even if the second were ever breached.
    """
    from datetime import date as _date

    from bow.contracts.enums import EvidenceEffect
    from bow.contracts.evidence import EvidenceEntry, EvidenceLedger
    from bow.engine.forecast import _amendment_flows

    # Deliberately hostile: an EXTRACT entry carrying exactly the keys the
    # amendment path looks for. It must still produce nothing.
    hostile = EvidenceEntry(
        source_type="image", source_id="img", field="amount",
        raw_value="500", normalized_value=Decimal("500"),
        effect=EvidenceEffect.EXTRACT,
        provenance={"cash_date": _date(2026, 1, 1), "direction": "credit"},
    )
    ledger = EvidenceLedger(request_id="r1", entries=(hostile,))
    assert _amendment_flows(ledger) == []


def test_the_vision_provenance_carries_no_direction_key():
    """What resolve.py actually attaches to an extracted amount."""
    import ast

    source = (PACKAGE_ROOT / "perception" / "resolve.py").read_text(encoding="utf-8")
    keys = {
        node.value
        for dict_node in ast.walk(ast.parse(source))
        if isinstance(dict_node, ast.Dict)
        for node in dict_node.keys
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    for forbidden in ("direction", "cash_date", "sign", "credit", "debit"):
        assert forbidden not in keys, (
            f"resolve.py now writes a {forbidden!r} key; a vision read could "
            f"reach the amendment path and assert a direction"
        )


def test_the_best_of_n_config_matches_what_the_code_does():
    """No config value may promise behaviour the pipeline does not implement.

    `reads_per_amount` and `read_disagreement_rule` were fixed in
    `config/thresholds.yaml` BEFORE the stability probe ran, so the rule could
    not be chosen after seeing which answer it picks. The probe found one
    unstable image out of sixteen, which is below the threshold recorded in that
    comment, so best-of-N was not adopted and nothing reads the value.

    That makes it dead config, and dead config that looks live is a trap: a
    future edit could set it to 3 and believe reads are being repeated. This
    fails if anyone does.
    """
    vision = load_thresholds().vision
    assert vision.reads_per_amount == 1, (
        "reads_per_amount is above 1, but no code path repeats a document read. "
        "Implement best-of-N in bow/perception/resolve.py before raising it, or "
        "the setting silently does nothing."
    )
    assert vision.read_disagreement_rule == "majority_then_reconciled", (
        "the disagreement rule was fixed in advance; changing it after the "
        "measurement is exactly the thing it exists to prevent"
    )


# --- reconciliation-driven substitution ---------------------------------------
#
# THE RULE, stated before it was implemented and before any image was re-scored:
# when the figure chosen by label priority does not reconcile, and the line-item
# sum equals a number printed somewhere in the document, take that sum as the
# amount and record the substitution in provenance.
#
# It is the same verify-then-accept discipline the rest of this module uses. The
# "printed somewhere in the document" clause is what stops it inventing a total:
# a sum the document never states is not evidence, it is arithmetic we did.


def test_a_non_reconciling_selection_yields_to_the_line_item_sum():
    """The rule's positive case."""
    from bow.perception.parsing import reconciling_substitute

    doc = DocumentObservation(
        labelled_figures=(LabelledFigure(label="Total", raw_value="670"),),
        line_items=(
            LineItem(description="a", raw_amount="1500"),
            LineItem(description="b", raw_amount="724"),
            LineItem(description="c", raw_amount="796"),
            LineItem(description="d", raw_amount="550"),
            LineItem(description="e", raw_amount="303"),
            LineItem(description="", raw_amount="670"),
        ),
        source_text="a 1500\nb 724\nc 796\nd 550\ne 303\n670\nTOTAL 4543",
    )
    got = reconciling_substitute(doc, tolerance=Decimal("1.00"))
    assert got == Decimal("4543")


def test_a_sum_the_document_never_prints_is_not_substituted():
    """The clause that stops the rule inventing a total.

    Same lines, but the document states no figure equal to their sum. Arithmetic
    we did is not a figure the document asserts, so nothing is substituted.
    """
    from bow.perception.parsing import reconciling_substitute

    doc = DocumentObservation(
        labelled_figures=(LabelledFigure(label="Total", raw_value="670"),),
        line_items=(
            LineItem(description="a", raw_amount="1500"),
            LineItem(description="b", raw_amount="724"),
        ),
        source_text="a 1500\nb 724\nTotal 670",
    )
    assert reconciling_substitute(doc, tolerance=Decimal("1.00")) is None


def test_nothing_is_substituted_when_there_are_no_line_items():
    from bow.perception.parsing import reconciling_substitute

    doc = DocumentObservation(
        labelled_figures=(LabelledFigure(label="Total", raw_value="670"),),
        source_text="Total 670",
    )
    assert reconciling_substitute(doc, tolerance=Decimal("1.00")) is None


def test_the_substituted_sum_is_a_magnitude():
    """The L10 rule holds on this path too."""
    from bow.perception.parsing import reconciling_substitute

    doc = DocumentObservation(
        line_items=(
            LineItem(description="a", raw_amount="-1500"),
            LineItem(description="b", raw_amount="-500"),
        ),
        source_text="a -1500\nb -500\nTotal -2000",
    )
    got = reconciling_substitute(doc, tolerance=Decimal("1.00"))
    assert got is None or got >= 0


def test_an_unlabelled_numeric_table_row_still_counts_toward_the_sum():
    """A row carrying a figure and no description is a line item all the same.

    Dropping it is what made the sum miss its own total: five described rows
    summed, the sixth undescribed row did not, and the arithmetic could never
    close.
    """
    from bow.perception.vision import table_pairs

    html = (
        "<table><tbody>"
        "<tr><td>1</td><td>Item A</td><td>1500</td></tr>"
        "<tr><td></td><td></td><td>670</td></tr>"
        "</tbody></table>"
    )
    pairs = table_pairs(html)
    assert ("Item A", "1500") in pairs
    assert ("", "670") in pairs, "the undescribed row's figure was dropped"


def test_an_unlabelled_figure_can_never_be_selected_by_label_priority(priority):
    """It counts toward the sum; it must not be selectable as the answer."""
    order, deny, _ = priority
    doc = DocumentObservation(
        labelled_figures=(
            LabelledFigure(label="", raw_value="99999"),
            LabelledFigure(label="Grand Total", raw_value="1995.00"),
        ),
    )
    amount, label = select_amount(doc, label_priority=order, label_denylist=deny)
    assert amount == Decimal("1995.00")
    assert label == "Grand Total"


def test_a_quantity_column_is_not_counted_as_a_line_item():
    """A figure standing before its row's description is a count, not an amount.

    Sweeping in every numeric cell to catch the undescribed row also catches the
    quantity column, and the sum comes out high by exactly the quantities. The
    row's own structure tells them apart: a described row's leading number is a
    quantity; an undescribed row's number is all the row has.
    """
    from bow.perception.vision import table_pairs

    html = (
        "<table><tbody>"
        "<tr><td>2</td><td>Item A</td><td>1500</td><td>00</td></tr>"
        "<tr><td>1</td><td>Item B</td><td>724</td><td>00</td></tr>"
        "<tr><td></td><td></td><td>670</td><td>00</td></tr>"
        "</tbody></table>"
    )
    pairs = table_pairs(html)
    values = [v for _label, v in pairs]
    assert values.count("2") == 0 and values.count("1") == 0, (
        f"a quantity column reached the line items: {pairs}"
    )
    assert ("Item A", "1500") in pairs
    assert ("", "670") in pairs
    total = sum(Decimal(v) for v in values)
    assert total == Decimal("2894"), f"line-item sum is wrong: {total}"
