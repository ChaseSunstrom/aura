#!/usr/bin/env python3
"""Fail on a broken relative link, anchor or image across ``docs/`` and the root
markdown files. Dependency-free (stdlib only) so ``make docs-check`` runs anywhere,
CI included, without installing a docs toolchain.

What it checks, for every ``.md`` under ``docs/`` and every ``*.md`` at the repo root:

* **Relative links and images** — ``[t](path)`` / ``![t](path)`` whose target is not a
  URL (``http(s):``, ``mailto:``, ``tel:``, ``//host``) must resolve to a file or
  directory in the tree, relative to the linking file's directory.
* **Anchors** — a ``#fragment`` (alone, or on a ``.md`` target) must match a heading in
  the destination file, by the GitHub slug rule. Unknown-anchor into a non-markdown
  target is not checked.

What it deliberately does not do: validate external URLs (offline, and not this
check's job). Fenced code blocks are skipped so example links in snippets do not
register as broken.

It also checks the claims register, ``docs/verification.md``: every repo path,
``file.py::test`` name and ``make`` target it cites as a proof must exist
(``_register_drift``). Fenced lines there are checked whole, since a proof is often
a command in a fence.

And the commands the pages document (``_command_drift``): a ``cd``, a ``for`` loop of
folders and a ``pip install -e``/``-r`` path must name something the tree has. It reads
more pages than the links check does (each tree's README, CLAUDE.md and ``docs/``, the
repo's skills and rules), and it does not run anything: a command that enters a real
folder and then fails is not this check's to find.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Add a path prefix here to exclude a whole subtree as a link *source*. Nothing is excluded now.
EXCLUDE_SOURCE_PREFIXES: tuple[str, ...] = ()

_LINK_RE = re.compile(r"!?\[[^\]]*\]\(\s*([^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_EXTERNAL_RE = re.compile(r"^(?:[a-z][a-z0-9+.-]*:|//|#!)", re.I)


def _iter_markdown() -> list[Path]:
    files = sorted(p for p in (ROOT / "docs").rglob("*.md"))
    files += sorted(ROOT.glob("*.md"))
    return files


def _slug(text: str) -> str:
    """GitHub heading-anchor slug: drop inline markup, lowercase, spaces→'-', keep
    word chars and '-'."""
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"[*_]", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # [t](u) → t
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s+", "-", text)


def _anchors(path: Path, cache: dict[Path, set[str]]) -> set[str]:
    if path in cache:
        return cache[path]
    anchors: set[str] = set()
    counts: dict[str, int] = {}
    fenced = False
    if path.exists() and path.suffix == ".md":
        for line in path.read_text(errors="replace").splitlines():
            if line.lstrip().startswith("```"):
                fenced = not fenced
                continue
            if fenced:
                continue
            m = _HEADING_RE.match(line)
            if not m:
                continue
            base = _slug(m.group(2))
            n = counts.get(base, 0)
            anchors.add(base if n == 0 else f"{base}-{n}")
            counts[base] = n + 1
    cache[path] = anchors
    return anchors


def _links(path: Path) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    fenced = False
    for i, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        # strip inline code spans so `[x](y)` inside backticks is not treated as a link
        clean = re.sub(r"`[^`]*`", "", line)
        for m in _LINK_RE.finditer(clean):
            out.append((i, m.group(1)))
    return out


#: The claims register: every proof it cites must still exist. Without this, deleting a
#: script or a test left 162 rows citing proofs that no longer ran (T-0471).
REGISTER = "docs/verification.md"
_CODE_RE = re.compile(r"`([^`]+)`")
_MAKE_RE = re.compile(r"\bmake ([a-z][\w-]*)")
_LINE_REF_RE = re.compile(r":\d+(?:-\d+)?$")
#: A milestone gate cited by bare name, as the register's milestone rows do.
_GATE_RE = re.compile(r"^m\d+[\w-]*\.sh$")
_RETIRED_RE = re.compile(r"retired|deleted in", re.I)


def _ignored_names(root: Path) -> set[str]:
    """The plain names the root .gitignore ignores. Run output and local secrets are cited
    where they land (``.verify/live/results.json``, ``aura-core/.env``); CI's clean checkout
    has none of them, so docs-links failed there while passing on a dev tree (T-0496). Only
    plain names: globs and paths are not read."""
    try:
        lines = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    return {
        name.strip().rstrip("/")
        for name in lines
        if name.strip() and not name.lstrip().startswith(("#", "!"))
        and not any(c in name.strip().rstrip("/") for c in "/*?[")
    }


#: Where the docs say the checkout lives: a ``cd`` under it is read from the root.
CHECKOUT = "/opt/jarvis"
_PLAIN_PATH_RE = re.compile(r"^[A-Za-z0-9_.][A-Za-z0-9_./+-]*$")
_SEGMENT_RE = re.compile(r"&&|\|\||[;|()&]")
_FOR_RE = re.compile(r"\bfor\s+(\w+)\s+in\s+([^;]*?)\s*(?:;|$)")
#: Words a command may follow inside one simple command (``do cd x``, ``sh -c 'cd x'``).
_COMMAND_LEADS = {"do", "then", "else", "{", "!", "time", "sudo", "-c", "-lc", "$"}
_INSTALL_FLAGS = {"-e", "--editable", "-r", "--requirement"}


def _command_pages() -> list[Path]:
    """The pages whose commands are read: what the links check reads, each top-level tree's
    README, CLAUDE.md and ``docs/``, and this repo's skills and rules. Fixed globs, so a
    checkout and a dev tree with ignored folders in it read the same set."""
    pages = [
        p for p in _iter_markdown()
        if not p.relative_to(ROOT).as_posix().startswith(EXCLUDE_SOURCE_PREFIXES)
    ]
    for tree in sorted(d for d in ROOT.iterdir() if d.is_dir() and not d.name.startswith(".")):
        if tree.name == "docs":
            continue
        pages += [p for p in (tree / "README.md", tree / "CLAUDE.md") if p.is_file()]
        pages += sorted((tree / "docs").rglob("*.md")) if (tree / "docs").is_dir() else []
    pages += sorted((ROOT / ".claude" / "skills").glob("*/SKILL.md"))
    pages += sorted((ROOT / ".claude" / "rules").glob("*.md"))
    return pages


def _command_blocks(path: Path) -> list[list[tuple[int, str]]]:
    """The commands a page documents: each fence is one block of (line, command), with a
    trailing-backslash continuation joined to the line it continues; outside a fence each
    backticked span is a block of its own. A line that says "retired" or "deleted in" is
    left out, as in the register."""
    blocks: list[list[tuple[int, str]]] = []
    fence: list[tuple[int, str]] | None = None
    carry: tuple[int, str] | None = None
    for i, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        if line.lstrip().startswith("```"):
            if fence is None:
                fence = []
            else:
                blocks.append(fence)
                fence = None
            carry = None
            continue
        if fence is None:
            if not _RETIRED_RE.search(line):
                blocks += [[(i, span)] for span in _CODE_RE.findall(line)]
            continue
        start, text = carry if carry else (i, "")
        text += line
        if text.endswith("\\"):
            carry = (start, text[:-1] + " ")
            continue
        carry = None
        if not _RETIRED_RE.search(text):
            fence.append((start, text))
    if fence:
        blocks.append(fence)  # an unclosed fence still documents its commands
    return blocks


def _plain(word: str) -> str:
    """The path a shell word names, or "" when it is not a plain one (a variable, a glob, a
    placeholder, ``-`` or ``~``)."""
    word = word.strip("\"'`")
    word = word.rstrip("/") or word  # `aura-core/` is aura-core; a bare `/` stays unreadable
    return word if _PLAIN_PATH_RE.match(word) else ""


def _block_drift(block: list[tuple[int, str]], bases: list[Path], root: Path,
                 ignored: set[str]) -> list[tuple[int, str]]:
    entered: list[Path] = []  # where an earlier cd of this block went
    made: set[str] = set()    # what an earlier command of this block made

    def find(target: str, want_dir: bool) -> Path | None:
        for base in bases + entered:
            hit = base / target
            if hit.is_dir() if want_dir else hit.exists():
                return hit
        return None

    def skipped(target: str) -> bool:
        return bool(ignored.intersection(target.split("/"))) or any(
            target == m or target.startswith(m + "/") for m in made
        )

    out: list[tuple[int, str]] = []
    for index, (lineno, text) in enumerate(block):
        for match in _FOR_RE.finditer(text):
            var, words = match.group(1), match.group(2).split()
            body = text[match.end():]
            if not re.search(r"\bdone\b", body):
                for _, later in block[index + 1:]:
                    body += "\n" + later
                    if re.search(r"\bdone\b", later):
                        break
            if not re.search(r"\bcd\s+[\"']?\$\{?" + re.escape(var) + r"\b", body):
                continue
            for word in words:
                target = _plain(word)
                if target and not target.startswith("..") and not skipped(target) \
                        and find(target, True) is None:
                    out.append((lineno, f"missing folder -> for {var} in ... {target}"))
        for segment in _SEGMENT_RE.split(text):
            words = [w.strip("\"'`") for w in segment.split()]
            words = [w for w in words if w]
            for at, word in enumerate(words):
                led = at == 0 or words[at - 1] in _COMMAND_LEADS
                rest = words[at + 1:]
                if word == "mkdir" and led:
                    made.update(w.rstrip("/") for w in rest if not w.startswith("-"))
                elif word == "clone" and at and words[at - 1] == "git":
                    args = [w for w in rest if not w.startswith("-")]
                    if args:
                        named = args[-1] if len(args) > 1 else args[0].rsplit("/", 1)[-1]
                        made.add(named.removesuffix(".git").rstrip("/"))
                elif word == "cd" and led and rest:
                    raw = rest[0]
                    under = raw == CHECKOUT or raw.startswith(CHECKOUT + "/")
                    target = _plain(raw.removeprefix(CHECKOUT).lstrip("/") if under else raw)
                    if raw == CHECKOUT or not target or target.startswith("..") or skipped(target):
                        continue
                    hit = (root / target) if under else find(target, True)
                    if hit is not None and hit.is_dir():
                        entered.append(hit)
                    else:
                        out.append((lineno, f"missing folder -> cd {raw}"))
                elif word == "install" and at and words[at - 1].rsplit("/", 1)[-1] in ("pip", "pip3"):
                    for flag, value in zip(rest, rest[1:]):
                        target = _plain(value.split("[", 1)[0])
                        if flag in _INSTALL_FLAGS and target and not target.startswith("..") \
                                and not skipped(target) and find(target, False) is None:
                            out.append((lineno, f"missing path -> pip install {target}"))
    return out


def _command_drift(files: list[Path] | None = None, root: Path | None = None) -> list[str]:
    """Documented commands that name a folder the tree does not have: a ``cd`` target, each
    folder of a ``for`` loop whose body enters its variable, and a path given to
    ``pip install -e`` or ``-r``. A relative path is read from the repository root, from the
    page's own folder and from wherever an earlier ``cd`` of the same fence went, and passes
    if one of them has it; ``cd /opt/jarvis/x`` is read from the root.

    T-0960 renamed five folders; the runbooks' ``cd`` lines, two loops and an install hint
    named the old ones until a person tried them (T-1434). Not covered: whether the command
    after the ``cd`` works; a target that is a variable, a glob, a placeholder, ``~``, ``..``
    or an absolute path outside the documented checkout; a folder an earlier command of the
    same block makes (``mkdir``, ``git clone``; an unpacked archive or a new worktree is not
    known and fails here); a line that says "retired" or "deleted in" anywhere; a path
    through a name the root .gitignore ignores; ``cd`` in prose outside backticks or a fence
    or in an indented block; a ``for`` loop that enters ``"$d"`` under another folder; a
    relative ``cd`` meant for a container or another machine, which is read as this tree's;
    the wrong one of two folders with the same name (``tests`` passes under any base); and
    ``-C``, ``-f`` and every other way a command names a folder."""
    root = root or ROOT
    ignored = _ignored_names(root)
    out: list[str] = []
    for page in files if files is not None else _command_pages():
        rel = page.relative_to(root).as_posix()
        for block in _command_blocks(page):
            for lineno, what in _block_drift(block, [root, page.parent], root, ignored):
                out.append(f"{rel}:{lineno}: {what}")
    return out


#: A test file cited by bare name: pytest's two spellings, a console spec or unit test,
#: a Kotlin test class.
_TEST_FILE_RE = re.compile(r"^(?:test_\w+\.py|\w+_test\.py|[\w.+-]+\.(?:test|spec)\.ts|\w+Test\.kt)$")


def _file_names(root: Path, ignored: set[str]) -> set[str]:
    """Every file name under ``root``, without walking ``.git`` or a name the root
    .gitignore ignores (``node_modules`` alone holds thousands of ``*.test.ts``)."""
    names: set[str] = set()
    for _folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != ".git" and d not in ignored]
        names.update(files)
    return names


def _register_drift() -> list[str]:
    """Backticked repo paths, ``file.py::test`` names and ``make`` targets in the
    register that do not exist. A path is read from the root or from any top-level
    tree whose first directory it names (``e2e/x.spec.ts`` is jarvis-web's, a bare
    ``tests/x.py`` any tree's), and passes if one of them has it. A test file cited by
    bare name (``test_agents.py``, ``home.spec.ts``, ``XTest.kt``) must exist somewhere
    in the tree, and a ``x/tests…`` path must have its ``x``. Not covered: any other
    word whose first directory no tree has; a path or test name that exists in the
    wrong tree (``scripts/x.sh`` passes on a sub-tree's copy); for a bare test file,
    which tree has it and whether its ``::test`` exists; a bare test file a local
    untracked copy keeps alive, which passes here and fails in CI; a bare gate
    (``m85-….sh``), a bare test file or a ``tests`` path on a line that says "retired"
    or "deleted in" anywhere, even about
    another proof; ``make x`` in prose outside backticks or a fence; a directory a local
    leftover keeps alive (an ignored ``__pycache__``), which passes here and fails in CI;
    and a path through a name the root .gitignore ignores (``.verify/``, ``.env``), which
    is skipped because a checkout never has it. A bare gate is read as scripts/verify/'s."""
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    targets = set(re.findall(r"^([a-z][\w-]*):", makefile, re.M))
    trees = [ROOT] + sorted(d for d in ROOT.iterdir() if d.is_dir() and not d.name.startswith("."))
    ignored = _ignored_names(ROOT)
    test_files = _file_names(ROOT, ignored)
    out: list[str] = []
    fenced = False
    for i, line in enumerate((ROOT / REGISTER).read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        spans = [line] if fenced else _CODE_RE.findall(line)
        for span in spans:
            for target in _MAKE_RE.findall(span):
                if target not in targets:
                    out.append(f"{REGISTER}:{i}: no make target -> {target}")
            for word in span.split():
                word = _LINE_REF_RE.sub("", word.strip(",;()'\"").rstrip(".:"))
                # Gates lived in scripts/verify/; a bare one cited as live was the
                # milestone rows' blind spot (T-0484: M85 read Built on a deleted gate).
                if _GATE_RE.match(word):
                    if not (ROOT / "scripts/verify" / word).exists() and not _RETIRED_RE.search(line):
                        out.append(f"{REGISTER}:{i}: missing gate -> {word}")
                    continue
                # A test cited by bare name names no tree to look in, so a deleted one went on
                # vouching: 73 citations of 25 deleted files read Automated (T-1441). It must
                # exist somewhere; which tree, and the test's own name, are still not read.
                bare = word.partition("::")[0]
                if "/" not in bare and _TEST_FILE_RE.match(bare):
                    if bare not in test_files and not _RETIRED_RE.search(line):
                        out.append(f"{REGISTER}:{i}: missing test file -> {bare}")
                    continue
                if "/" not in word or any(c in word for c in "*<{…$=|"):
                    continue
                path, _, test = word.partition("::")
                path = path.removeprefix("./")  # `./scripts/x.sh` is the root's
                head = path.split("/", 1)[0]
                if head in ("", ".", ".."):
                    continue
                bases = [b for b in trees if (b / head).is_dir()]
                if not bases:
                    # A word under a folder no tree has is usually a shortened path from a
                    # deeper tree (`routes/…`, `lib/…`) and is not read. A `tests` folder
                    # right under it is a suite, and its tree is gone (T-1441: the browser
                    # and orchestrator suites, retired in September, cited as Automated).
                    if path.rstrip("/").split("/")[1:2] == ["tests"] and not _RETIRED_RE.search(line) \
                            and not ignored.intersection(path.rstrip("/").split("/")):
                        out.append(f"{REGISTER}:{i}: missing proof -> {path.rstrip('/')}")
                    continue
                # A word whose first directory only a sub-tree has is read as a path only
                # when it looks like one (a file or a trailing slash): websocket commands
                # such as jarvis/test/catalog_mode share heads with the package trees.
                if not (ROOT / head).is_dir() and not (
                    path.endswith("/") or "." in path.rsplit("/", 1)[-1]
                ):
                    continue
                found = [b / path for b in bases if (b / path).exists()]
                if not found:
                    if not ignored.intersection(path.rstrip("/").split("/")):
                        out.append(f"{REGISTER}:{i}: missing proof -> {path}")
                elif test and path.endswith(".py"):
                    name = test.split("::")[-1].split("[")[0]
                    pat = re.compile(rf"def {re.escape(name)}\b")
                    if not any(pat.search(f.read_text(encoding="utf-8")) for f in found):
                        out.append(f"{REGISTER}:{i}: missing test -> {word}")
    return out


def main() -> int:
    cache: dict[Path, set[str]] = {}
    broken: list[str] = _register_drift() + _command_drift()
    checked = 0

    for md in _iter_markdown():
        rel = md.relative_to(ROOT).as_posix()
        if any(rel.startswith(p) for p in EXCLUDE_SOURCE_PREFIXES):
            continue
        for lineno, target in _links(md):
            if _EXTERNAL_RE.match(target):
                continue
            checked += 1
            frag = ""
            if "#" in target:
                target, frag = target.split("#", 1)

            if target == "":
                dest = md  # pure "#anchor" → same file
            else:
                dest = (md.parent / target).resolve()

            where = f"{rel}:{lineno}"
            if target and not dest.exists():
                broken.append(f"{where}: missing target -> {target}")
                continue
            if frag and dest.suffix == ".md":
                if _slug(frag) not in _anchors(dest, cache):
                    broken.append(f"{where}: missing anchor -> #{frag} in {dest.relative_to(ROOT).as_posix()}")

    if broken:
        print(f"check-links: {len(broken)} broken link(s) of {checked} checked:\n")
        for b in broken:
            print(f"  {b}")
        return 1
    print(f"check-links: OK ({checked} relative links across {len(_iter_markdown())} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
