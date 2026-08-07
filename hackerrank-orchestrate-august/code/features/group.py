"""Group membership role and mute state."""

from __future__ import annotations

from schemas import MemberRole, Membership


def is_admin(membership: Membership | None) -> bool:
    """Whether the membership carries the admin role. False when absent."""
    return membership is not None and membership.role is MemberRole.ADMIN


def muted_by_receiver(membership: Membership | None) -> bool:
    """Whether the receiver has muted this conversation. False when absent."""
    return membership is not None and membership.group_muted_by_user
