"""Assemble the complete MessageContext for one message from the indexes."""

from __future__ import annotations

from errors import ContextError
from ingest.loader import Dataset
from schemas import MediaArtifact, MessageContext


def build_context(
    dataset: Dataset,
    message_id: str,
    media: MediaArtifact | None,
) -> MessageContext:
    """Assemble the full context for one message.

    The MediaArtifact is extracted before this call and passed in, so the frozen
    MessageContext is complete at construction.

    Raises:
        ContextError: the message id is unknown, or a join that must resolve
            does not.
    """
    message = dataset.message_by_id.get(message_id)
    if message is None:
        raise ContextError(f"{message_id} is not in the target or eval set")

    receiver = dataset.users.get(message.user_id)
    if receiver is None:
        raise ContextError(f"{message_id}: receiver {message.user_id} not in users")

    group = None
    receiver_membership = None
    sender_membership = None
    if message.group_id is not None:
        group = dataset.groups.get(message.group_id)
        if group is None:
            raise ContextError(f"{message_id}: group {message.group_id} not in groups")
        receiver_membership = dataset.membership_by_pair.get((message.group_id, message.user_id))
        if receiver_membership is None:
            raise ContextError(
                f"{message_id}: receiver {message.user_id} is not a member of {message.group_id}"
            )
        if message.sender_user_id is not None:
            sender_membership = dataset.membership_by_pair.get(
                (message.group_id, message.sender_user_id)
            )

    business = None
    business_relationship = None
    if message.business_id is not None:
        business = dataset.businesses.get(message.business_id)
        if business is None:
            raise ContextError(f"{message_id}: business {message.business_id} not in businesses")
        # Absent by design for a cold sender; not an error.
        business_relationship = dataset.relationship_by_pair.get(
            (message.user_id, message.business_id)
        )

    return MessageContext(
        message=message,
        receiver=receiver,
        group=group,
        receiver_membership=receiver_membership,
        sender_membership=sender_membership,
        business=business,
        business_relationship=business_relationship,
        history=dataset.history_by_user.get(message.user_id, ()),
        media=media,
        daily_load_baseline=dataset.baseline_dismissal_by_user.get(message.user_id),
    )
