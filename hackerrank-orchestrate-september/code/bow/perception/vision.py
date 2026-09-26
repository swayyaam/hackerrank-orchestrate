"""Reading an amount off a document.

The division of labour is the point:

**The model transcribes.** One ``digitise`` read returns positioned text. The
observation built from it carries glyph strings and a locale, never a number.

**Code decides.** Which labelled figure is the answer comes from an ordered
priority list in config; the arithmetic reconciliation against the line items
and the amount-in-words block happens here; the ``Decimal`` parsing happens here
using the separator the document itself shows.

A second, schema-constrained ``extract`` read is made **only** when the
arithmetic does not close.

Fail closed. A reconciliation failure, a ``partially_completed`` job, or an
unparseable figure produces an unresolved flag rather than a guess, and the
affected row is still written and marked in the run report.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from bow.contracts.observation import (
    DocumentObservation,
    LabelledFigure,
    LineItem,
    ReadPath,
)
from bow.perception.parsing import (
    ParseError,
    infer_decimal_separator,
    parse_amount,
    reconciles,
    reconciling_substitute,
    select_amount,
)

__all__ = ["VisionOutcome", "build_observation", "read_amount"]

#: A numeric token: digits with optional separators, optionally signed.
_NUMBER = re.compile(r"-?\d[\d.,]*\d|\d")
_CURRENCY_GLYPH = re.compile(r"[₹$€£¥]|\b(?:INR|USD|EUR|IDR|ZAR|RS)\b", re.I)
_WORDS_HINT = re.compile(r"\b(?:in words|amount in words|words)\b", re.I)
_DATE_FORMATS = (
    (re.compile(r"\b\d{2}/\d{2}/\d{4}\b"), "DD/MM/YYYY"),
    (re.compile(r"\b\d{2}-\d{2}-\d{4}\b"), "DD-MM-YYYY"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "YYYY-MM-DD"),
    (re.compile(r"\b\d{1,2}[- ][A-Za-z]{3,}[- ]\d{4}\b"), "D Month YYYY"),
)


@dataclass
class VisionOutcome:
    """What one document read produced, and how."""

    image_id: str
    amount: Decimal | None
    label: str | None
    path: ReadPath
    detail: str
    reconciled: bool = False
    job_status: str | None = None
    #: The label whose figure was displaced when the line-item sum was preferred,
    #: so the substitution travels into the evidence trail rather than vanishing.
    substituted_from: str | None = None


_TAG = re.compile(r"<[^>]+>")
_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<(t[dh])[^>]*>(.*?)</t[dh]>", re.S | re.I)
_HEAD = re.compile(r"<thead[^>]*>(.*?)</thead>", re.S | re.I)
_NUMERIC_CELL = re.compile(r"^[^0-9]{0,4}-?\d[\d.,]*\s*$")
#: A block holding a figure and nothing else, so its label must be a neighbour.
_VALUE_ONLY = re.compile(r"^[^0-9A-Za-z]{0,4}-?\d[\d.,]*\s*$")


def _is_numeric_cell(cell: str) -> bool:
    return bool(_NUMERIC_CELL.match(cell.strip()))


def table_pairs(html: str) -> list[tuple[str, str]]:
    """Label/value pairs from a table, using the grid as the geometry.

    A tabular region arrives from the provider as ONE block carrying an HTML
    grid, so bounding boxes stop at the table's outer edge and the column and
    row structure is the only geometry there is. It is used as such:

    * each numeric cell is paired with its **column header**, by position
    * and with the nearest preceding descriptive cell in its **row**

    Both pairings are emitted. A document that names its total in the row
    ("Net Pay ... 4,365,000") and one that names it in the column header
    ("Grand Total" row under a "Total (Incl Taxes)" column) are then both
    reachable, and the label-priority list decides between them rather than a
    rule about which layout this is.
    """
    pairs: list[tuple[str, str]] = []
    header_cells: list[str] = []
    head = _HEAD.search(html)
    if head:
        for row in _ROW.findall(head.group(1)):
            cells = [_TAG.sub(" ", c).strip() for _tag, c in _CELL.findall(row)]
            if len(cells) > len(header_cells):
                header_cells = cells

    for row in _ROW.findall(html):
        cells = [_TAG.sub(" ", c).strip() for _tag, c in _CELL.findall(row)]
        if cells and cells == header_cells:
            continue
        # Column-header pairing is only trustworthy when this row has the same
        # number of cells as the header. A row spanning columns shifts every
        # index after the span, and a header label paired with the wrong
        # column's figure is worse than no pairing at all.
        aligned = len(cells) == len(header_cells)
        # A row with no descriptive cell anywhere still carries a figure, and
        # that figure is part of what the table adds up to; dropping it is how a
        # line-item sum comes to miss its own total. A row that DOES describe
        # itself is different: a numeric cell standing before that description
        # is a quantity or a serial number, not an amount, and counting it
        # inflates the sum by the quantity column.
        describes_itself = any(
            cell.strip(" :|") and not _is_numeric_cell(cell) for cell in cells
        )
        label = ""
        for index, cell in enumerate(cells):
            if not cell:
                continue
            if _is_numeric_cell(cell):
                # Emitted with an empty label when the row describes nothing, so
                # it reaches the sum but can never be chosen as the answer: no
                # priority phrase matches an empty label.
                if label or not describes_itself:
                    pairs.append((label, cell))
                if aligned and header_cells[index]:
                    pairs.append((header_cells[index], cell))
                continue
            stripped = cell.strip(" :|")
            # A short separator cell such as ": IDR" refines a label, never
            # replaces one.
            if stripped and (len(stripped) > 4 or not label):
                label = stripped
    return pairs


def geometric_pairs(blocks: list[dict]) -> list[tuple[str, str]]:
    """Pair a value-only block with the label block beside or above it.

    Uses the block coordinates the provider does return. A figure sitting alone
    in its own block takes its label from the nearest block that is on the same
    line and to its left, or directly above it and horizontally overlapping.

    This fires on layouts that split label and value into separate blocks. It
    cannot help inside a table, because the provider returns a whole table as a
    single block with one bounding box.
    """
    pairs: list[tuple[str, str]] = []
    positioned = [
        b for b in blocks
        if isinstance(b.get("text"), str) and isinstance(b.get("coordinates"), dict)
    ]
    for block in positioned:
        text = block["text"].strip()
        if not _VALUE_ONLY.match(text):
            continue
        box = block["coordinates"]
        best, best_distance = None, None
        for other in positioned:
            if other is block:
                continue
            other_text = other["text"].strip()
            if not other_text or _VALUE_ONLY.match(other_text):
                continue
            theirs = other["coordinates"]
            same_line = not (
                theirs["y2"] < box["y1"] or theirs["y1"] > box["y2"]
            )
            to_the_left = theirs["x2"] <= box["x1"]
            overlaps_x = not (theirs["x2"] < box["x1"] or theirs["x1"] > box["x2"])
            above = theirs["y2"] <= box["y1"] and overlaps_x
            if same_line and to_the_left:
                distance = box["x1"] - theirs["x2"]
            elif above:
                distance = box["y1"] - theirs["y2"]
            else:
                continue
            if best_distance is None or distance < best_distance:
                best, best_distance = other_text, distance
        if best:
            pairs.append((best, text))
    return pairs


def _split_blocks(payload: dict) -> tuple[list[str], list[tuple[str, str]]]:
    """Plain lines in reading order, plus label/value pairs from tables and geometry."""
    lines: list[str] = []
    pairs: list[tuple[str, str]] = []
    for document in payload.get("documents") or []:
        for page in document.get("pages") or []:
            blocks = sorted(
                page.get("blocks") or [], key=lambda b: b.get("reading_order", 0)
            )
            pairs.extend(geometric_pairs(blocks))
            for block in blocks:
                text = block.get("text")
                if not isinstance(text, str) or not text.strip():
                    continue
                if "<tr" in text.lower():
                    pairs.extend(table_pairs(text))
                    lines.extend(
                        part.strip()
                        for part in _TAG.sub("\n", text).splitlines()
                        if part.strip()
                    )
                else:
                    lines.extend(
                        part.strip() for part in text.splitlines() if part.strip()
                    )
    return lines, pairs


def build_observation(payload: dict) -> DocumentObservation:
    """Turn a transcription into labelled glyph strings and a locale.

    Generic over layout, and no number is parsed here.
    """
    lines, pairs = _split_blocks(payload)
    figures = [
        LabelledFigure(label=label, raw_value=value)
        for label, value in pairs
        if label
    ]
    items = [LineItem(description=label, raw_amount=value) for label, value in pairs]
    words: str | None = None
    separators: list[str] = []
    currency: str | None = None
    date_format: str | None = None

    for line in lines:
        if currency is None:
            found = _CURRENCY_GLYPH.search(line)
            if found:
                currency = found.group(0)
        if date_format is None:
            for pattern, name in _DATE_FORMATS:
                if pattern.search(line):
                    date_format = name
                    break
        if words is None and _WORDS_HINT.search(line):
            words = line
        matches = list(_NUMBER.finditer(line))
        if not matches:
            continue
        last = matches[-1]
        label = line[: last.start()].strip(" :\t|-")
        raw = last.group(0)
        separator = infer_decimal_separator(raw)
        if separator:
            separators.append(separator)
        if label:
            figures.append(LabelledFigure(label=label, raw_value=raw))
            if not pairs:
                items.append(LineItem(description=label, raw_amount=raw))

    for _label, value in pairs:
        separator = infer_decimal_separator(value)
        if separator:
            separators.append(separator)

    majority = max(set(separators), key=separators.count) if separators else None
    return DocumentObservation(
        labelled_figures=tuple(figures),
        line_items=tuple(items),
        amount_in_words=words,
        currency_symbol=currency,
        # REPORTED ONLY. Parsing infers per value, never per document: the taxi
        # receipt prints $28.50 and $5,00 in the same file, so one document-wide
        # separator would misread one of them. Geometry does not help with this;
        # it is a separate rule keyed on each value's own digit-group pattern.
        decimal_separator=majority,
        thousands_grouping=None,
        date_format=date_format,
        source_text="\n".join(lines),
    )


#: Observation-shaped, strings only, every field described because the API
#: rejects a schema whose fields carry no description.
EXTRACT_SCHEMA = json.dumps(
    {
        "type": "object",
        "properties": {
            "final_amount_label": {
                "type": "string",
                "description": "The label printed beside the figure the document says is owed or was paid.",
            },
            "final_amount_text": {
                "type": "string",
                "description": "That figure exactly as printed, keeping every separator glyph.",
            },
            "amount_in_words": {
                "type": "string",
                "description": "The amount written out in words, if the document prints one.",
            },
            "currency_marker": {
                "type": "string",
                "description": "Any currency symbol or code shown on the document.",
            },
        },
    }
)


def _observation_from_extract(payload: dict) -> DocumentObservation:
    result = (payload or {}).get("result") or {}
    label = result.get("final_amount_label") or "total"
    text = result.get("final_amount_text") or ""
    return DocumentObservation(
        labelled_figures=(LabelledFigure(label=label, raw_value=text),) if text else (),
        amount_in_words=result.get("amount_in_words"),
        currency_symbol=result.get("currency_marker"),
        decimal_separator=infer_decimal_separator(text) if text else None,
        source_text=json.dumps(result),
    )


def read_amount(
    image_id: str,
    image_path: Path,
    client,
    *,
    label_priority: tuple[str, ...],
    label_denylist: tuple[str, ...],
    tolerance: Decimal,
) -> VisionOutcome:
    """One digitise read, then extract only if the arithmetic does not close."""
    job = client.digitise(image_path)
    states = client._settings.models.doc_ai_job_states  # noqa: SLF001 - same package
    partial = job.status in states.partial

    if job.payload is None:
        return VisionOutcome(
            image_id, None, None, ReadPath.UNRESOLVED,
            f"digitise returned {job.status}", job_status=job.status,
        )

    observation = build_observation(job.payload)
    amount: Decimal | None = None
    label: str | None = None
    try:
        amount, label = select_amount(
            observation, label_priority=label_priority, label_denylist=label_denylist
        )
    except ParseError as exc:
        amount, label = None, None
        detail = str(exc)
    else:
        detail = f"selected on label {label!r}"

    closed = False
    if amount is not None:
        closed, why = reconciles(
            amount, observation, tolerance=tolerance,
            from_line_item_sum=(label == "line_item_sum"),
        )
        detail = f"{detail}; {why}"

    if amount is not None and closed and not partial:
        return VisionOutcome(
            image_id, amount, label, ReadPath.DIGITISE, detail,
            reconciled=True, job_status=job.status,
        )

    # The arithmetic did not close. Before paying for a second read, try the
    # substitution rule: if the line items sum to a figure the document prints
    # somewhere, that sum is verified arithmetic against a stated figure, which
    # is stronger evidence than a label match that did not close. A partial job
    # still escalates — a flagged partial read is not a trustworthy transcript
    # to do arithmetic on.
    if not partial:
        substitute = reconciling_substitute(observation, tolerance=tolerance)
        if substitute is not None and substitute != amount:
            return VisionOutcome(
                image_id, substitute, "line_item_sum", ReadPath.DIGITISE,
                f"{detail}; line items sum to {substitute}, which the document "
                f"prints; substituted for the figure at {label!r}",
                reconciled=True, job_status=job.status, substituted_from=label,
            )

    # Still nothing that closes, or the job was partial. Second read.
    second = client.extract(image_path, EXTRACT_SCHEMA)
    if second.payload is not None:
        extracted = _observation_from_extract(second.payload)
        # Merge, do not replace. The second read contributes one more labelled
        # figure; the priority list then chooses across BOTH reads. Replacing
        # would let a weaker label from the second read beat a stronger one the
        # first read already found.
        merged = observation.model_copy(
            update={
                "labelled_figures": extracted.labelled_figures
                + observation.labelled_figures,
                "amount_in_words": observation.amount_in_words
                or extracted.amount_in_words,
            }
        )
        try:
            confirmed, confirmed_label = select_amount(
                merged, label_priority=label_priority, label_denylist=label_denylist
            )
        except ParseError:
            confirmed = None
            confirmed_label = None
        if confirmed is not None:
            agrees = amount is not None and abs(confirmed - amount) <= tolerance
            return VisionOutcome(
                image_id, confirmed, confirmed_label or label,
                ReadPath.DIGITISE_THEN_EXTRACT,
                f"{detail}; second read {'agrees' if agrees else 'used'}",
                reconciled=agrees, job_status=second.status,
            )

    if amount is not None:
        return VisionOutcome(
            image_id, amount, label, ReadPath.DIGITISE_THEN_EXTRACT,
            f"{detail}; second read produced nothing", reconciled=False,
            job_status=second.status,
        )
    return VisionOutcome(
        image_id, None, None, ReadPath.UNRESOLVED,
        f"{detail}; neither read produced a usable figure", job_status=second.status,
    )
