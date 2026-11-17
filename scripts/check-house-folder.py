#!/usr/bin/env python3
"""Stop `make up` while the house's own data is still under the folder's old name.

The house folder was `jarvis-core/` until T-0960 and is `aura-core/` now. Git moved the tracked
files. The env file, the voice models and the map data are untracked, so on a host that ran the
old tree they are still under `jarvis-core/` after a pull, and the house would start from a
folder with no env file and empty model directories: its settings are not read and the models
are fetched again. This prints what to move and exits 3; with nothing left it prints nothing.

Usage: check-house-folder.py [REPO_ROOT]

Does not cover: moving anything (the house may be running from the old folder: an operator stops
it first), telling a copy from the original when a file is in both folders, or keeping names
unprinted in a directory both folders hold data in (it is walked, to say which entries the new
one lacks).
"""
from __future__ import annotations

import os
import shlex
import sys

OLD, NEW = "jarvis-core", "aura-core"
#: What a checkout, a test run, an install or a file manager leaves behind that is nobody's data,
#: and the placeholder git tracks to keep an empty directory. Counting one would stop every start.
NOT_DATA = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".hypothesis",
            ".coverage", ".venv", ".DS_Store", ".gitkeep"}
NOT_DATA_SUFFIXES = (".pyc", ".egg-info")


def holds_data(path: str) -> bool:
    """True when [path] is a file, or a directory with a file anywhere under it, leftovers aside.
    Names only: no file is opened."""
    name = os.path.basename(path)
    if name in NOT_DATA or name.endswith(NOT_DATA_SUFFIXES):
        return False
    if not os.path.isdir(path) or os.path.islink(path):
        return True
    try:
        names = os.listdir(path)
    except OSError:
        # A directory a container's user owns and this user cannot list: it was made to hold data.
        return True
    return any(holds_data(os.path.join(path, name)) for name in names)


def same(old: str, new: str) -> bool:
    """True when the old path is the new one under another name (a symlink kept the old path
    alive): there is nothing to move, and calling it "in both folders" would stop every start."""
    try:
        return os.path.samefile(old, new)
    except OSError:
        return False


def leftovers(root: str, rel: str = "") -> tuple[list[str], list[str], list[str]]:
    """(entries to move, directories whose contents move, entries in both folders), as paths
    under the house folder. A directory the new folder lacks, or holds only as a placeholder
    (git tracks `maps-data/.gitkeep`; docker makes an empty one for a bind mount), is one entry
    and is not walked: the names inside it (a map file is named after its region) are not this
    script's to print."""
    move: list[str] = []
    into: list[str] = []
    both: list[str] = []
    try:
        names = sorted(os.listdir(os.path.join(root, OLD, rel)))
    except OSError:
        # In both folders and not listable here: a person with the rights compares them.
        return [], [], [rel]
    for name in names:
        sub = os.path.join(rel, name)
        old, new = os.path.join(root, OLD, sub), os.path.join(root, NEW, sub)
        if not holds_data(old) or same(old, new):
            continue
        if not os.path.lexists(new):
            move.append(sub)
        elif os.path.isdir(old) and not os.path.islink(old) and os.path.isdir(new):
            if not holds_data(new):
                into.append(sub)
                continue
            deeper = leftovers(root, sub)
            move += deeper[0]
            into += deeper[1]
            both += deeper[2]
        else:
            both.append(sub)
    return move, into, both


def main(argv: list[str]) -> int:
    root = argv[1] if len(argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not os.path.isdir(os.path.join(root, OLD)) or same(os.path.join(root, OLD), os.path.join(root, NEW)):
        return 0
    move, into, both = leftovers(root)
    if not move and not into and not both:
        return 0
    # A name with a line break or a control character would print as a second line, or as a
    # command nobody wrote: such an entry is shown escaped and left for a person.
    by_hand = [sub for sub in move + into if not sub.isprintable()]
    move = [sub for sub in move if sub.isprintable()]
    into = [sub for sub in into if sub.isprintable()]

    def old(sub: str) -> str:
        return shlex.quote(os.path.join(OLD, sub))

    def new(sub: str) -> str:
        return shlex.quote(os.path.join(NEW, sub))

    out = [f"make up: the house's data is still under {OLD}/, which is {NEW}/ since the folders were renamed.",
           f"Starting now would run the house from {NEW}/ without it (no env file, empty model folders).",
           "Stop the house first: make down, and if the maps, fixtures or another profile's services run,",
           f"  (cd {NEW} && docker compose --profile '*' down)",
           "Then, from the repository root:"]
    out += [f"  mv {old(sub)} {new(sub)}" for sub in move]
    out += [f"  mv {old(sub)}/* {new(sub)}/" for sub in into]
    if into:
        out.append("A line ending in /* moves what a folder holds into the one already there; a name "
                   "starting with a dot is not matched and needs its own mv.")
    if by_hand:
        out.append("Named with characters a command cannot show; move by hand:")
        out += [f"  {ascii(os.path.join(OLD, sub))}" for sub in by_hand]
    if both:
        out.append("In both folders; compare and keep one (the old folder's copy is not moved over the new one):")
        out += [f"  {os.path.join(OLD, sub) if sub.isprintable() else ascii(os.path.join(OLD, sub))}"
                for sub in both]
    out.append(f"Then remove what is left of {OLD}/ and run make up again.")
    print("\n".join(out), file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv))
