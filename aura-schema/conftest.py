"""Make ``aura_schema`` importable when the suite runs from this directory.

``cd aura-schema && python -m pytest tests`` collects tests under ``tests/``,
whose directory pytest puts on the path — not this one. Insert the package root
so ``import aura_schema`` works without an editable install.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
