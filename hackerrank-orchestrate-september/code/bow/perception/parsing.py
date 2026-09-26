"""Locale-aware number parsing, and the label-priority rule.

Pure: no network, no model. Everything here is testable offline, which is what
lets the rule that decides *which figure is the answer* be verified without
spending a single API call.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from bow.contracts.observation import DocumentObservation, LabelledFigure

__all__ = [
    "ParseError",
    "infer_decimal_separator",
    "parse_amount",
    "reconciles",
    "reconciling_substitute",
    "select_amount",
]

#: Any numeric run printed anywhere in the document.
_PRINTED_NUMBER = re.compile(r"-?\d[\d.,]*\d|\d")

_DIGITS = re.compile(r"[0-9]")
_NOT_NUMERIC = re.compile(r"[^0-9.,\-]")
#: A group of exactly two digits after the last separator, with no other
#: fractional marker, is the shape of a decimal fraction rather than a grouping.
_TRAILING_PAIR = re.compile(r"^-?\d{1,3}(?:([.,])\d{3})*([.,])\d{1,2}$")


class ParseError(ValueError):
    """A glyph string could not be read as a number. Never silently zero."""


def infer_decimal_separator(raw: str) -> str | None:
    """Which glyph separates the fraction, judged from the string's own shape.

    Generic, not locale-tabled. If both separators appear, the **last** one is
    the decimal point, because grouping separators always precede it. If only
    one appears and it is followed by exactly three digits it is a grouping
    separator; one or two digits make it a decimal point.
    """
    cleaned = _NOT_NUMERIC.sub("", raw)
    dots, commas = cleaned.count("."), cleaned.count(",")
    if dots and commas:
        return "." if cleaned.rfind(".") > cleaned.rfind(",") else ","
    marker = "." if dots else ("," if commas else None)
    if marker is None:
        return None
    tail = cleaned.rsplit(marker, 1)[-1]
    if len(tail) == 3 and cleaned.count(marker) >= 1 and len(cleaned.split(marker)) > 2:
        return None  # repeated 3-digit groups: grouping, not a decimal point
    if len(tail) == 3:
        return None
    return marker


def parse_amount(raw: str, decimal_separator: str | None = None) -> Decimal:
    """Turn printed glyphs into a ``Decimal`` using the reported separator.

    Never routes through ``float``, and never guesses silently: an unreadable
    string raises rather than becoming zero.
    """
    if raw is None:
        raise ParseError("no value")
    cleaned = _NOT_NUMERIC.sub("", str(raw)).strip()
    if not cleaned or not _DIGITS.search(cleaned):
        raise ParseError(f"no digits in {raw!r}")
    separator = decimal_separator or infer_decimal_separator(cleaned)
    if separator is None:
        normalised = cleaned.replace(",", "").replace(".", "")
    else:
        grouping = "," if separator == "." else "."
        normalised = cleaned.replace(grouping, "").replace(separator, ".")
    try:
        return Decimal(normalised)
    except InvalidOperation as exc:
        raise ParseError(f"not a number: {raw!r}") from exc


def _normalise_label(label: str) -> str:
    return re.sub(r"[^a-z ]", " ", label.lower()).strip()
    

def _matches(label: str, phrase: str) -> bool:
    return phrase in _normalise_label(label).replace("  ", " ")


def select_amount(
    observation: DocumentObservation,
    *,
    label_priority: tuple[str, ...],
    label_denylist: tuple[str, ...],
) -> tuple[Decimal, str]:
    """The figure the document says was owed or paid, and the label that said so.

    Walks the configured priority list in order and takes the first labelled
    figure that matches and is not on the denylist. Falls back to summing the
    line items only when the priority list is exhausted.

    **The selected figure is a MAGNITUDE.** A sign glyph printed beside it is
    discarded, deliberately. Whether a movement of money is a credit or a debit
    comes from the financial event's typed ``direction`` field and from nothing
    a document says; a minus on a receipt is the document asserting a direction,
    which is not this layer's to assert. Letting one through would flip the sign
    downstream, because ``CashFlow.signed`` negates a DEBIT and so would turn a
    negative debit into an inflow.

    The line-item fallback is NOT forced positive: a set of lines that sums
    below zero has not produced a usable total, and the caller refuses it rather
    than taking its absolute value and pretending it had.
    """
    # Per value, not per document. See DocumentObservation.decimal_separator.
    denied = tuple(d.lower() for d in label_denylist)

    for phrase in label_priority:
        if phrase == "line_item_sum":
            break
        matched = [
            figure
            for figure in observation.labelled_figures
            if _matches(figure.label, phrase)
            and not any(_matches(figure.label, bad) for bad in denied)
        ]
        if not matched:
            continue
        # Several figures can match the same phrase when a label is garbled or
        # a run of text was captured with it. Prefer the tightest label: the one
        # closest in length to the phrase itself is the one that actually names
        # the figure, rather than a line that merely contains the word.
        best = min(matched, key=lambda f: (len(_normalise_label(f.label)), f.label))
        # abs(): magnitude only. See the docstring — direction is the
        # event's typed field, never a glyph on the page.
        return abs(parse_amount(best.raw_value)), best.label

    if observation.line_items:
        total = sum(
            (parse_amount(item.raw_amount) for item in observation.line_items),
            Decimal(),
        )
        return total, "line_item_sum"
    raise ParseError("no labelled figure matched and no line items to sum")


def reconciles(
    candidate: Decimal,
    observation: DocumentObservation,
    *,
    tolerance: Decimal,
    from_line_item_sum: bool = False,
) -> tuple[bool, str]:
    """Does the chosen figure agree with the document's own components?

    Two independent checks, either of which is enough:

    * the line items sum to it, within tolerance
    * the amount-in-words block names it

    A document offering neither cannot be reconciled, which is reported rather
    than waved through.
    """
    if from_line_item_sum:
        # The fallback cannot verify itself. Summing the lines and then checking
        # that sum against the same lines is tautological, so a figure produced
        # that way is never reconciled and always escalates to a second read.
        return False, "figure came from the line-item fallback; not self-verifiable"

    if observation.line_items:
        try:
            total = sum(
                (parse_amount(i.raw_amount) for i in observation.line_items),
                Decimal(),
            )
        except ParseError:
            total = None
        if total is not None and abs(total - candidate) <= tolerance:
            return True, "line items sum to the chosen figure"

    words = (observation.amount_in_words or "").lower()
    if words:
        spoken = _words_to_decimal(words)
        if spoken is not None and abs(spoken - candidate) <= tolerance:
            return True, "amount in words names the chosen figure"

    return False, "no component check closed"


def _printed_numbers(observation: DocumentObservation) -> list[Decimal]:
    """Every number the document actually prints, wherever it prints it.

    Both the transcribed text and the values of labelled figures, because a
    figure merged into a label's own cell appears only in the text.
    """
    seen: list[Decimal] = []
    for raw in _PRINTED_NUMBER.findall(observation.source_text or ""):
        try:
            seen.append(parse_amount(raw))
        except ParseError:
            continue
    for figure in observation.labelled_figures:
        try:
            seen.append(parse_amount(figure.raw_value))
        except ParseError:
            continue
    return seen


def reconciling_substitute(
    observation: DocumentObservation, *, tolerance: Decimal
) -> Decimal | None:
    """The line-item sum, but only when the document itself prints that figure.

    THE RULE: when the figure chosen by label priority does not reconcile, and
    the line-item sum equals a number printed somewhere in the document, that sum
    is the amount.

    Both halves matter. The sum on its own is arithmetic *we* did, and adopting
    it would be inventing a total the document never states — the same thing the
    line-item fallback is refused for. Requiring the document to print the sum
    somewhere turns it into a figure the document asserts and we verified, which
    is the verify-then-accept discipline used everywhere else here.

    Returns ``None`` when there is nothing to substitute: no line items, an
    unparseable one, a sum at or below zero, or a sum the document never prints.
    """
    if not observation.line_items:
        return None
    try:
        total = sum(
            (parse_amount(item.raw_amount) for item in observation.line_items),
            Decimal(),
        )
    except ParseError:
        return None
    if total <= 0:
        # A magnitude, like every other amount this module returns. See the
        # select_amount docstring: direction is the event's typed field.
        return None
    for printed in _printed_numbers(observation):
        if abs(printed - total) <= tolerance:
            return total
    return None


_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
    "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}
_SCALES = {"hundred": 100, "thousand": 1000, "lakh": 100000, "million": 1000000,
           "crore": 10000000}


def _words_to_decimal(text: str) -> Decimal | None:
    """Read an English amount-in-words block. Returns ``None`` when it cannot.

    Deliberately simple and deliberately fallible: it is a cross-check, so a
    failure to read it means "unverified", never "wrong".
    """
    words = re.findall(r"[a-z]+", text.replace("-", " "))
    if not words:
        return None
    total = current = 0
    seen = False
    for word in words:
        if word in _UNITS:
            current += _UNITS[word]
            seen = True
        elif word in _SCALES:
            scale = _SCALES[word]
            if scale == 100:
                current = max(current, 1) * scale
            else:
                total += max(current, 1) * scale
                current = 0
            seen = True
        elif word in ("and", "only", "rupees", "rupee", "paise", "paisa", "rupiahs"):
            continue
        elif seen and word not in _UNITS:
            break
    return Decimal(total + current) if seen else None
