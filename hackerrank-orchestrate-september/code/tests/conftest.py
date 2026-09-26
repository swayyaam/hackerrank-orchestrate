"""Shared test setup.

Puts the submission root on ``sys.path`` so ``import bow`` resolves without
installing the package, and stops CPython writing ``__pycache__`` directories
into the submission root, which ``test_submission_hygiene.py`` forbids.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True

SUBMISSION_ROOT = Path(__file__).resolve().parent.parent
if str(SUBMISSION_ROOT) not in sys.path:
    sys.path.insert(0, str(SUBMISSION_ROOT))
