#!/usr/bin/env python3
"""Fail when a tracked file holds a private value: a real address, host, person, number or key.

The repository is public. What may be in it is made up (192.0.2.x, example.com, .test, 555-01xx) or
is a public service the code talks to. Everything else of these shapes is a finding:

  address  a public IPv4 address outside the documentation ranges (a private-range one names no one
           by itself; the machine's list holds the prefixes that do)
  host     a URL's host that is neither made up nor on the list of public services (PUBLIC_HOSTS)
  email    an address at anything but an example domain
  phone    a phone number that is not a fictional one
  mac      a hardware address that is not a documentation or pattern value
  path     a home folder that names a person or a machine account
  key      a string shaped like a live credential or a private key
  link     a link to the project's own account that is not the public repository or its site
  local    a value from this machine's own list (see below)

What it does not cover: a private host written without a scheme, a person's name, a street address or
a coordinate cannot be told from ordinary text by shape. Those are caught only by the local list:
`.private-values` at the repository root, untracked, one `kind value` per line: `text` (anywhere,
any case), `word` (a whole word, any case), `name` (a whole word in exactly this case, for a name
that is also an ordinary word) and `coord lat,lon,km`. A checkout without that file runs the shape classes only and says so. Pictures
are not read at all: a screenshot of a real map has to be looked at by a person.

    python3 scripts/verify/private_values.py            # tracked files, shape classes
    python3 scripts/verify/private_values.py --local    # also this machine's list; fails without one
    python3 scripts/verify/private_values.py --show     # print the matched text, not its mask
"""
from __future__ import annotations

import argparse
import fnmatch
import functools
import ipaddress
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LOCAL_LIST = ".private-values"

# The project's own home: the public links, and nothing else. Any other link under the same
# account (a private repository, a gist, a fork) is a finding. The account is a person's handle,
# so it is typed nowhere but inside these links: PROJECT_OWNERS is read out of them, and a
# machine's own list, which holds the handle, does not find it inside one (`_own_links`).
PROJECT_LINKS: tuple[str, ...] = (
    "github.com/ChaseSunstrom/aura",
    "chasesunstrom.github.io/aura",
    "raw.githubusercontent.com/ChaseSunstrom/aura",
)
FORGES = ("github.com", "gitlab.com", "bitbucket.org", "codeberg.org", "raw.githubusercontent.com")
_PAGES = ".github.io"


def owners_of(links: tuple[str, ...]) -> tuple[str, ...]:
    """The accounts the links stand under: the first label of a Pages host, else the path's first part."""
    out: list[str] = []
    for link in links:
        host, _, rest = link.partition("/")
        who = (host[:-len(_PAGES)] if host.endswith(_PAGES) else rest.split("/", 1)[0]).lower()
        if who and who not in out:
            out.append(who)
    return tuple(out)


PROJECT_OWNERS: tuple[str, ...] = owners_of(PROJECT_LINKS)


@functools.lru_cache(maxsize=8)
def _own_links(links: tuple[str, ...]) -> re.Pattern[str] | None:
    """The public links as they stand in a line. A longer name (aura-private) is another repository."""
    if not links:
        return None
    return re.compile("(?:" + "|".join(re.escape(link) for link in links) + r")(?![A-Za-z0-9_-])", re.IGNORECASE)

# Networks whose addresses name no one. The private-use ranges are here whole: 192.168.50.7 is
# millions of houses' address, and what ties one to a person is their own prefix, which only the
# machine's list knows. A public address is different: it is one place, so outside the
# documentation ranges and the few everyone knows it is a finding.
MADE_UP_NETS = tuple(ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "127.0.0.0/8", "255.0.0.0/8", "224.0.0.0/4", "240.0.0.0/4", "169.254.0.0/16",
    "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "198.18.0.0/15",   # documentation, benchmarking
    "192.0.0.0/24",                                                          # IETF protocol assignments
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10",        # private use, carrier NAT
))
# Public addresses everyone knows: resolvers, the classic placeholder, an overlay's own resolver.
WELL_KNOWN = frozenset({"1.1.1.1", "1.0.0.1", "8.8.8.8", "8.8.4.4", "9.9.9.9", "1.2.3.4", "93.184.216.34"})
# The first address outside each private range: what a classifier's test feeds it to prove the
# edge. Public, but nobody's in particular.
EDGES = frozenset({"172.15.0.1", "172.32.0.1", "11.0.0.1", "192.169.0.1", "193.168.2.10", "169.253.1.1",
                   "100.63.255.255", "100.128.0.1", "100.200.0.1", "177.0.0.1"})

MADE_UP_HOST_SUFFIXES = (".test", ".example", ".invalid", ".localhost", ".internal")
MADE_UP_HOSTS = frozenset({"localhost", "example.com", "example.org", "example.net"})
MADE_UP_USERS = frozenset({
    "user", "username", "me", "you", "app", "node", "runner", "ubuntu", "debian", "pi", "vscode", "dev",
    "hermes", "jarvis", "aura", "agent", "sandbox", "coder", "gitlab-runner", "nonroot", "appuser",
    "alice", "bob", "owner", "someone", "example", "name", "builder", "worker", "test", "tester", "root",
    "linuxbrew", "runner-x", "shared", "public", "u", "x", "work", "deploy", "secret", "noreply",
})

# A name on the home network is made up when it is one of these plain words under a suffix no
# registry sells. A device's or a person's own name there is a finding.
LAN_SUFFIXES = (".lan", ".local", ".home", ".tail")
MADE_UP_LAN_LABELS = frozenset({
    "jarvis", "aura", "box", "cloud", "printer", "nas", "hub", "adapter", "searx", "grafana", "n8n", "home",
    "shop", "console", "homeassistant", "paperless", "a", "b", "my-nas", "example", "host", "server", "router",
    "evil-jarvis", "jarvis_box", "fixture",
})


def _read_public_hosts(path: Path) -> frozenset[str]:
    """Public services the code talks to or the docs cite, one registrable domain per line.

    A host under one of these is not private. Adding a line is a statement that the host is a public
    service; a missing file is an empty list, so everything is reported rather than nothing."""
    try:
        rows = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return frozenset()
    return frozenset(r.split("#", 1)[0].strip().lower() for r in rows if r.split("#", 1)[0].strip())


PUBLIC_HOSTS: frozenset[str] = _read_public_hosts(Path(__file__).with_name("public_hosts.txt"))

# (path glob, class, pattern of the matched text, why) for a match that is right where it is.
EXCEPTIONS: tuple[tuple[str, str, str, str], ...] = (
    ("aura-voice/Dockerfile", "address", r"^11\.(0\.2\.54|2\.1\.3)$", "cuFFT versions, four numbers long"),
    ("docs/runbooks/voice.md", "address", r"^11\.0\.2\.54$", "a cuFFT version"),
    ("deploy/Dockerfile", "address", r"^1\.2\.1\.0$", "a brotlicffi version"),
    ("aura-core/aura/integrations/calendar/__init__.py", "address", r"^3\.8\.2\.2$", "a section of RFC 5545"),
)

_IPV4 = re.compile(r"(?<![\w.])(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})(?![\w]|\.\d)")
_VERSION_BEFORE = re.compile(r"(?:[A-Za-z]/|[vV@=~^-]|\bversion\s|\bVersion\s)$")
_URL = re.compile(r"\b(?:https?|wss?|ssh|git|mqtts?|rtsps?|redis|postgres(?:ql)?|ftp|s3)://([^\s/\"'<>)\]\\`,|?&=;#]+)([^\s\"'<>)\]\\`]*)")
_SCP = re.compile(r"\b[\w.-]+@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}):[\w./~-]+")
_EMAIL = re.compile(r"(?<![\w.+/-])([A-Za-z0-9][A-Za-z0-9._%+-]*)@((?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})(?![\w-])")
_PHONE_PLUS = re.compile(r"(?<![\w+])\+\d[\d ().-]{6,17}\d(?!\w)")
_PHONE_NANP = re.compile(r"(?<![\w.-])(?:\(\d{3}\) ?|\d{3}[-.])\d{3}[-.]\d{4}(?![\w.-])")
_MAC = re.compile(r"(?<![\w:-])([0-9A-Fa-f]{2})([:-])([0-9A-Fa-f]{2})(?:\2([0-9A-Fa-f]{2})){4}(?![\w]|[:-][0-9A-Fa-f])")
_HOME = re.compile(r"(?<![\w.-])(?:/home/|/Users/|[A-Za-z]:\\+Users\\+)([A-Za-z_][\w.-]*)")
_KEYS = tuple(re.compile(p) for p in (
    r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{24,}", r"\bghp_[A-Za-z0-9]{30,}", r"\bgithub_pat_[A-Za-z0-9_]{20,}",
    r"\bglpat-[A-Za-z0-9_-]{20,}", r"\bAKIA[0-9A-Z]{16}\b", r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
    r"\bAIza[0-9A-Za-z_-]{35}\b", r"\bhf_[A-Za-z0-9]{30,}", r"\btskey-[a-z]+-[A-Za-z0-9]{8,}",
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----", r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{6,}",
))
_PEM_BODY = re.compile(r"""(?:\\n|[\s"'+])*[A-Za-z0-9+/=]{40,}""")
_TAILNET = re.compile(r"(?<![\w.-])((?:[A-Za-z0-9-]+\.)+ts\.net)(?![\w-])")
_MADE_UP_TAILNET = re.compile(r"(?:tailnet|example|your-tailnet|tailnet-name|tail[0-9x]{0,6})\Z")
_FILE_TLDS = frozenset({"png", "jpg", "jpeg", "svg", "webp", "gif", "json", "js", "ts", "py", "md", "html", "css", "kt", "xml"})
_NUMBER = re.compile(r"(?<![\w.])-?\d{1,3}\.\d{3,}(?![\w.])")
_PLACEHOLDER = re.compile(r"[{}$<>%*\[\]]|\.\.\.")
_PATTERN_MACS = frozenset({
    "aabbccddeeff", "001122334455", "112233445566", "0123456789ab", "123456789abc", "ffffffffffff",
    "000000000000", "a1b2c3d4e5f6", "0a1b2c3d4e5f", "010203040506", "020000000001", "deadbeef0001",
})


_PATTERN_TAILS = frozenset({"000001", "000002", "000003", "112233", "aabbcc", "010203", "001122", "334455", "334456",
                            "334457", "334458", "334459", "aabbdd", "ddeeff", "123456", "abcdef", "000000", "ffffff"})


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str
    text: str

    def masked(self) -> str:
        return self.text[:2] + "*" * max(len(self.text) - 2, 3)

    def __repr__(self) -> str:
        # A failing assert prints its operands: a finding must not put the value it found
        # into a test log, which on a public pipeline is a second leak.
        return f"Finding({self.path}:{self.line} {self.kind} {self.masked()})"


@dataclass(frozen=True)
class LocalList:
    texts: tuple[str, ...] = ()
    words: tuple[re.Pattern[str], ...] = ()
    coords: tuple[tuple[float, float, float], ...] = ()
    present: bool = False


def read_local(path: Path) -> LocalList:
    """This machine's own list. A missing file is an empty list with present=False, never an error."""
    if not path.is_file():
        return LocalList()
    texts, words, coords = [], [], []
    for raw in path.read_text(encoding="utf-8").splitlines():
        row = raw.strip()
        if not row or row.startswith("#") or " " not in row:
            continue
        kind, value = row.split(" ", 1)
        value = value.strip()
        if kind == "text" and len(value) >= 3:
            texts.append(value.lower())
        elif kind == "word" and len(value) >= 2:
            words.append(re.compile(rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])", re.IGNORECASE))
        elif kind == "name" and len(value) >= 2:
            words.append(re.compile(rf"(?<![A-Za-z0-9]){re.escape(value)}(?![A-Za-z0-9])"))
        elif kind == "coord":
            parts = value.split(",")
            if len(parts) == 3:
                coords.append((float(parts[0]), float(parts[1]), float(parts[2])))
    return LocalList(tuple(texts), tuple(words), tuple(coords), True)


def _public_host(host: str, public: frozenset[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in public)


def host_is_fine(host: str, public: frozenset[str] = PUBLIC_HOSTS) -> bool:
    """Made up, a placeholder, a bare service name, an address (the address class judges it) or public."""
    host = host.rsplit("@", 1)[-1].lower().rstrip(".")
    if host.startswith("["):
        return True
    host = host.split(":", 1)[0]
    if not host or _PLACEHOLDER.search(host) or "." not in host:
        return True
    if host.rsplit(".", 1)[-1].isdigit():
        return True  # an address, or a malformed one a parser test feeds in: the address class judges it
    if host in MADE_UP_HOSTS or host.endswith(MADE_UP_HOST_SUFFIXES):
        return True
    if any(host.endswith("." + h) for h in MADE_UP_HOSTS):
        return True
    if host.endswith(".ts.net"):
        return tailnet_is_fine(host)
    if host.endswith(LAN_SUFFIXES):
        return host.rsplit(".", 1)[0].split(".")[-1] in MADE_UP_LAN_LABELS
    return _public_host(host, public)


def tailnet_is_fine(host: str) -> bool:
    """An overlay network's name is private; only a plainly made-up one (example, tail1234) may be written."""
    labels = host.lower().rstrip(".").split(".")
    return len(labels) < 3 or bool(_MADE_UP_TAILNET.match(labels[-3]))


def address_is_fine(text: str) -> bool:
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return True  # 999.1.1.1 is not an address
    return text in WELL_KNOWN or text in EDGES or any(ip in net for net in MADE_UP_NETS)


def phone_is_fine(text: str) -> bool:
    digits = re.sub(r"\D", "", text)
    if not 8 <= len(digits) <= 15:
        return True
    if len(set(digits)) <= 2 or digits in "01234567890123456789" or digits in "98765432109876543210":
        return True
    national = digits[-10:]
    if len(digits) in (10, 11) and "555" in (national[:3], national[3:6]):
        return True  # the fictional exchange, or the never-assigned area code
    if re.search(r"55501\d\d$", digits) or digits.startswith("1555"):
        return True  # 555-0100 to 555-0199, written short; or the never-assigned 555 area code
    return digits.startswith("447700900") or digits.startswith("07700900")  # Ofcom's drama range


def mac_is_fine(text: str) -> bool:
    flat = re.sub(r"[:-]", "", text).lower()
    if flat in _PATTERN_MACS or len(set(flat)) <= 2:
        return True
    if flat.startswith("00005e0053") or flat.startswith("deadbeef"):
        return True
    return flat[6:] in _PATTERN_TAILS  # a real vendor prefix with a patterned tail: a made-up device of that vendor


def _excepted(path: str, kind: str, text: str, exceptions: tuple[tuple[str, str, str, str], ...]) -> bool:
    return any(k == kind and fnmatch.fnmatch(path, glob) and re.search(pat, text)
               for glob, k, pat, _why in exceptions)


NO_LIST = LocalList()


def scan_text(path: str, body: str, local: LocalList = NO_LIST, *,
              public: frozenset[str] = PUBLIC_HOSTS,
              exceptions: tuple[tuple[str, str, str, str], ...] = EXCEPTIONS,
              owners: tuple[str, ...] = PROJECT_OWNERS, links: tuple[str, ...] = PROJECT_LINKS) -> list[Finding]:
    """Every finding in one file's text. Pure: no file is read, nothing is printed."""
    out: list[Finding] = []
    seen: set[tuple[int, str, str]] = set()

    def add(n: int, kind: str, text: str) -> None:
        if (n, kind, text) not in seen and not _excepted(path, kind, text, exceptions):
            seen.add((n, kind, text))  # one value on one line is one finding, whichever two rules saw it
            out.append(Finding(path, n, kind, text))

    lines = body.splitlines()
    for n, line in enumerate(lines, 1):
        if len(line) > 20000:
            continue  # a minified bundle or an embedded blob: shapes in it are noise
        for m in _IPV4.finditer(line):
            if not _VERSION_BEFORE.search(line[:m.start()]) and not address_is_fine(m.group(0)):
                add(n, "address", m.group(0))
        for m in _URL.finditer(line):
            host, rest = m.group(1), m.group(2)
            bare = host.rsplit("@", 1)[-1].split(":", 1)[0].lower()
            if not host_is_fine(host, public):
                add(n, "host", bare)
            where = (bare + rest).rstrip("/.,;:")
            owner = rest.strip("/").split("/", 1)[0].lower()
            under = (bare in FORGES and owner in owners) or any(bare == f"{o}.github.io" for o in owners)
            if under and not any(where == link or where.startswith(link + "/") or where.startswith(link + "#")
                                 or where.startswith(link + ".git") for link in links):
                add(n, "link", where)
        for m in _TAILNET.finditer(line):
            if not tailnet_is_fine(m.group(1)):
                add(n, "host", m.group(1).lower())
        for m in _SCP.finditer(line):
            if not host_is_fine(m.group(1), public):
                add(n, "host", m.group(1).lower())
        for m in _EMAIL.finditer(line):
            if m.group(1) == "git" or m.group(2).rsplit(".", 1)[-1].lower() in _FILE_TLDS:
                continue  # an scp-style address, or a file name like icon@2x.png
            if host_is_fine(m.group(2), frozenset({"users.noreply.github.com"})):
                continue
            who = m.group(1).lower()
            if who in MADE_UP_USERS or len(who) == 1 or who == "this":
                continue  # a role or a placeholder is nobody's mailbox; `this@Label` is Kotlin
            add(n, "email", m.group(0))
        for rx in (_PHONE_PLUS, _PHONE_NANP):
            for m in rx.finditer(line):
                if not phone_is_fine(m.group(0)):
                    add(n, "phone", m.group(0))
        for m in _MAC.finditer(line):
            if not mac_is_fine(m.group(0)):
                add(n, "mac", m.group(0))
        for m in _HOME.finditer(line):
            who = m.group(1).lower()
            if who not in MADE_UP_USERS and not _PLACEHOLDER.search(line[m.end():m.end() + 1] + who):
                add(n, "path", m.group(0))
        for rx in _KEYS:
            for m in rx.finditer(line):
                if m.group(0).startswith("-----BEGIN"):
                    rest = line[m.end():]
                    after = lines[n] if n < len(lines) and not rest.strip(" \"'+\\n,") else ""
                    if not (_PEM_BODY.match(rest) or _PEM_BODY.match(after)):
                        continue  # the header alone, in a note or an error message, is the name of a format
                add(n, "key", m.group(0))
        if local.texts or local.words:
            own = _own_links(links)
            rest_of_line = own.sub(" ", line) if own else line
            low = rest_of_line.lower()
            for t in local.texts:
                if t in low:
                    add(n, "local", t)
            for w in local.words:
                m = w.search(rest_of_line)
                if m:
                    add(n, "local", m.group(0))
    if local.coords:
        numbers = [[float(m.group(0)) for m in _NUMBER.finditer(line)] if len(line) <= 20000 else [] for line in lines]
        for n in range(len(lines)):
            window = [v for row in numbers[n:n + 3] for v in row]
            if not numbers[n] or len(window) < 2:
                continue
            for lat, lon, km in local.coords:
                dlat, dlon = km / 111.0, km / 70.0
                if any(abs(v - lat) <= dlat for v in numbers[n]) and any(abs(v - lon) <= dlon for v in window) \
                        or any(abs(v - lon) <= dlon for v in numbers[n]) and any(abs(v - lat) <= dlat for v in window):
                    add(n + 1, "local", "a coordinate near a listed place")
                    break
    return out


_NOT_SOURCE = frozenset({
    ".git", "node_modules", ".venv", "__pycache__", "build", "dist", "target", ".svelte-kit", ".gradle",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "graphify-out", "test-results", "playwright-report",
})


def tracked(repo: Path) -> list[str]:
    """The files git tracks. Where there is no git or no history (a job's exported copy), every file
    outside the folders a build writes: such a copy holds nothing but what was tracked."""
    try:
        done = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], capture_output=True, check=True)
        found = [p for p in done.stdout.decode("utf-8", "surrogateescape").split("\0") if p]
        if found:
            return found
    except (OSError, subprocess.CalledProcessError):
        pass
    out: list[str] = []
    for root, dirs, files in os.walk(repo):
        dirs[:] = sorted(d for d in dirs if d not in _NOT_SOURCE)
        out.extend(os.path.relpath(os.path.join(root, f), repo) for f in sorted(files))
    return out


def scan_repo(repo: Path, local: LocalList, paths: list[str] | None = None) -> list[Finding]:
    out: list[Finding] = []
    for rel in paths if paths is not None else tracked(repo):
        if rel == LOCAL_LIST:
            continue
        # A file's own name can be the private value (a region, a person).
        out.extend(f for f in scan_text(rel, rel, local) if f.kind == "local")
        full = repo / rel
        try:
            raw = full.read_bytes()
        except OSError:
            continue  # a deleted but still-indexed file, a dangling link
        if b"\0" in raw[:8192]:
            continue  # binary: not read (see the module docstring)
        out.extend(scan_text(rel, raw.decode("utf-8", "replace"), local))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--local", action="store_true", help="require this machine's list and use it")
    ap.add_argument("--show", action="store_true", help="print the matched text instead of its mask")
    ap.add_argument("--counts", action="store_true", help="print only the count per class")
    ap.add_argument("--repo", type=Path, default=REPO)
    args = ap.parse_args(argv)

    local = read_local(args.repo / LOCAL_LIST)
    if args.local and not local.present:
        print(f"private-values: --local needs {LOCAL_LIST} at the repository root (untracked); there is none")
        return 2
    findings = scan_repo(args.repo, local)
    by_kind: dict[str, int] = {}
    for f in findings:
        by_kind[f.kind] = by_kind.get(f.kind, 0) + 1
    if not args.counts:
        for f in findings:
            print(f"{f.path}:{f.line}: {f.kind}: {f.text if args.show else f.masked()}")
    said = ", ".join(f"{k} {v}" for k, v in sorted(by_kind.items())) or "none"
    lists = "shape classes and this machine's list" if local.present else "shape classes only (no local list here)"
    print(f"private-values: {len(findings)} finding(s) in tracked files [{said}]; {lists}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
