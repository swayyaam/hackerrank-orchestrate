"""What a document read returns.

**Every numeric field is a string.** The model reports the glyphs it saw and
the locale it saw them in; code does the ``Decimal`` parsing using the reported
separator.

That split is the whole point. A model that hands back a parsed float has
already decided that ``$33,50`` means thirty-three thousand five hundred, and
destroyed the evidence needed to notice it meant thirty-three and a half. The
separator has to travel with the digits.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

__all__ = [
    "DocumentObservation",
    "LabelledFigure",
    "LineItem",
    "ReadPath",
]

from enum import StrEnum


class ReadPath(StrEnum):
    """Which route produced a document's amount. Recorded per image."""

    DIGITISE = "digitise"
    DIGITISE_THEN_EXTRACT = "digitise_then_extract"
    UNRESOLVED = "unresolved"


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LabelledFigure(_Frozen):
    """One label and the glyphs printed beside it, both verbatim."""

    label: str
    raw_value: str


class LineItem(_Frozen):
    """One row of an itemised list, glyphs only."""

    description: str
    raw_amount: str


class DocumentObservation(_Frozen):
    """A read of one document. Strings and a locale; no parsed numbers."""

    labelled_figures: tuple[LabelledFigure, ...] = ()
    line_items: tuple[LineItem, ...] = ()
    amount_in_words: str | None = None
    currency_symbol: str | None = None
    #: "." or "," — which glyph separates the fractional part.
    decimal_separator: str | None = None
    #: How digits are grouped: "thousand", "lakh", "none".
    thousands_grouping: str | None = None
    #: The date format observed, e.g. "DD/MM/YYYY".
    date_format: str | None = None
    source_text: str = ""
