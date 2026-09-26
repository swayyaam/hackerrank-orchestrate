"""CSV loading. Typed objects out; pandas never escapes this module.

Three rules shape everything here.

**Nothing is inferred.** Every cell is read as a string. pandas is told
``dtype=str`` and ``na_filter=False``, so no column is silently turned into a
float, an int, a bool, or NaN on the way in. :func:`_assert_all_object_dtype`
verifies that rather than trusting it.

**Money never touches a float.** Amount columns arrive as strings and become
``Decimal`` in the contract layer.

**Bad rows are collected, not fatal-on-first.** A file with three enum
violations raises once, naming all three with file, row, column and value. One
pass gives the whole list.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, TypeVar

import pandas as pd
from pydantic import BaseModel, ValidationError

from bow.contracts import (
    DatasetBundle,
    ExchangeRate,
    FileLoadStats,
    FinancialEvent,
    FinancialProfile,
    ImageLink,
    Message,
    PaymentOption,
    Request,
    RunReport,
    SampleRequest,
    VocabularyStats,
)

__all__ = [
    "LoadResult",
    "RowViolation",
    "SchemaViolationError",
    "load_dataset",
    "load_events",
    "load_exchange_rates",
    "load_images",
    "load_messages",
    "load_payment_options",
    "load_profiles",
    "load_requests",
    "load_sample_requests",
]

T = TypeVar("T", bound=BaseModel)

#: Columns that hold a pipe-separated list rather than a scalar.
_LIST_COLUMNS = {
    "financial_priorities",
    "expense_categories_to_protect",
    "expense_categories_user_is_willing_to_reduce",
    "expense_categories_user_is_willing_to_stop",
    "payment_methods_user_will_consider",
}

#: Columns serialised as lowercase ``true``/``false``. Parsed explicitly so
#: pandas never gets the chance to guess.
_BOOL_COLUMNS = {"allows_partial_payment"}

_BOOL_VALUES = {"true": True, "false": False}

#: Open vocabularies: counted so an unseen value is visible, never fatal.
_OPEN_VOCABULARIES = {
    "financial_events.csv": ("category", "description"),
    "messages.csv": ("source_type",),
}

_LIST_SEPARATOR = "|"


class RowViolation(BaseModel):
    """One rejected cell, located precisely enough to fix by hand."""

    model_config = {"frozen": True}

    file: str
    row_index: int
    row_id: str
    column: str
    value: str
    message: str

    def summary(self) -> str:
        return (
            f"{self.file} row {self.row_index} ({self.row_id}) "
            f"column {self.column!r} value {self.value!r}: {self.message}"
        )


class SchemaViolationError(ValueError):
    """Every bad row in one file, reported together.

    Raised once per file after the whole file has been walked, so a fix pass
    can address all of them rather than one per run.
    """

    def __init__(self, file: str, violations: Sequence[RowViolation]) -> None:
        self.file = file
        self.violations = tuple(violations)
        detail = "\n".join(f"  - {v.summary()}" for v in self.violations)
        super().__init__(
            f"{file}: {len(self.violations)} row(s) failed validation\n{detail}"
        )


class LoadResult(BaseModel):
    """Everything loaded, plus what the run report needs to know about it."""

    model_config = {"frozen": True, "extra": "forbid"}

    dataset: DatasetBundle
    report: RunReport


# --- reading ------------------------------------------------------------------


def _assert_all_object_dtype(frame: pd.DataFrame, path: Path) -> None:
    """Prove no column was type-inferred on the way in."""
    inferred = {
        str(column): str(dtype)
        for column, dtype in frame.dtypes.items()
        if dtype != object
    }
    if inferred:
        raise TypeError(
            f"{path.name}: pandas inferred non-string dtypes for {inferred}. "
            "Every cell must arrive as a string so money never passes through "
            "a float."
        )


def _read_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    """Read a CSV as pure strings. No NaN, no inference, no coercion."""
    if not path.is_file():
        raise FileNotFoundError(f"dataset file not found: {path}")
    frame = pd.read_csv(
        path,
        dtype=str,
        keep_default_na=False,
        na_filter=False,
        encoding="utf-8",
    )
    _assert_all_object_dtype(frame, path)
    columns = [str(c) for c in frame.columns]
    rows = [
        {column: str(row[column]) for column in columns}
        for _, row in frame.iterrows()
    ]
    return rows, columns


def _null_counts(rows: Sequence[Mapping[str, str]], columns: Sequence[str]) -> dict[str, int]:
    return {
        column: sum(1 for row in rows if not row[column].strip()) for column in columns
    }


def _blank_to_none(value: str) -> str | None:
    text = value.strip()
    return text or None


def _split_list(value: str) -> tuple[str, ...]:
    return tuple(part for part in (p.strip() for p in value.split(_LIST_SEPARATOR)) if part)


def _parse_bool(value: str, column: str) -> bool:
    """Case-sensitive on purpose.

    The column serialises as lowercase ``true``/``false``. Accepting ``True``
    or ``1`` would mean quietly accepting a file that is not the format we were
    given, which is exactly the kind of leniency that hides a schema change.
    """
    text = value.strip()
    if text not in _BOOL_VALUES:
        raise ValueError(
            f"{column} must be lowercase 'true' or 'false', got {value!r}"
        )
    return _BOOL_VALUES[text]


def _prepare(row: Mapping[str, str]) -> dict[str, Any]:
    """Turn one raw string row into constructor keyword arguments."""
    prepared: dict[str, Any] = {}
    for column, raw in row.items():
        if column in _LIST_COLUMNS:
            prepared[column] = _split_list(raw)
        elif column in _BOOL_COLUMNS:
            prepared[column] = _parse_bool(raw, column)
        else:
            prepared[column] = _blank_to_none(raw)
    return prepared


def _identify(row: Mapping[str, str]) -> str:
    """A human-readable handle for a row, using whichever id column it has."""
    for column in row:
        if column.endswith("_id"):
            value = row[column].strip()
            if value:
                return value
    return "<no id column>"


def _violations_from(
    error: ValidationError, file: str, row_index: int, row: Mapping[str, str]
) -> list[RowViolation]:
    found: list[RowViolation] = []
    for detail in error.errors():
        location = detail.get("loc") or ("<row>",)
        column = str(location[0])
        found.append(
            RowViolation(
                file=file,
                row_index=row_index,
                row_id=_identify(row),
                column=column,
                value=row.get(column, ""),
                message=str(detail.get("msg", "invalid")),
            )
        )
    return found


def _load(path: Path, model: type[T]) -> tuple[tuple[T, ...], FileLoadStats]:
    """Load one file into typed objects, collecting every violation first."""
    rows, columns = _read_rows(path)
    built: list[T] = []
    violations: list[RowViolation] = []

    for index, row in enumerate(rows, start=1):
        try:
            built.append(model.model_validate(_prepare(row)))
        except ValidationError as exc:
            violations.extend(_violations_from(exc, path.name, index, row))
        except (ValueError, TypeError) as exc:
            violations.append(
                RowViolation(
                    file=path.name,
                    row_index=index,
                    row_id=_identify(row),
                    column="<row>",
                    value="",
                    message=str(exc),
                )
            )

    if violations:
        raise SchemaViolationError(path.name, violations)

    stats = FileLoadStats(
        file=path.name, rows=len(rows), null_counts=_null_counts(rows, columns)
    )
    return tuple(built), stats


def _vocabulary_stats(path: Path, rows: Iterable[Mapping[str, str]]) -> list[VocabularyStats]:
    """Count the distinct values of each open vocabulary in a file."""
    names = _OPEN_VOCABULARIES.get(path.name, ())
    if not names:
        return []
    materialised = list(rows)
    return [
        VocabularyStats(
            name=f"{path.name}:{name}",
            values=dict(Counter(row[name].strip() for row in materialised if name in row)),
        )
        for name in names
    ]


# --- one function per file ----------------------------------------------------


def load_profiles(path: Path) -> tuple[tuple[FinancialProfile, ...], FileLoadStats]:
    return _load(path, FinancialProfile)


def load_events(path: Path) -> tuple[tuple[FinancialEvent, ...], FileLoadStats]:
    return _load(path, FinancialEvent)


def load_exchange_rates(path: Path) -> tuple[tuple[ExchangeRate, ...], FileLoadStats]:
    return _load(path, ExchangeRate)


def load_requests(path: Path) -> tuple[tuple[Request, ...], FileLoadStats]:
    return _load(path, Request)


def load_sample_requests(path: Path) -> tuple[tuple[SampleRequest, ...], FileLoadStats]:
    return _load(path, SampleRequest)


def load_payment_options(path: Path) -> tuple[tuple[PaymentOption, ...], FileLoadStats]:
    return _load(path, PaymentOption)


def load_messages(path: Path) -> tuple[tuple[Message, ...], FileLoadStats]:
    return _load(path, Message)


def load_images(path: Path) -> tuple[tuple[ImageLink, ...], FileLoadStats]:
    return _load(path, ImageLink)


#: Filename -> loader. Filenames are data about the dataset layout, declared in
#: one place rather than scattered through call sites.
_FILES: tuple[tuple[str, str, Any], ...] = (
    ("profiles", "financial_profiles.csv", load_profiles),
    ("events", "financial_events.csv", load_events),
    ("exchange_rates", "exchange_rates.csv", load_exchange_rates),
    ("requests", "requests.csv", load_requests),
    ("sample_requests", "sample_requests.csv", load_sample_requests),
    ("payment_options", "request_payment_options.csv", load_payment_options),
    ("messages", "messages.csv", load_messages),
    ("images", "images.csv", load_images),
)


def load_dataset(dataset_dir: Path) -> LoadResult:
    """Load every file, recording row and null counts into the run report."""
    loaded: dict[str, tuple[Any, ...]] = {}
    report = RunReport()

    for attribute, filename, loader in _FILES:
        path = dataset_dir / filename
        records, stats = loader(path)
        loaded[attribute] = records
        report = report.with_load(stats)
        for vocabulary in _vocabulary_stats(path, _read_rows(path)[0]):
            report = report.with_vocabulary(vocabulary)

    return LoadResult(dataset=DatasetBundle(**loaded), report=report)
