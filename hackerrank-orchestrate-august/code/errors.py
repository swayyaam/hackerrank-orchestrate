"""Typed exception hierarchy. Every error this package raises descends from RouterError."""

from __future__ import annotations


class RouterError(Exception):
    """Base for every error this package raises."""


class DatasetError(RouterError):
    """A CSV is malformed, missing, or has an unresolvable foreign key."""


class ContextError(RouterError):
    """A join failed for one message."""


class MediaPathError(RouterError):
    """A media path escapes the dataset root."""


class MediaDecodeError(RouterError):
    """A media file could not be decoded."""


class PerceptionTransportError(RouterError):
    """Network, timeout, or rate limit on a perception call.

    ``cause`` is the named failure reason recorded on the extraction record and
    counted by the metrics engine's ``media.failures_by_cause``. Subclasses
    narrow it so a provider's status code survives as a distinguishable outcome
    rather than collapsing into one bucket.
    """

    cause: str = "extraction_unavailable"


class ProviderRejectedMediaError(PerceptionTransportError):
    """HTTP 422: the provider could not process this file at all."""

    cause = "unprocessable_media"


class ProviderCredentialsRejectedError(PerceptionTransportError):
    """HTTP 403: the credential was presented and refused. Not retryable."""

    cause = "credentials_rejected"


class ProviderRateLimitedError(PerceptionTransportError):
    """HTTP 429, after the backoff schedule was exhausted."""

    cause = "rate_limited"


class ProviderUnavailableError(PerceptionTransportError):
    """HTTP 503, after the backoff schedule was exhausted."""

    cause = "provider_unavailable"


class PerceptionSchemaError(RouterError):
    """A model response did not validate against Perception."""


class PerceptionCacheMissError(RouterError):
    """An offline run needs a perception record that is not cached.

    Carries the message_id so the run can name exactly what is missing instead
    of quietly calling out to fill the gap.
    """

    def __init__(self, message_id: str) -> None:
        super().__init__(f"no cached perception for {message_id}")
        self.message_id = message_id


class PolicyError(RouterError):
    """A rule table invariant broke."""


class EvidenceIntegrityError(RouterError):
    """A cited evidence id fails an ownership or temporal invariant."""


class ReasonRegisterError(RouterError):
    """A composed reason violated its register bounds."""


class BudgetExhaustedError(RouterError):
    """A run-level token or wall-clock ceiling was reached mid-run."""


class OutputContractError(RouterError):
    """The output rows do not satisfy the submission contract."""
