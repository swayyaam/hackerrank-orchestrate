"""Which model reads a message.

``Q15_message_language_routing`` = ``route_on_binary_in_set_verdict``.

Detection was measured separating 45 non-English messages from 170 English ones
with no false positives, while naming the wrong language on 5 of them — an
in-set code for text that was not in that language. So the **binary** verdict is
used and the returned code is treated as unreliable detail.
"""

from __future__ import annotations

__all__ = ["Route", "choose_route"]

from enum import StrEnum


class Route(StrEnum):
    IN_SET = "in_set"
    OUT_OF_SET = "out_of_set"


def choose_route(detected_code: str | None, *, in_set: tuple[str, ...]) -> Route:
    """In-set only when detection names a language from the configured list.

    A null code, an error, or anything outside the list routes out-of-set. The
    specific code is used only to answer "is this one of ours", never to claim
    which language it actually is.
    """
    if not detected_code:
        return Route.OUT_OF_SET
    base = detected_code.split("-", 1)[0].lower()
    return Route.IN_SET if base in {c.lower() for c in in_set} else Route.OUT_OF_SET
