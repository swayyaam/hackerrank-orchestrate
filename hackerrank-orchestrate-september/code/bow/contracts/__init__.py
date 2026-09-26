"""Pydantic contracts. This package imports nothing else from ``bow``.

Everything here is frozen and closed (``extra="forbid"``), so a column that
appears without warning is an error rather than a silently ignored field.
"""

from __future__ import annotations

from bow.contracts.dataset import (
    DatasetBundle,
    ExchangeRate,
    FinancialEvent,
    FinancialProfile,
    ImageLink,
    Message,
    PaymentOption,
    Request,
    SampleRequest,
)
from bow.contracts.decision import Decision
from bow.contracts.enums import (
    AffordabilityStatus,
    AmountSafeBasis,
    BalanceAsOfBasis,
    CashDateBasis,
    CashFlowSource,
    HorizonEndBasis,
    Direction,
    DropReason,
    EventStatus,
    EventType,
    EvidenceEffect,
    EvidenceSourceType,
    Flexibility,
    KeepReason,
    PaymentMethod,
    PendingDuplicatePolicy,
    RequestType,
    UnresolvedReason,
)
from bow.contracts.evidence import EvidenceEntry, EvidenceLedger, UnresolvedItem
from bow.contracts.projection import (
    Cadence,
    NotProjectedReason,
    ProjectedEvent,
    ProjectionOrigin,
    SeriesClassification,
    SeriesKey,
)
from bow.contracts.report import (
    FileLoadStats,
    RunReport,
    VocabularyStats,
    WarningCount,
)
from bow.contracts.scalars import (
    AwareDateTime,
    CurrencyCode,
    IsoDate,
    Money,
    parse_money,
)

__all__ = [
    "AffordabilityStatus",
    "AmountSafeBasis",
    "AwareDateTime",
    "BalanceAsOfBasis",
    "Cadence",
    "CashFlowSource",
    "CashDateBasis",
    "CurrencyCode",
    "DatasetBundle",
    "Decision",
    "Direction",
    "DropReason",
    "EventStatus",
    "EventType",
    "EvidenceEffect",
    "EvidenceEntry",
    "EvidenceLedger",
    "EvidenceSourceType",
    "ExchangeRate",
    "FileLoadStats",
    "FinancialEvent",
    "FinancialProfile",
    "Flexibility",
    "HorizonEndBasis",
    "ImageLink",
    "IsoDate",
    "KeepReason",
    "Message",
    "Money",
    "NotProjectedReason",
    "PaymentMethod",
    "PaymentOption",
    "PendingDuplicatePolicy",
    "ProjectedEvent",
    "ProjectionOrigin",
    "Request",
    "RequestType",
    "RunReport",
    "SampleRequest",
    "SeriesClassification",
    "SeriesKey",
    "UnresolvedItem",
    "UnresolvedReason",
    "VocabularyStats",
    "WarningCount",
    "parse_money",
]
