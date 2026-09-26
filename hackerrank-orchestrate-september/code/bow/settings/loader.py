"""Config loading and role resolution.

Three failure modes are deliberate and loud:

* an unknown key anywhere in either YAML file      -> :class:`ConfigError`
* a role, retry policy, or rate limit that is referenced but not defined
                                                   -> :class:`ConfigError`
* a missing or empty ``SARVAM_API_KEY``            -> :class:`ConfigError`

The API key is read but never rendered. ``SecretStr`` keeps it out of reprs and
tracebacks, and :class:`Settings` exposes only its length and prefix shape for
diagnostics.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from decimal import Decimal
from typing import Any, Literal

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from bow import paths

__all__ = [
    "ConfigError",
    "ForecastThresholds",
    "RecurrenceThresholds",
    "PerceptionThresholds",
    "SpendingChangeThresholds",
    "VisionThresholds",
    "ThresholdsConfig",
    "load_thresholds",
    "DecisionEntry",
    "DecisionsConfig",
    "LanguagesConfig",
    "ModelsConfig",
    "ProviderConfig",
    "RateLimitConfig",
    "RetryPolicyConfig",
    "RoleConfig",
    "Settings",
    "load_decisions",
    "load_models",
    "load_settings",
    "resolve_role",
]

RoleKind = Literal["chat", "doc_ai", "text"]
ReasoningEffort = Literal["low", "medium", "high"]


class ConfigError(RuntimeError):
    """Configuration is unusable. Never carries a secret value."""


class _Strict(BaseModel):
    """Base model that refuses unknown keys."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class RetryPolicyConfig(_Strict):
    max_attempts: int = Field(ge=1)
    initial_backoff_seconds: float = Field(gt=0)
    max_backoff_seconds: float = Field(gt=0)
    multiplier: float = Field(ge=1)
    jitter_seconds: float = Field(ge=0)
    retry_on_status: tuple[int, ...]


class RateLimitConfig(_Strict):
    requests_per_minute: int = Field(gt=0)
    burst: int = Field(gt=0)


class ProviderConfig(_Strict):
    name: str
    api_key_env: str
    base_url: str
    chat_v1_path: str
    chat_v2_path: str
    doc_ai_digitise_path: str
    doc_ai_extract_path: str
    doc_ai_status_path: str
    doc_ai_results_path: str
    doc_ai_download_path: str
    lang_detect_path: str
    plan: str
    max_tokens_ceiling: int = Field(gt=0)


class DocAiJobStates(_Strict):
    success: tuple[str, ...]
    partial: tuple[str, ...]
    failure: tuple[str, ...]


class LanguagesConfig(_Strict):
    document_in_set: tuple[str, ...]
    text_in_set: tuple[str, ...]
    document_default_language: str
    unknown_code: str


class RoleConfig(_Strict):
    description: str | None = None
    kind: RoleKind
    provider_path: str
    endpoint: str
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int | None = Field(default=None, gt=0)
    reasoning_effort: ReasoningEffort | None = None
    # Probe 5 measured: temperature 0 alone is NOT byte-reproducible, but
    # temperature 0 with a fixed seed is. Deterministic roles must pin it.
    seed: int | None = None
    timeout_seconds: float = Field(gt=0)
    retry_policy: str
    rate_limit: str
    # Document AI roles only.
    language: str | None = None
    output_format: str | None = None
    content_type: str | None = None
    poll_interval_seconds: float | None = Field(default=None, gt=0)
    poll_timeout_seconds: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _doc_ai_roles_are_complete(self) -> RoleConfig:
        if self.kind == "doc_ai":
            missing = [
                name
                for name in (
                    "language",
                    "output_format",
                    "poll_interval_seconds",
                    "poll_timeout_seconds",
                )
                if getattr(self, name) is None
            ]
            if missing:
                raise ValueError(
                    f"doc_ai role is missing required field(s): {', '.join(missing)}"
                )
        return self


class ModelsConfig(_Strict):
    version: int
    provider: ProviderConfig
    doc_ai_job_states: DocAiJobStates
    languages: LanguagesConfig
    retry_policies: dict[str, RetryPolicyConfig]
    rate_limits: dict[str, RateLimitConfig]
    roles: dict[str, RoleConfig]

    @model_validator(mode="after")
    def _references_resolve(self) -> ModelsConfig:
        for role_name, role in self.roles.items():
            if role.retry_policy not in self.retry_policies:
                raise ValueError(
                    f"role {role_name!r} references undefined "
                    f"retry_policy {role.retry_policy!r}"
                )
            if role.rate_limit not in self.rate_limits:
                raise ValueError(
                    f"role {role_name!r} references undefined "
                    f"rate_limit {role.rate_limit!r}"
                )
            if role.max_tokens is not None:
                ceiling = self.provider.max_tokens_ceiling
                if role.max_tokens > ceiling:
                    raise ValueError(
                        f"role {role_name!r} max_tokens {role.max_tokens} exceeds "
                        f"the configured plan ceiling {ceiling}"
                    )
        return self


class RecurrenceThresholds(_Strict):
    """Bands, tolerances and minimums for cadence detection.

    Every value the recurrence layer needs arrives here, so that module holds
    no numeric literal of its own. Ratios are ``Decimal`` rather than ``float``:
    they multiply money and periods, and a float would leak into both.
    """

    monthly_gap_min_days: int = Field(gt=0)
    monthly_gap_max_days: int = Field(gt=0)
    day_of_month_tolerance_days: int = Field(ge=0)
    fixed_interval_tolerance_days: int = Field(ge=0)
    min_occurrences: int = Field(ge=2)
    staleness_period_multiple: Decimal = Field(gt=0)
    amount_median_divergence_fraction: Decimal = Field(ge=0)
    max_period_days: int = Field(gt=0)
    continuation_period_multiple: Decimal = Field(gt=0)

    @model_validator(mode="after")
    def _band_is_ordered(self) -> RecurrenceThresholds:
        if self.monthly_gap_min_days >= self.monthly_gap_max_days:
            raise ValueError("monthly_gap_min_days must be below monthly_gap_max_days")
        if self.max_period_days < self.monthly_gap_max_days:
            raise ValueError("max_period_days must not exclude the monthly band")
        return self


class SpendingChangeThresholds(_Strict):
    max_changes: int = Field(ge=1)


class PerceptionThresholds(_Strict):
    max_evidence_quote_chars: int = Field(gt=0)


class VisionThresholds(_Strict):
    label_priority: tuple[str, ...]
    label_denylist: tuple[str, ...]
    reconciliation_tolerance: Decimal = Field(ge=0)
    #: How many independent reads of one document are taken before an amount is
    #: accepted, and how disagreement between them is settled. Both are fixed in
    #: config so the rule cannot be chosen after seeing which answer it picks.
    reads_per_amount: int = Field(default=1, ge=1)
    read_disagreement_rule: str = "majority_then_reconciled"


class ForecastThresholds(_Strict):
    horizon_days: int = Field(gt=0)
    variable_spend_window_days: int = Field(gt=0)
    variable_spend_warn_fraction_of_opening: Decimal = Field(gt=0)
    min_occurrences_in_trailing_window: int = Field(ge=1)


class ThresholdsConfig(_Strict):
    version: int
    recurrence: RecurrenceThresholds
    spending_changes: SpendingChangeThresholds
    perception: PerceptionThresholds
    vision: VisionThresholds
    forecast: ForecastThresholds


class DecisionEntry(_Strict):
    id: str
    question: str
    default: str
    alternative: str
    sample_discriminates: bool
    rationale: str
    read_at: str
    #: True when the pipeline consults this flag at runtime, so the alternative
    #: is one config edit away. False when the default is simply the implemented
    #: behaviour and the entry records the choice rather than switching it.
    switchable: bool = False
    #: The line from AGENTS.md that settles this question, quoted verbatim, or
    #: ``None`` where the contract is silent and the default is still our
    #: reading. The distinction matters: a confirmed default is not a risk, an
    #: unconfirmed one is, and only the register can tell them apart.
    contract: str | None = None


class DecisionsConfig(_Strict):
    version: int
    decisions: tuple[DecisionEntry, ...]

    @model_validator(mode="after")
    def _ids_are_unique(self) -> DecisionsConfig:
        seen = [d.id for d in self.decisions]
        duplicates = sorted({i for i in seen if seen.count(i) > 1})
        if duplicates:
            raise ValueError(f"duplicate decision id(s): {', '.join(duplicates)}")
        return self

    def by_id(self, decision_id: str) -> DecisionEntry:
        for entry in self.decisions:
            if entry.id == decision_id:
                return entry
        raise ConfigError(f"unknown decision id: {decision_id!r}")


class Settings(BaseModel):
    """Fully resolved configuration plus the single credential."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    models: ModelsConfig
    decisions: DecisionsConfig
    thresholds: ThresholdsConfig
    api_key: SecretStr

    @property
    def api_key_length(self) -> int:
        """Length only. Never the characters."""
        return len(self.api_key.get_secret_value())

    @property
    def api_key_prefix(self) -> str:
        """Leading non-secret marker, for shape checks only."""
        value = self.api_key.get_secret_value()
        return value.split("_", 1)[0] + "_" if "_" in value[:8] else ""

    def role(self, name: str) -> RoleConfig:
        return resolve_role(self.models, name)

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            f"Settings(provider={self.models.provider.name!r}, "
            f"roles={len(self.models.roles)}, "
            f"decisions={len(self.decisions.decisions)}, api_key=<redacted>)"
        )

    __str__ = __repr__


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name} is not valid YAML: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ConfigError(f"{path.name} must contain a mapping at the top level")
    return loaded


def load_models(path: Path | None = None) -> ModelsConfig:
    """Load and validate ``models.yaml``. Unknown keys are fatal."""
    target = path or (paths.CONFIG_DIR / "models.yaml")
    try:
        return ModelsConfig.model_validate(_read_yaml(target))
    except ConfigError:
        raise
    except Exception as exc:
        raise ConfigError(f"{target.name} failed validation: {exc}") from exc


def load_decisions(path: Path | None = None) -> DecisionsConfig:
    """Load and validate ``decisions.yaml``. Unknown keys are fatal."""
    target = path or (paths.CONFIG_DIR / "decisions.yaml")
    try:
        return DecisionsConfig.model_validate(_read_yaml(target))
    except ConfigError:
        raise
    except Exception as exc:
        raise ConfigError(f"{target.name} failed validation: {exc}") from exc


def load_thresholds(path: Path | None = None) -> ThresholdsConfig:
    """Load and validate ``thresholds.yaml``. Needs no credential."""
    target = path or (paths.CONFIG_DIR / "thresholds.yaml")
    try:
        return ThresholdsConfig.model_validate(_read_yaml(target))
    except ConfigError:
        raise
    except Exception as exc:
        raise ConfigError(f"{target.name} failed validation: {exc}") from exc


def _cross_check(thresholds: ThresholdsConfig, decisions: DecisionsConfig) -> None:
    """A number that appears in two files must agree in both.

    ``min_occurrences`` is both a tunable threshold and a recorded decision.
    Letting them drift would mean the decision register documents one rule while
    the code runs another.
    """
    recorded = decisions.by_id("Q18_min_occurrences_for_cadence").default
    if str(thresholds.recurrence.min_occurrences) != recorded.strip():
        raise ConfigError(
            "thresholds.recurrence.min_occurrences is "
            f"{thresholds.recurrence.min_occurrences} but "
            f"Q18_min_occurrences_for_cadence records {recorded!r}. "
            "The two must agree."
        )


def resolve_role(models: ModelsConfig, name: str) -> RoleConfig:
    """Look up one role by name, listing the known roles on failure."""
    try:
        return models.roles[name]
    except KeyError:
        known = ", ".join(sorted(models.roles))
        raise ConfigError(f"unknown role {name!r}. Known roles: {known}") from None


def _read_api_key(variable: str, env_path: Path) -> SecretStr:
    """Read one credential from the env file, then the process environment.

    The env file wins so that a stale exported shell variable cannot silently
    shadow the file the operator edited. Nothing about the value is logged.
    """
    import os

    values = dotenv_values(env_path) if env_path.is_file() else {}
    if variable in values:
        raw = values[variable]
        source = str(env_path)
    elif variable in os.environ:
        raw = os.environ[variable]
        source = "the process environment"
    else:
        raise ConfigError(
            f"{variable} is not set. Expected it in {env_path} "
            f"or in the process environment. "
            f"Override the file location with {paths.ENV_PATH_ENV_VAR}."
        )
    raw = (raw or "").strip()
    if not raw:
        raise ConfigError(f"{variable} is present but empty in {source}.")
    return SecretStr(raw)


def load_settings(
    *,
    models_path: Path | None = None,
    decisions_path: Path | None = None,
    thresholds_path: Path | None = None,
    env_path: Path | None = None,
) -> Settings:
    """Load everything. Raises :class:`ConfigError` on any problem."""
    models = load_models(models_path)
    decisions = load_decisions(decisions_path)
    thresholds = load_thresholds(thresholds_path)
    _cross_check(thresholds, decisions)
    target_env = env_path or paths.ENV_PATH
    api_key = _read_api_key(models.provider.api_key_env, target_env)
    return Settings(
        models=models, decisions=decisions, thresholds=thresholds, api_key=api_key
    )


@lru_cache(maxsize=1)
def cached_settings() -> Settings:
    """Process-wide singleton for call sites that should not reload."""
    return load_settings()
