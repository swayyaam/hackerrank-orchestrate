"""One model per dataset row, matching the real column set.

Columns follow ``docs/DATA_REPORT.md`` section 1 exactly.

Note what ``financial_events.csv`` does **not** have: there is no
``is_recurring`` column and no ``recurrence_days`` column. Recurrence is not
given by the data, it is inferred, and nothing in this module assumes
otherwise.

Every optional column is ``None`` when blank. A blank amount is ``None``, never
zero — the problem statement is explicit about that, and the difference is the
whole reason the perception layer exists.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, model_validator

from bow.contracts.enums import (
    AffordabilityStatus,
    Direction,
    EventStatus,
    EventType,
    Flexibility,
    PaymentMethod,
    RequestType,
)
from bow.contracts.scalars import AwareDateTime, CurrencyCode, IsoDate, Money

__all__ = [
    "DatasetBundle",
    "ExchangeRate",
    "FinancialEvent",
    "FinancialProfile",
    "ImageLink",
    "Message",
    "PaymentOption",
    "Request",
    "SampleRequest",
]


class _Row(BaseModel):
    """Frozen, closed, and hashable-by-value."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class FinancialProfile(_Row):
    """``financial_profiles.csv`` — 10 columns."""

    user_id: str
    home_currency: CurrencyCode
    current_available_balance: Money
    minimum_balance_to_keep: Money
    financial_priorities: tuple[str, ...]
    expense_categories_to_protect: tuple[str, ...]
    expense_categories_user_is_willing_to_reduce: tuple[str, ...]
    expense_categories_user_is_willing_to_stop: tuple[str, ...]
    payment_methods_user_will_consider: tuple[PaymentMethod, ...]
    # Blank when the user will not consider installments at all.
    max_installment_months: int | None = None

    @property
    def considers_installments(self) -> bool:
        return PaymentMethod.INSTALLMENTS in self.payment_methods_user_will_consider


class FinancialEvent(_Row):
    """``financial_events.csv`` — 14 columns.

    ``amount`` is ``None`` when the source cell is blank. It is never zero by
    default; resolving it is the perception layer's job.
    """

    event_id: str
    user_id: str
    event_type: EventType
    # Open vocabulary: counted in the run report, never branched on.
    description: str
    category: str
    direction: Direction
    amount: Money | None = None
    currency: CurrencyCode
    event_date: IsoDate
    settlement_date: IsoDate | None = None
    status: EventStatus
    linked_event_id: str | None = None
    flexibility: Flexibility
    minimum_allowed_amount: Money | None = None

    @property
    def has_amount(self) -> bool:
        return self.amount is not None


class ExchangeRate(_Row):
    """``exchange_rates.csv`` — 4 columns. Directional: A->B is not B->A."""

    rate_date: IsoDate
    from_currency: CurrencyCode
    to_currency: CurrencyCode
    rate: Money

    @property
    def pair(self) -> tuple[str, str]:
        return (self.from_currency, self.to_currency)


class Request(_Row):
    """``requests.csv`` — 8 columns."""

    request_id: str
    user_id: str
    request_date: IsoDate
    request_type: RequestType
    requested_amount: Money
    desired_completion_date: IsoDate
    allows_partial_payment: bool
    request_text: str


class SampleRequest(Request):
    """``sample_requests.csv`` — the 8 input columns plus 7 output columns."""

    amount_safe_to_pay: Money
    affordability_status: AffordabilityStatus
    recommended_payment_method: PaymentMethod
    payment_plan: str
    earliest_date_for_full_payment: IsoDate | None = None
    spending_changes_needed: str
    decision_explanation: str


class PaymentOption(_Row):
    """``request_payment_options.csv`` — 9 columns.

    ``payment_frequency_days`` is blank for a single-payment option.
    """

    payment_option_id: str
    request_id: str
    payment_method: PaymentMethod
    payment_amount: Money
    number_of_payments: int
    first_payment_date: IsoDate
    payment_frequency_days: int | None = None
    financing_fee: Money
    total_payable_amount: Money

    @model_validator(mode="after")
    def _multi_payment_needs_a_frequency(self) -> PaymentOption:
        if self.number_of_payments > 1 and self.payment_frequency_days is None:
            raise ValueError(
                "an option with more than one payment needs payment_frequency_days"
            )
        return self


class Message(_Row):
    """``messages.csv`` — 7 columns.

    ``related_event_id`` is populated only when the message describes one
    supplied event row; blank means no one-to-one event exists.
    """

    message_id: str
    user_id: str
    request_id: str | None = None
    related_event_id: str | None = None
    sent_at: AwareDateTime
    # Open vocabulary.
    source_type: str
    # Untrusted content. Never enters a system prompt.
    message_text: str


class ImageLink(_Row):
    """``images.csv`` — 4 columns."""

    image_id: str
    user_id: str
    request_id: str
    related_event_id: str


class DatasetBundle(_Row):
    """Everything loaded, in one immutable container."""

    profiles: tuple[FinancialProfile, ...]
    events: tuple[FinancialEvent, ...]
    exchange_rates: tuple[ExchangeRate, ...]
    requests: tuple[Request, ...]
    sample_requests: tuple[SampleRequest, ...]
    payment_options: tuple[PaymentOption, ...]
    messages: tuple[Message, ...]
    images: tuple[ImageLink, ...]

    def profile_by_user(self) -> dict[str, FinancialProfile]:
        return {p.user_id: p for p in self.profiles}

    def event_by_id(self) -> dict[str, FinancialEvent]:
        return {e.event_id: e for e in self.events}

    def image_by_related_event(self) -> dict[str, ImageLink]:
        return {i.related_event_id: i for i in self.images}

    def options_by_request(self) -> dict[str, tuple[PaymentOption, ...]]:
        grouped: dict[str, list[PaymentOption]] = {}
        for option in self.payment_options:
            grouped.setdefault(option.request_id, []).append(option)
        return {k: tuple(v) for k, v in grouped.items()}
