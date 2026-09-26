"""The run report.

Two kinds of thing land here.

**Facts about the load**: a row count and a per-column null count per file, and
the distinct values seen in each open vocabulary, so a value this dataset has
never shown is visible rather than silent.

**Warning-level assertions**: every observation the reconnaissance made about
*this* dataset is checked here and reported as a count. None of them raises.
The general case is implemented for all of them; if a future dataset breaks
one, the report carries a loud number instead of a crash or a silently wrong
answer.

The report is immutable and accumulates functionally, so nothing in the
pipeline can mutate a report another stage already read.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

__all__ = ["FileLoadStats", "RunReport", "VocabularyStats", "WarningCount"]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FileLoadStats(_Frozen):
    """Row count and per-column null count for one source file."""

    file: str
    rows: int
    null_counts: dict[str, int]

    def summary(self) -> str:
        populated = {k: v for k, v in self.null_counts.items() if v}
        nulls = ", ".join(f"{k}={v}" for k, v in sorted(populated.items())) or "none"
        return f"{self.file}: {self.rows} rows, nulls: {nulls}"


class VocabularyStats(_Frozen):
    """Distinct values observed in one open vocabulary."""

    name: str
    values: dict[str, int]

    @property
    def distinct(self) -> int:
        return len(self.values)


class WarningCount(_Frozen):
    """One warning-level assertion. Reports a number; never raises."""

    name: str
    count: int
    detail: dict[str, int] = {}
    note: str = ""

    def summary(self) -> str:
        extra = ""
        if self.detail:
            extra = "  {" + ", ".join(f"{k}: {v}" for k, v in sorted(self.detail.items())) + "}"
        flag = " " if self.count == 0 else "!"
        return f"{flag} {self.name}: {self.count}{extra}"


class RunReport(_Frozen):
    """Immutable accumulation of everything worth reporting about a run."""

    loads: tuple[FileLoadStats, ...] = ()
    vocabularies: tuple[VocabularyStats, ...] = ()
    warnings: tuple[WarningCount, ...] = ()
    drops: dict[str, int] = {}
    keeps: dict[str, int] = {}

    def with_load(self, stats: FileLoadStats) -> RunReport:
        return self.model_copy(update={"loads": self.loads + (stats,)})

    def with_vocabulary(self, stats: VocabularyStats) -> RunReport:
        return self.model_copy(update={"vocabularies": self.vocabularies + (stats,)})

    def with_warning(self, warning: WarningCount) -> RunReport:
        return self.model_copy(update={"warnings": self.warnings + (warning,)})

    def with_warnings(self, warnings: tuple[WarningCount, ...]) -> RunReport:
        return self.model_copy(update={"warnings": self.warnings + warnings})

    def with_counts(
        self, *, drops: dict[str, int] | None = None, keeps: dict[str, int] | None = None
    ) -> RunReport:
        return self.model_copy(
            update={
                "drops": {**self.drops, **(drops or {})},
                "keeps": {**self.keeps, **(keeps or {})},
            }
        )

    def merged(self, other: RunReport) -> RunReport:
        return RunReport(
            loads=self.loads + other.loads,
            vocabularies=self.vocabularies + other.vocabularies,
            warnings=self.warnings + other.warnings,
            drops={**self.drops, **other.drops},
            keeps={**self.keeps, **other.keeps},
        )

    def warning(self, name: str) -> WarningCount | None:
        for item in self.warnings:
            if item.name == name:
                return item
        return None

    @property
    def triggered_warnings(self) -> tuple[WarningCount, ...]:
        """Warnings whose count is not zero — the ones worth reading."""
        return tuple(w for w in self.warnings if w.count)

    def render(self) -> str:
        lines: list[str] = []
        if self.loads:
            lines.append("loads")
            lines += [f"  {s.summary()}" for s in self.loads]
        if self.vocabularies:
            lines.append("open vocabularies")
            lines += [
                f"  {v.name}: {v.distinct} distinct value(s)" for v in self.vocabularies
            ]
        if self.keeps:
            lines.append("kept events by reason")
            lines += [f"  {k}: {n}" for k, n in sorted(self.keeps.items())]
        if self.drops:
            lines.append("dropped events by reason")
            lines += [f"  {k}: {n}" for k, n in sorted(self.drops.items())]
        if self.warnings:
            lines.append("warning-level assertions")
            lines += [f"  {w.summary()}" for w in self.warnings]
        return "\n".join(lines)
