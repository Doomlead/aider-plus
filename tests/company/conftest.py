"""
Bootstrap the aider-plus source tree onto sys.path when pytest is invoked
from the repository root without an editable install active.

pytest discovers this conftest.py automatically for anything under tests/company/.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Repository root is two levels above this file: tests/company/conftest.py
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
