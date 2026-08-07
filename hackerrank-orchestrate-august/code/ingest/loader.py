"""Read and validate all 13 CSVs; build the lookup indexes.

Also performs the sample-row projection (U2): sample_messages.csv's 11 input
columns become IncomingMessage rows routed on equal footing with the targets,
while its 5 label columns are held separately as the eval truth.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from errors import DatasetError
from schemas import (
    BusinessProfile,
    BusinessRelationship,
    ConversationType,
    EvalTruthRow,
    GroupProfile,
    HistoryRecord,
    IncomingMessage,
    InteractionEvent,
    MediaKind,
    MemberRole,
    Membership,
    UserProfile,
)

MESSAGE_INPUT_COLUMNS = (
    "message_id",
    "user_id",
    "conversation_type",
    "group_id",
    "business_id",
    "sender_user_id",
    "created_at",
    "message_text",
    "media_type",
    "media_id",
    "forwarded_count",
)
EVAL_LABEL_COLUMNS = (
    "action",
    "message_type",
    "reason",
    "confidence",
    "evidence_message_ids",
)

_DATETIME_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d")


def _blank_to_none(value: str) -> str | None:
    stripped = value.strip()
    return stripped or None


def _parse_dt(value: str, *, field: str, row_id: str) -> datetime:
    """Parse a naive timestamp. No timezone conversion is performed (D15)."""
    text = value.strip()
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise DatasetError(f"{row_id}: {field}={value!r} is not a recognised timestamp")


def _parse_optional_dt(value: str, *, field: str, row_id: str) -> datetime | None:
    return None if not value.strip() else _parse_dt(value, field=field, row_id=row_id)


def _parse_bool01(value: str, *, field: str, row_id: str) -> bool:
    text = value.strip()
    if text == "0":
        return False
    if text == "1":
        return True
    raise DatasetError(f"{row_id}: {field}={value!r} is not 0 or 1")


def _parse_int(value: str, *, field: str, row_id: str) -> int:
    try:
        return int(value.strip())
    except ValueError as exc:
        raise DatasetError(f"{row_id}: {field}={value!r} is not an integer") from exc


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise DatasetError(f"required dataset file is missing: {path}")
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _require_columns(rows: Sequence[Mapping[str, Any]], required: Sequence[str], name: str) -> None:
    if not rows:
        raise DatasetError(f"{name} contains no data rows")
    missing = [column for column in required if column not in rows[0]]
    if missing:
        raise DatasetError(f"{name} is missing required columns: {missing}")


def _message_from_row(row: Mapping[str, str], source: str) -> IncomingMessage:
    message_id = row["message_id"].strip()
    row_id = f"{source}:{message_id}"
    media_type = row["media_type"].strip()
    try:
        media_kind = MediaKind(media_type)
    except ValueError as exc:
        raise DatasetError(f"{row_id}: media_type={media_type!r} is not a known kind") from exc
    try:
        conversation_type = ConversationType(row["conversation_type"].strip())
    except ValueError as exc:
        raise DatasetError(
            f"{row_id}: conversation_type={row['conversation_type']!r} is not a known type"
        ) from exc
    return IncomingMessage(
        message_id=message_id,
        user_id=row["user_id"].strip(),
        conversation_type=conversation_type,
        group_id=_blank_to_none(row["group_id"]),
        business_id=_blank_to_none(row["business_id"]),
        sender_user_id=_blank_to_none(row["sender_user_id"]),
        created_at=_parse_dt(row["created_at"], field="created_at", row_id=row_id),
        message_text=row["message_text"],
        media_kind=media_kind,
        media_id=_blank_to_none(row["media_id"]),
        forwarded_count=_parse_int(row["forwarded_count"], field="forwarded_count", row_id=row_id),
    )


@dataclass(frozen=True)
class Dataset:
    """Parsed tables plus prebuilt indexes. Frozen; loaded once per run."""

    targets: tuple[IncomingMessage, ...]
    eval_messages: tuple[IncomingMessage, ...]
    eval_truth: tuple[EvalTruthRow, ...]

    users: Mapping[str, UserProfile]
    groups: Mapping[str, GroupProfile]
    businesses: Mapping[str, BusinessProfile]
    images: Mapping[str, str]
    voice_notes: Mapping[str, str]

    membership_by_pair: Mapping[tuple[str, str], Membership]
    relationship_by_pair: Mapping[tuple[str, str], BusinessRelationship]
    history_by_user: Mapping[str, tuple[HistoryRecord, ...]]
    message_by_id: Mapping[str, IncomingMessage]
    baseline_dismissal_by_user: Mapping[str, float]

    fingerprint: str

    @property
    def target_order(self) -> tuple[str, ...]:
        return tuple(message.message_id for message in self.targets)


INPUT_CSVS: tuple[str, ...] = (
    "business_accounts.csv",
    "daily_notification_summary.csv",
    "group_members.csv",
    "groups.csv",
    "images.csv",
    "message_events.csv",
    "message_history.csv",
    "messages.csv",
    "sample_messages.csv",
    "user_business_history.csv",
    "users.csv",
    "voice_notes.csv",
)
"""The twelve CSVs the router reads.

``output.csv`` is deliberately excluded: it is the submission template, which
this pipeline overwrites. A fingerprint whose purpose is "did the inputs
change" must not include an artifact the run itself produces -- otherwise two
consecutive identical runs report different fingerprints, which is what the
Phase 0 determinism gate caught.
"""


def _fingerprint(root: Path) -> str:
    """sha256 over the twelve input CSVs, in a fixed order."""
    import hashlib

    digest = hashlib.sha256()
    for name in INPUT_CSVS:
        path = root / name
        digest.update(name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def load_dataset(root: Path) -> Dataset:
    """Read and validate all 13 CSVs; build lookup indexes.

    sample_messages.csv is loaded twice over: its 11 input columns are projected
    into IncomingMessage rows (the eval set, routed through the identical
    pipeline), and its 5 label columns are held separately as the eval truth,
    which emit copies into the run directory as eval_truth.csv.

    Raises:
        DatasetError: a file is missing or malformed, or a foreign key does not
            resolve. A broken join means the indexes are wrong, so the run
            aborts rather than routing suspect rows.
    """
    root = Path(root)

    users_rows = _read_csv(root / "users.csv")
    _require_columns(users_rows, ["user_id", "do_not_disturb_window"], "users.csv")
    users = {
        row["user_id"].strip(): UserProfile(
            user_id=row["user_id"].strip(),
            do_not_disturb_window=row["do_not_disturb_window"].strip(),
            messages_opened_30d=_parse_int(
                row["messages_opened_30d"], field="messages_opened_30d", row_id=row["user_id"]
            ),
            messages_replied_30d=_parse_int(
                row["messages_replied_30d"], field="messages_replied_30d", row_id=row["user_id"]
            ),
            notifications_dismissed_30d=_parse_int(
                row["notifications_dismissed_30d"],
                field="notifications_dismissed_30d",
                row_id=row["user_id"],
            ),
            messages_reported_30d=_parse_int(
                row["messages_reported_30d"], field="messages_reported_30d", row_id=row["user_id"]
            ),
        )
        for row in users_rows
    }

    group_rows = _read_csv(root / "groups.csv")
    groups = {
        row["group_id"].strip(): GroupProfile(
            group_id=row["group_id"].strip(),
            group_name=row["group_name"],
            group_type=row["group_type"].strip(),
            member_count=_parse_int(row["member_count"], field="member_count", row_id=row["group_id"]),
            admin_count=_parse_int(row["admin_count"], field="admin_count", row_id=row["group_id"]),
            created_at=_parse_dt(row["created_at"], field="created_at", row_id=row["group_id"]),
            messages_30d=_parse_int(row["messages_30d"], field="messages_30d", row_id=row["group_id"]),
        )
        for row in group_rows
    }

    business_rows = _read_csv(root / "business_accounts.csv")
    businesses = {
        row["business_id"].strip(): BusinessProfile(
            business_id=row["business_id"].strip(),
            display_name=row["display_name"],
            brand_name=row["brand_name"],
            category=row["category"].strip(),
            verified=_parse_bool01(row["verified"], field="verified", row_id=row["business_id"]),
            official_domain=_blank_to_none(row["official_domain"]),
            domain_used_by_sender=_blank_to_none(row["domain_used_by_sender"]),
            account_age_days=_parse_int(
                row["account_age_days"], field="account_age_days", row_id=row["business_id"]
            ),
            messages_sent_30d=_parse_int(
                row["messages_sent_30d"], field="messages_sent_30d", row_id=row["business_id"]
            ),
            user_reports_30d=_parse_int(
                row["user_reports_30d"], field="user_reports_30d", row_id=row["business_id"]
            ),
            domain_used_by_sender_age_days=_parse_int(
                row["domain_used_by_sender_age_days"],
                field="domain_used_by_sender_age_days",
                row_id=row["business_id"],
            ),
        )
        for row in business_rows
    }

    membership_by_pair: dict[tuple[str, str], Membership] = {}
    for row in _read_csv(root / "group_members.csv"):
        row_id = f"{row['group_id']}/{row['user_id']}"
        membership_by_pair[(row["group_id"].strip(), row["user_id"].strip())] = Membership(
            group_id=row["group_id"].strip(),
            user_id=row["user_id"].strip(),
            role=MemberRole(row["role"].strip()),
            joined_at=_parse_dt(row["joined_at"], field="joined_at", row_id=row_id),
            messages_sent_30d=_parse_int(row["messages_sent_30d"], field="messages_sent_30d", row_id=row_id),
            messages_read_30d=_parse_int(row["messages_read_30d"], field="messages_read_30d", row_id=row_id),
            replies_sent_30d=_parse_int(row["replies_sent_30d"], field="replies_sent_30d", row_id=row_id),
            notifications_dismissed_30d=_parse_int(
                row["notifications_dismissed_30d"], field="notifications_dismissed_30d", row_id=row_id
            ),
            group_muted_by_user=_parse_bool01(
                row["group_muted_by_user"], field="group_muted_by_user", row_id=row_id
            ),
        )

    relationship_by_pair: dict[tuple[str, str], BusinessRelationship] = {}
    for row in _read_csv(root / "user_business_history.csv"):
        row_id = f"{row['user_id']}/{row['business_id']}"
        relationship_by_pair[(row["user_id"].strip(), row["business_id"].strip())] = BusinessRelationship(
            user_id=row["user_id"].strip(),
            business_id=row["business_id"].strip(),
            why_user_knows_account=row["why_user_knows_account"],
            last_activity_at=_parse_dt(row["last_activity_at"], field="last_activity_at", row_id=row_id),
            allows_promotions=_parse_bool01(
                row["allows_promotions"], field="allows_promotions", row_id=row_id
            ),
            promotions_opted_out_at=_parse_optional_dt(
                row["promotions_opted_out_at"], field="promotions_opted_out_at", row_id=row_id
            ),
            activity_count_180d=_parse_int(
                row["activity_count_180d"], field="activity_count_180d", row_id=row_id
            ),
            messages_opened_30d=_parse_int(
                row["messages_opened_30d"], field="messages_opened_30d", row_id=row_id
            ),
            messages_dismissed_30d=_parse_int(
                row["messages_dismissed_30d"], field="messages_dismissed_30d", row_id=row_id
            ),
            messages_replied_30d=_parse_int(
                row["messages_replied_30d"], field="messages_replied_30d", row_id=row_id
            ),
            last_reply_at=_parse_optional_dt(row["last_reply_at"], field="last_reply_at", row_id=row_id),
        )

    events_by_id: dict[str, InteractionEvent] = {}
    for row in _read_csv(root / "message_events.csv"):
        row_id = row["message_id"]
        reaction = row["reaction_time_minutes"].strip()
        events_by_id[row_id.strip()] = InteractionEvent(
            user_id=row["user_id"].strip(),
            message_id=row_id.strip(),
            opened=_parse_bool01(row["message_opened"], field="message_opened", row_id=row_id),
            replied=_parse_bool01(row["message_replied"], field="message_replied", row_id=row_id),
            reaction_time_minutes=(
                None if not reaction else _parse_int(reaction, field="reaction_time_minutes", row_id=row_id)
            ),
            dismissed=_parse_bool01(
                row["notification_dismissed"], field="notification_dismissed", row_id=row_id
            ),
            muted_after=_parse_bool01(row["muted_after_message"], field="muted_after_message", row_id=row_id),
            reported=_parse_bool01(row["message_reported"], field="message_reported", row_id=row_id),
        )

    history_by_user: dict[str, list[HistoryRecord]] = {}
    for row in _read_csv(root / "message_history.csv"):
        message_id = row["message_id"].strip()
        event = events_by_id.get(message_id)
        if event is None:
            raise DatasetError(f"message_history:{message_id} has no matching message_events row")
        if event.user_id != row["user_id"].strip():
            raise DatasetError(
                f"message_history:{message_id} user_id={row['user_id']} disagrees with its event "
                f"user_id={event.user_id}"
            )
        record = HistoryRecord(
            message_id=message_id,
            user_id=row["user_id"].strip(),
            conversation_type=ConversationType(row["conversation_type"].strip()),
            group_id=_blank_to_none(row["group_id"]),
            business_id=_blank_to_none(row["business_id"]),
            sender_user_id=_blank_to_none(row["sender_user_id"]),
            created_at=_parse_dt(row["created_at"], field="created_at", row_id=message_id),
            message_text=row["message_text"],
            media_kind=MediaKind(row["media_type"].strip()),
            media_id=_blank_to_none(row["media_id"]),
            forwarded_count=_parse_int(row["forwarded_count"], field="forwarded_count", row_id=message_id),
            event=event,
        )
        history_by_user.setdefault(record.user_id, []).append(record)
    history_frozen = {
        user: tuple(sorted(records, key=lambda record: (record.created_at, record.message_id)))
        for user, records in sorted(history_by_user.items())
    }

    images = {row["image_id"].strip(): row["file_path"].strip() for row in _read_csv(root / "images.csv")}
    voice_notes = {
        row["voice_note_id"].strip(): row["file_path"].strip() for row in _read_csv(root / "voice_notes.csv")
    }

    sent: dict[str, int] = {}
    dismissed: dict[str, int] = {}
    for row in _read_csv(root / "daily_notification_summary.csv"):
        user = row["user_id"].strip()
        sent[user] = sent.get(user, 0) + _parse_int(
            row["notifications_sent"], field="notifications_sent", row_id=user
        )
        dismissed[user] = dismissed.get(user, 0) + _parse_int(
            row["notifications_dismissed"], field="notifications_dismissed", row_id=user
        )
    baseline_dismissal_by_user = {
        user: dismissed[user] / sent[user] for user in sorted(sent) if sent[user] > 0
    }

    target_rows = _read_csv(root / "messages.csv")
    _require_columns(target_rows, MESSAGE_INPUT_COLUMNS, "messages.csv")
    targets = tuple(_message_from_row(row, "messages.csv") for row in target_rows)

    sample_rows = _read_csv(root / "sample_messages.csv")
    _require_columns(
        sample_rows, (*MESSAGE_INPUT_COLUMNS, *EVAL_LABEL_COLUMNS), "sample_messages.csv"
    )
    eval_messages = tuple(_message_from_row(row, "sample_messages.csv") for row in sample_rows)
    eval_truth = tuple(
        EvalTruthRow(
            message_id=row["message_id"].strip(),
            action=row["action"].strip(),
            message_type=row["message_type"].strip(),
            reason=row["reason"],
            confidence=float(row["confidence"]),
            evidence_message_ids=row["evidence_message_ids"].strip(),
        )
        for row in sample_rows
    )

    message_by_id: dict[str, IncomingMessage] = {}
    for message in (*targets, *eval_messages):
        if message.message_id in message_by_id:
            raise DatasetError(f"duplicate message_id across target and eval sets: {message.message_id}")
        message_by_id[message.message_id] = message

    dataset = Dataset(
        targets=targets,
        eval_messages=eval_messages,
        eval_truth=eval_truth,
        users=users,
        groups=groups,
        businesses=businesses,
        images=images,
        voice_notes=voice_notes,
        membership_by_pair=membership_by_pair,
        relationship_by_pair=relationship_by_pair,
        history_by_user=history_frozen,
        message_by_id=message_by_id,
        baseline_dismissal_by_user=baseline_dismissal_by_user,
        fingerprint=_fingerprint(root),
    )
    _assert_referential_integrity(dataset)
    return dataset


def _assert_referential_integrity(dataset: Dataset) -> None:
    """Sweep every foreign key before any row is routed.

    A dangling key means the indexes are wrong, so every row is suspect and the
    run aborts (FAILURE_MODES #5).
    """
    for source, messages in (("messages.csv", dataset.targets), ("sample_messages.csv", dataset.eval_messages)):
        for message in messages:
            row_id = f"{source}:{message.message_id}"
            if message.user_id not in dataset.users:
                raise DatasetError(f"{row_id}: user_id={message.user_id} not in users.csv")
            if message.group_id is not None and message.group_id not in dataset.groups:
                raise DatasetError(f"{row_id}: group_id={message.group_id} not in groups.csv")
            if message.business_id is not None and message.business_id not in dataset.businesses:
                raise DatasetError(
                    f"{row_id}: business_id={message.business_id} not in business_accounts.csv"
                )
            if message.sender_user_id is not None and message.sender_user_id not in dataset.users:
                raise DatasetError(f"{row_id}: sender_user_id={message.sender_user_id} not in users.csv")
            if message.media_id is not None:
                table = dataset.images if message.media_kind is MediaKind.IMAGE else dataset.voice_notes
                if message.media_id not in table:
                    raise DatasetError(
                        f"{row_id}: media_id={message.media_id} not in the {message.media_kind} table"
                    )
