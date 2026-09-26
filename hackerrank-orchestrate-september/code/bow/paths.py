"""Path resolution for the Buy or Wait? submission.

Every path is derived from this module's own location on disk. Nothing here
reads the process working directory, and no absolute path from any particular
machine is baked in, so the tree behaves identically whether it is run from
``code/``, from the repository root, or from wherever ``code.zip`` was
unpacked.

Anchors
-------
``SOURCE_ROOT``      the ``bow`` package directory itself
``SUBMISSION_ROOT``  its parent — ``code/``, the directory that gets zipped
``REPO_ROOT``        the parent of that — where ``dataset/`` and ``.env`` live

``DATASET_DIR``, ``ENV_PATH`` and ``OUTPUT_PATH`` are the only paths that point
*outside* the submission root, and each for its own reason: the dataset is
provided and read-only; keeping the credential outside ``code/`` means zipping
``code/`` cannot capture it; and ``output.csv`` is a graded deliverable that
the README places at the run root beside ``code/``, not inside it.
``DATASET_DIR`` and ``ENV_PATH`` accept an environment override so the tree
still runs when ``code/`` is unpacked away from this repository.

Existence policy
----------------
``DATASET_DIR`` is verified at import: without it nothing downstream can run,
and the spec's fail-closed posture says say so immediately rather than degrade.
``ENV_PATH`` is *not* verified at import, because ``scripts/check_env.py``
exists precisely to diagnose a missing or malformed credential file and cannot
do that if importing this module raises first. Call :func:`require_env_path`
at the point of use.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    "SOURCE_ROOT",
    "SUBMISSION_ROOT",
    "REPO_ROOT",
    "CONFIG_DIR",
    "PROMPTS_DIR",
    "DOCS_DIR",
    "EVAL_DIR",
    "SCRIPTS_DIR",
    "TESTS_DIR",
    "CACHE_DIR",
    "OUTPUT_PATH",
    "OUTPUT_SAMPLE_PATH",
    "DATASET_DIR",
    "IMAGES_DIR",
    "ENV_PATH",
    "DATASET_DIR_ENV_VAR",
    "ENV_PATH_ENV_VAR",
    "PathResolutionError",
    "require_dataset_dir",
    "require_env_path",
    "image_path",
]

DATASET_DIR_ENV_VAR = "BOW_DATASET_DIR"
ENV_PATH_ENV_VAR = "BOW_ENV_PATH"

_DATASET_DIR_NAME = "dataset"
_IMAGES_SUBPATH = ("media", "images")
_ENV_FILE_NAME = ".env"


class PathResolutionError(RuntimeError):
    """A required path could not be resolved, and guessing would be worse."""


SOURCE_ROOT: Path = Path(__file__).resolve().parent
SUBMISSION_ROOT: Path = SOURCE_ROOT.parent
REPO_ROOT: Path = SUBMISSION_ROOT.parent

# --- inside the submission root; all of these ship in code.zip ---------------
CONFIG_DIR: Path = SUBMISSION_ROOT / "config"
PROMPTS_DIR: Path = SUBMISSION_ROOT / "prompts"
DOCS_DIR: Path = SUBMISSION_ROOT / "docs"
EVAL_DIR: Path = SUBMISSION_ROOT / "evaluation"
SCRIPTS_DIR: Path = SUBMISSION_ROOT / "scripts"
TESTS_DIR: Path = SUBMISSION_ROOT / "tests"
CACHE_DIR: Path = SUBMISSION_ROOT / ".cache"

#: Sample-set predictions. An analysis artifact, not a deliverable, so it stays
#: inside the submission root where the rest of the working files live.
OUTPUT_SAMPLE_PATH: Path = SUBMISSION_ROOT / "output_sample.csv"


def _resolve_dataset_dir() -> Path:
    override = os.environ.get(DATASET_DIR_ENV_VAR)
    if override:
        return Path(override).expanduser().resolve()
    return REPO_ROOT / _DATASET_DIR_NAME


def _resolve_env_path() -> Path:
    override = os.environ.get(ENV_PATH_ENV_VAR)
    if override:
        return Path(override).expanduser().resolve()
    return REPO_ROOT / _ENV_FILE_NAME


# --- outside the submission root ---------------------------------------------
DATASET_DIR: Path = _resolve_dataset_dir()
IMAGES_DIR: Path = DATASET_DIR.joinpath(*_IMAGES_SUBPATH)
ENV_PATH: Path = _resolve_env_path()

#: The graded predictions file. It sits at the run root — the directory holding
#: both ``dataset/`` and ``code/`` — because the README states that the blank
#: ``dataset/output.csv`` is a reference template and the generated file must be
#: the root-level ``output.csv``. `dataset/` is still never written to.
OUTPUT_PATH: Path = REPO_ROOT / "output.csv"


def require_dataset_dir() -> Path:
    """Return ``DATASET_DIR``, or raise naming the override variable."""
    if not DATASET_DIR.is_dir():
        raise PathResolutionError(
            f"dataset directory not found at {DATASET_DIR}. "
            f"Set {DATASET_DIR_ENV_VAR} to its location."
        )
    return DATASET_DIR


def require_env_path() -> Path:
    """Return ``ENV_PATH``, or raise naming the override variable."""
    if not ENV_PATH.is_file():
        raise PathResolutionError(
            f"environment file not found at {ENV_PATH}. "
            f"Set {ENV_PATH_ENV_VAR} to its location."
        )
    return ENV_PATH


def image_path(image_id: str) -> Path:
    """Resolve one dataset image by id. The id comes from data, never source."""
    return IMAGES_DIR / f"{image_id}.png"


# Fail loudly at import rather than silently carrying an empty path forward.
require_dataset_dir()
