"""Typed configuration for the Buy or Wait? submission.

Loads ``config/models.yaml`` and ``config/decisions.yaml`` into pydantic models
that forbid unknown keys, and reads the single API credential out of the
environment file resolved by :mod:`bow.paths`.

The credential value never leaves this module's return value. It is not
logged, not written to a cache, not placed in a repr, and not included in any
exception message.
"""

from __future__ import annotations

from bow.settings.loader import (
    ConfigError,
    DecisionEntry,
    DecisionsConfig,
    LanguagesConfig,
    ModelsConfig,
    ProviderConfig,
    RateLimitConfig,
    RetryPolicyConfig,
    ForecastThresholds,
    RecurrenceThresholds,
    RoleConfig,
    PerceptionThresholds,
    SpendingChangeThresholds,
    VisionThresholds,
    Settings,
    ThresholdsConfig,
    load_decisions,
    load_models,
    load_settings,
    load_thresholds,
    resolve_role,
)

__all__ = [
    "ConfigError",
    "DecisionEntry",
    "DecisionsConfig",
    "LanguagesConfig",
    "ModelsConfig",
    "ProviderConfig",
    "RateLimitConfig",
    "RetryPolicyConfig",
    "ForecastThresholds",
    "RecurrenceThresholds",
    "RoleConfig",
    "PerceptionThresholds",
    "SpendingChangeThresholds",
    "VisionThresholds",
    "Settings",
    "ThresholdsConfig",
    "load_decisions",
    "load_models",
    "load_settings",
    "load_thresholds",
    "resolve_role",
]
