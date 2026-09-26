"""Closed vocabularies.

Two tiers, and the difference is deliberate.

**Spec enums** are contract. The problem statement defines them, so an unknown
value means the input is not the problem we were given, and we stop.

**Dataset-vocabulary enums that drive hard rules** are also strict, because an
unrecognised value would silently change a financial decision rather than
merely appear in a report. ``Flexibility`` is the clearest case: an unknown
value quietly treated as ``fixed`` would change which spending changes are
legal. It raises instead.

Everything else the data supplies — ``category``, ``description``,
``source_type`` — stays a plain ``str``. Those are samples of an open
distribution, not contract; they are counted in the run report so a new value
is visible, never fatal.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "AffordabilityStatus",
    "AmountSafeBasis",
    "BalanceAsOfBasis",
    "CashDateBasis",
    "CashFlowSource",
    "HorizonEndBasis",
    "PendingDuplicatePolicy",
    "Direction",
    "DropReason",
    "EventStatus",
    "EventType",
    "EvidenceEffect",
    "EvidenceSourceType",
    "Flexibility",
    "KeepReason",
    "PaymentMethod",
    "RequestType",
    "UnresolvedReason",
]


# --- spec enums ---------------------------------------------------------------


class AffordabilityStatus(StrEnum):
    """Output column ``affordability_status``."""

    AFFORDABLE_NOW = "affordable_now"
    AFFORDABLE_WITH_PLAN = "affordable_with_plan"
    AFFORDABLE_LATER = "affordable_later"
    NOT_AFFORDABLE = "not_affordable"


class PaymentMethod(StrEnum):
    """Output column ``recommended_payment_method``.

    The same vocabulary names the methods a user will consider and the methods
    a seller offers. Those are subsets in practice, but the spec never declares
    them closed subsets, so no subset is enforced here; a value outside the
    expected subset is counted as a warning by the loader instead.
    """

    FULL_PAYMENT = "full_payment"
    PARTIAL_PAYMENT = "partial_payment"
    INSTALLMENTS = "installments"
    WAIT = "wait"
    NOT_RECOMMENDED = "not_recommended"


class RequestType(StrEnum):
    """Input column ``request_type``."""

    PURCHASE = "purchase"
    TRAVEL = "travel"
    EDUCATION = "education"
    FAMILY_TRANSFER = "family_transfer"
    DEBT_REPAYMENT = "debt_repayment"
    INVESTMENT = "investment"
    HOUSING = "housing"
    EMERGENCY_EXPENSE = "emergency_expense"
    OTHER = "other"


# --- dataset vocabularies that drive hard rules -------------------------------


class EventStatus(StrEnum):
    """``financial_events.status``. Drives the cash-treatment rules."""

    SETTLED = "settled"
    PENDING = "pending"
    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"
    FAILED = "failed"
    UNREALIZED = "unrealized"


class Direction(StrEnum):
    """``financial_events.direction``. Carries the sign; amounts are unsigned."""

    DEBIT = "debit"
    CREDIT = "credit"
    NON_CASH = "non_cash"


class EventType(StrEnum):
    """``financial_events.event_type``."""

    EXPENSE = "expense"
    SUBSCRIPTION = "subscription"
    INCOME = "income"
    DEBT_PAYMENT = "debt_payment"
    REFUND = "refund"
    INVESTMENT_PURCHASE = "investment_purchase"
    INVESTMENT_SALE = "investment_sale"
    INVESTMENT_VALUATION = "investment_valuation"


class Flexibility(StrEnum):
    """``financial_events.flexibility``.

    Strict on purpose. An unrecognised value must never be silently treated as
    ``fixed``, because that quietly changes which spending changes are legal.
    """

    FIXED = "fixed"
    REDUCIBLE = "reducible"
    STOPPABLE = "stoppable"
    REDUCIBLE_OR_STOPPABLE = "reducible_or_stoppable"

    @property
    def is_reducible(self) -> bool:
        return self in (Flexibility.REDUCIBLE, Flexibility.REDUCIBLE_OR_STOPPABLE)

    @property
    def is_stoppable(self) -> bool:
        return self in (Flexibility.STOPPABLE, Flexibility.REDUCIBLE_OR_STOPPABLE)

    @property
    def is_flexible(self) -> bool:
        return self is not Flexibility.FIXED


# --- normalization outcomes ---------------------------------------------------


class KeepReason(StrEnum):
    """Why a normalized event still counts toward cash flow."""

    SETTLED = "settled"
    SCHEDULED_COMMITMENT = "scheduled_commitment"
    PENDING_DEBIT_RESERVED = "pending_debit_reserved"


class DropReason(StrEnum):
    """Why a normalized event does not count toward cash flow."""

    CANCELLED_OR_FAILED = "cancelled_or_failed"
    UNREALIZED_OR_NON_CASH = "unrealized_or_non_cash"
    PENDING_CREDIT = "pending_credit"
    EXACT_DUPLICATE = "exact_duplicate"
    LINKED_DUPLICATE_PENDING_DEBIT = "linked_duplicate_pending_debit"


# --- evidence -----------------------------------------------------------------


class EvidenceSourceType(StrEnum):
    """Where one piece of evidence came from."""

    EVENT = "event"
    PROFILE = "profile"
    REQUEST = "request"
    PAYMENT_OPTION = "payment_option"
    EXCHANGE_RATE = "exchange_rate"
    MESSAGE = "message"
    IMAGE = "image"


class EvidenceEffect(StrEnum):
    """What a piece of evidence did to the financial picture.

    The first five mirror the problem statement's own list of what a message or
    image may do. The rest are what the deterministic layers do.
    """

    CLARIFY = "clarify"
    AMEND = "amend"
    CANCEL = "cancel"
    DELAY = "delay"
    CONFIRM = "confirm"
    EXTRACT = "extract"
    CONVERT = "convert"
    DROP = "drop"
    KEEP = "keep"


class UnresolvedReason(StrEnum):
    """Why something could not be resolved. Fail closed, never default-fill."""

    MISSING_AMOUNT = "missing_amount"
    MISSING_SOURCE_DOCUMENT = "missing_source_document"
    EXTRACTION_FAILED = "extraction_failed"
    RECONCILIATION_FAILED = "reconciliation_failed"
    CONFLICTING_RECORDS = "conflicting_records"


# --- decision vocabularies ----------------------------------------------------
#
# These mirror entries in ``config/decisions.yaml``. They are strict for the
# same reason ``Flexibility`` is: an unrecognised value would silently pick a
# branch rather than announce that the configuration is wrong.


class CashDateBasis(StrEnum):
    """``Q16_cash_date_basis``.

    Selects the date an event hits the balance **and**, by the same token, the
    date the exchange-rate lookup keys on. The two are one decision; letting
    them diverge would convert on a different day from the one the money moves.
    """

    SETTLEMENT_DATE = "settlement_date"
    EVENT_DATE = "event_date"


class PendingDuplicatePolicy(StrEnum):
    """``Q1_pending_duplicate_debit``.

    The spec says both "reserve pending debits" and "ignore duplicate records".
    This selects which wins when one row is both.
    """

    RESERVE = "reserve"
    IGNORE_AS_DUPLICATE = "ignore_as_duplicate"


class BalanceAsOfBasis(StrEnum):
    """``Q8_balance_as_of_semantics``.

    Decides whether an event whose cash date falls exactly on ``request_date``
    is already inside ``current_available_balance`` or still to be applied.
    Getting this boundary wrong double-counts, or drops, a day of cash.
    """

    INCLUSIVE_THROUGH_REQUEST_DATE = "inclusive_through_request_date"
    AS_OF_LAST_SETTLED_EVENT = "as_of_last_settled_event"


class HorizonEndBasis(StrEnum):
    """``Q24_horizon_end_basis``.

    Whether the safety window ends at a point fixed by the request, or slides
    forward with the payment date under evaluation.
    """

    FIXED_FROM_REQUEST_DATE = "fixed_from_request_date"
    SLIDING_FROM_PAYMENT_DATE = "sliding_from_payment_date"


class AmountSafeBasis(StrEnum):
    """``Q10_amount_safe_basis``."""

    STANDALONE_TODAY = "standalone_today"
    FIRST_LEG_OF_PLAN = "first_leg_of_plan"


class CashFlowSource(StrEnum):
    """Where a forecast cash flow came from. The boundary is explicit."""

    PROJECTED = "projected"
    GIVEN_COMMITMENT = "given_commitment"
    AMENDMENT = "amendment"
    #: Essential variable spending, forecast at a trailing rate rather than on
    #: a cadence. Kept distinct so it can never be confused with a dated
    #: commitment.
    VARIABLE_SPEND = "variable_spend"
