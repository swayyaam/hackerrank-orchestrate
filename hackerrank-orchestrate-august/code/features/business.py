"""Business sender integrity and relationship facts."""

from __future__ import annotations

from schemas import BusinessProfile, BusinessRelationship, DomainState


def _normalise_domain(domain: str) -> str:
    """Case-fold, trim, drop a leading www. Nothing further.

    No lookalike-character analysis and no edit-distance threshold: any such
    threshold would be a constant with no defensible source.
    """
    return domain.strip().lower().removeprefix("www.")


def domain_state(business: BusinessProfile | None) -> DomainState:
    """MATCH, MISMATCH, or UNDETERMINED when either domain field is blank.

    UNDETERMINED is not MISMATCH. A business with no recorded official domain
    cannot be shown to be sending from a domain it does not own -- that is
    absence of evidence, not evidence of impersonation.
    """
    if business is None:
        return DomainState.UNDETERMINED
    official = business.official_domain
    used = business.domain_used_by_sender
    if official is None or used is None:
        return DomainState.UNDETERMINED
    official_n = _normalise_domain(official)
    used_n = _normalise_domain(used)
    if not official_n or not used_n:
        return DomainState.UNDETERMINED
    return DomainState.MATCH if official_n == used_n else DomainState.MISMATCH


def identity_consistent(business: BusinessProfile | None) -> bool:
    """Whether the account's display identity matches the brand it claims.

    True when there is no business (nothing inconsistent about a non-business
    conversation) or when display and brand agree.
    """
    if business is None:
        return True
    return business.display_name.strip().casefold() == business.brand_name.strip().casefold()


def promotions_opted_out(relationship: BusinessRelationship | None) -> bool:
    """Whether the receiver has withdrawn marketing consent from this sender."""
    if relationship is None:
        return False
    return (not relationship.allows_promotions) or relationship.promotions_opted_out_at is not None
