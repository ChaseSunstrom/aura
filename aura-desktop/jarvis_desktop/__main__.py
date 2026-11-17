"""``python -m jarvis_desktop ...`` is ``python -m aura_desktop ...`` (see the package docstring)."""
from __future__ import annotations

from aura_desktop.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
