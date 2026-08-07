"""Resolve a media id to a path confined to the dataset root."""

from __future__ import annotations

from pathlib import Path

from errors import MediaPathError
from schemas import MediaKind


def resolve_media_path(
    dataset_root: Path,
    relative_path: str,
    media_id: str,
    kind: MediaKind,
) -> Path:
    """Resolve to a real path under the dataset root.

    Both sides are fully resolved (symlinks included) before containment is
    checked, so a symlink pointing outside the tree is rejected.

    Raises:
        MediaPathError: the resolved path escapes the dataset root.
    """
    root = Path(dataset_root).resolve()
    candidate = (root / relative_path).resolve()
    if not candidate.is_relative_to(root):
        raise MediaPathError(
            f"{media_id} ({kind}): {relative_path!r} resolves to {candidate}, outside {root}"
        )
    return candidate
