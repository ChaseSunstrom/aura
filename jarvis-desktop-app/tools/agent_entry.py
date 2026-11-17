"""The frozen agent's entry point: the same ``main`` the ``jarvis-desktop`` command runs.

PyInstaller needs a script, not a console-script entry; this is that script and
nothing else, so the binary the app carries (ADR-0051) is the CLI people had,
subcommand for subcommand.
"""

from __future__ import annotations

import sys

from aura_desktop.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
