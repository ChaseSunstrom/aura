"""Self-update against the project's GitLab PyPI package registry.

The agent is a plain wheel published by CI to a GitLab project's PyPI registry
(``.gitlab/ci/desktop-dist.yml``). This module is the client half: it reads the
package's *simple* index anonymously, compares the newest published version with
the one installed, and — only when the operator asks — runs ``pip install
--upgrade`` in the current interpreter.

Two entry points, and the line between them is the whole safety story:

* ``jarvis-desktop --check-update`` and the once-a-day check at startup only
  *look* and *say*. They never install anything.
* ``jarvis-desktop --update`` is the only path that installs, and it is a
  person typing a command.

What this does NOT do: verify a signature on the wheel (GitLab's registry is
reached over TLS and the project is the operator's own; there is no separate
signing key), pin to a channel, or downgrade. It compares release numbers and
offers the highest one.

Everything that reaches the network takes an injectable ``opener`` so the tests
never open a socket, and the version comparison is a plain tuple compare so
``0.1.9`` sorts below ``0.1.123`` (a string compare would not — the property the
CI's ``0.1.<pipeline-iid>`` scheme depends on).
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .config import Config

_LOGGER = logging.getLogger(__name__)

#: The GitLab that publishes the wheel. Hard-coded rather than configured: this
#: is the update *origin*, not the jarvis-core server, and letting the config
#: (which the server can influence on some deployments) redirect where the
#: agent fetches its own next version from would be the wrong thing to make
#: settable. The repo names no house domain (T-0841): CI stamps its own server's URL into
#: the copy it builds (desktop:dist, tools/build-agent.sh), and a local build keeps the
#: placeholder, whose update check finds nothing.
GITLAB_BASE = "https://gitlab.home.example"
PROJECT_ID = 3
PACKAGE_NAME = "jarvis-desktop"

#: pip's ``--index-url`` root. pip appends ``/<name>/`` itself, so this is the
#: registry root and NOT the per-package page.
PYPI_INDEX_ROOT = f"{GITLAB_BASE}/api/v4/projects/{PROJECT_ID}/packages/pypi/simple"
#: The per-package simple page we read to list versions. This one DOES carry the
#: package name, because we fetch it directly rather than handing it to pip.
PACKAGE_INDEX_URL = f"{PYPI_INDEX_ROOT}/{PACKAGE_NAME}/"

#: Best-effort startup check runs at most this often. Remembered on disk so a
#: service that restarts every few minutes does not check every few minutes.
_CHECK_INTERVAL_S = 24 * 60 * 60
_STATE_FILE = "update-check.json"

#: Short on purpose: a check is best-effort, and a hung DNS lookup must not delay
#: startup or the CLI for longer than a person will wait.
_TIMEOUT_S = 8.0

#: A callable that fetches a URL's bytes. Injected in tests so nothing here ever
#: touches a socket.
Opener = Callable[[str, float], bytes]

#: A callable that runs the pip upgrade and returns its exit code. Injected in
#: tests so nothing here ever spawns pip.
PipRunner = Callable[[list[str]], int]


def installed_version() -> str:
    """The installed distribution's version, or the source-tree ``__version__``.

    ``importlib.metadata`` reads the *installed* metadata, which is what a real
    upgrade changes; running from a source checkout has no such metadata, so the
    module constant is the honest fallback there.
    """
    try:
        from importlib.metadata import version

        return version(PACKAGE_NAME)
    except Exception:  # noqa: BLE001 - PackageNotFoundError and, defensively, anything else
        from . import __version__

        return __version__


def parse_version(text: str) -> tuple[int, ...]:
    """A dotted-numeric version as a tuple of ints, for ordering.

    Only the numeric release segment is kept: a leading ``v`` is dropped and
    anything from the first non ``[0-9.]`` character on (a ``+local`` tag, an
    ``rc1`` suffix) is ignored. That is enough for CI's ``0.1.<iid>`` scheme and
    for the repo's plain ``0.1.0``; it deliberately does NOT implement full PEP
    440 ordering of pre-releases, which this project never publishes.
    """
    cleaned = (text or "").strip().lstrip("vV")
    release = re.split(r"[^0-9.]", cleaned, maxsplit=1)[0]
    parts = [int(chunk) for chunk in release.split(".") if chunk.isdigit()]
    return tuple(parts) if parts else (0,)


def is_newer(candidate: str, current: str) -> bool:
    """True when ``candidate`` is a strictly higher version than ``current``.

    Tuples are zero-padded to equal length first, so ``0.1`` and ``0.1.0`` compare
    equal and ``0.1.123`` beats ``0.1.9`` — the ordering a string compare gets
    wrong and the reason this is not ``a > b`` on the raw strings.
    """
    a = parse_version(candidate)
    b = parse_version(current)
    width = max(len(a), len(b))
    a += (0,) * (width - len(a))
    b += (0,) * (width - len(b))
    return a > b


#: Matches a jarvis-desktop wheel or sdist filename and captures its version.
#: The version is the segment after the name and a dash, ending at the next dash
#: (wheel: ``-py3-none-any.whl``) or the archive suffix (sdist: ``.tar.gz``).
_DIST_RE = re.compile(
    r"jarvis[-_]desktop-(?P<ver>\d[^-\s/\"'#<>]*?)(?:-[^\s/\"'#<>]*)?\.(?:whl|tar\.gz)",
    re.IGNORECASE,
)


def parse_simple_index(html: str) -> list[str]:
    """Every version named by a distribution file in a PEP 503 simple page.

    Reads filenames rather than trusting any ordering the page happens to have;
    the index lists both wheels and sdists and often each filename twice (href
    and anchor text), so the result is de-duplicated.
    """
    return sorted({m.group("ver") for m in _DIST_RE.finditer(html or "")}, key=parse_version)


def _default_opener(url: str, timeout: float) -> bytes:
    # A named User-Agent so a request that does show up in the registry's logs
    # says what made it, rather than a bare urllib default.
    req = urllib.request.Request(
        url, headers={"User-Agent": f"{PACKAGE_NAME}/{installed_version()}", "Accept": "text/html"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - https to a fixed host
        return resp.read()


def fetch_versions(
    index_url: str = PACKAGE_INDEX_URL,
    *,
    timeout: float = _TIMEOUT_S,
    opener: Opener | None = None,
) -> list[str]:
    """Versions published for the package, oldest first. ``[]`` on any failure.

    Never raises: a self-update check must not be able to bring down the agent
    it runs inside, so a DNS failure, a 404 or malformed HTML all come back as
    "nothing known", and the caller treats that as "no update".
    """
    get = opener or _default_opener
    try:
        raw = get(index_url, timeout)
    except Exception as exc:  # noqa: BLE001 - URLError, timeout, TLS, all mean "no answer"
        _LOGGER.debug("update check could not read %s: %s", index_url, exc)
        return []
    text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    return parse_simple_index(text)


def latest_available(
    *,
    index_url: str = PACKAGE_INDEX_URL,
    timeout: float = _TIMEOUT_S,
    opener: Opener | None = None,
) -> str | None:
    """The highest published version, or ``None`` when the index is unreachable."""
    versions = fetch_versions(index_url, timeout=timeout, opener=opener)
    if not versions:
        return None
    return max(versions, key=parse_version)


def available_update(
    current: str | None = None,
    *,
    index_url: str = PACKAGE_INDEX_URL,
    timeout: float = _TIMEOUT_S,
    opener: Opener | None = None,
) -> str | None:
    """The newest version *strictly newer* than ``current``, else ``None``."""
    running = current or installed_version()
    latest = latest_available(index_url=index_url, timeout=timeout, opener=opener)
    if latest is None:
        return None
    return latest if is_newer(latest, running) else None


def _default_pip_runner(command: list[str]) -> int:
    # The current interpreter's pip, so the upgrade lands in the same
    # environment the agent runs from rather than a system site-packages it may
    # not even have write access to.
    return subprocess.call(command)


def run_update(
    *,
    index_root: str = PYPI_INDEX_ROOT,
    runner: PipRunner | None = None,
) -> int:
    """Install the newest published wheel with pip. Returns pip's exit code.

    ``--index-url`` is the registry ROOT (pip appends the package name); this is
    why :data:`PYPI_INDEX_ROOT` and :data:`PACKAGE_INDEX_URL` are two different
    constants and mixing them up would make pip look under ``.../jarvis-desktop/
    jarvis-desktop/``.
    """
    run = runner or _default_pip_runner
    command = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--index-url",
        index_root,
        PACKAGE_NAME,
    ]
    _LOGGER.info("running: %s", " ".join(command))
    return run(command)


# --- the once-a-day startup check ------------------------------------------


def _state_path(config: "Config") -> Path:
    return Path(config.state_dir) / _STATE_FILE


def _read_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(path: Path, state: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError as exc:
        # A check that cannot remember when it last ran will just run next time;
        # that is a nuisance, not a reason to fail startup.
        _LOGGER.debug("could not write %s: %s", path, exc)


def bundled() -> bool:
    """True inside the desktop app, where this agent is a PyInstaller binary (ADR-0051).

    pip cannot update that binary, and the app replaces it with itself, so every
    self-update path here stands down: the daily notice says nothing and
    ``--update`` / ``--check-update`` refuse and say why.
    """
    return bool(getattr(sys, "frozen", False))


def maybe_notify_update(
    config: "Config",
    current: str | None = None,
    *,
    now: float | None = None,
    opener: Opener | None = None,
    index_url: str = PACKAGE_INDEX_URL,
) -> str | None:
    """At most once per day, check for a newer version and log if there is one.

    Returns the newer version string when one is available (and the check
    actually ran), else ``None``. Guarantees it does NOT provide: it never
    installs, never raises, and the once-a-day gate is remembered in the state
    directory — delete that file or set ``auto_update_check: false`` to change
    the behaviour. A restart within the window is a no-op that reads one small
    file and returns.
    """
    if bundled():
        return None
    try:
        running = current or installed_version()
        clock = time.time() if now is None else now
        path = _state_path(config)
        state = _read_state(path)
        last = state.get("last_check")
        if isinstance(last, (int, float)) and clock - last < _CHECK_INTERVAL_S:
            return None

        newer = available_update(
            running, index_url=index_url, timeout=_TIMEOUT_S, opener=opener
        )
        state["last_check"] = clock
        if newer:
            state["latest_seen"] = newer
        _write_state(path, state)

        if newer:
            _LOGGER.warning(
                "update available: jarvis-desktop %s (you have %s) - run "
                "`jarvis-desktop --update` to install it",
                newer,
                running,
            )
        return newer
    except Exception as exc:  # noqa: BLE001 - a background check must never crash the agent
        _LOGGER.debug("update check failed: %s", exc)
        return None


# --- CLI entry points -------------------------------------------------------


def cli_check_update(
    current: str | None = None,
    *,
    index_url: str = PACKAGE_INDEX_URL,
    opener: Opener | None = None,
    out: Callable[[str], None] = print,
) -> int:
    """``jarvis-desktop --check-update``: report, never install.

    Exit codes: ``0`` up to date, ``0`` an update is available (it is not an
    error to have one), ``1`` the registry could not be reached — so a script
    can tell "nothing to do" from "I could not find out".
    """
    running = current or installed_version()
    latest = latest_available(index_url=index_url, opener=opener)
    if latest is None:
        out(f"could not reach the update registry at {index_url}")
        return 1
    if is_newer(latest, running):
        out(
            f"update available: jarvis-desktop {latest} (you have {running})\n"
            f"run `jarvis-desktop --update` to install it"
        )
        return 0
    out(f"jarvis-desktop {running} is the latest available")
    return 0


def cli_update(
    current: str | None = None,
    *,
    index_url: str = PACKAGE_INDEX_URL,
    index_root: str = PYPI_INDEX_ROOT,
    opener: Opener | None = None,
    runner: PipRunner | None = None,
    out: Callable[[str], None] = print,
) -> int:
    """``jarvis-desktop --update``: install the newest published version.

    Checks first and does nothing when already current, so re-running it is
    cheap and safe. Returns pip's exit code when it does install, ``0`` when
    there was nothing to do, ``1`` when the registry was unreachable.
    """
    running = current or installed_version()
    latest = latest_available(index_url=index_url, opener=opener)
    if latest is None:
        out(f"could not reach the update registry at {index_url}")
        return 1
    if not is_newer(latest, running):
        out(f"jarvis-desktop {running} is already the latest available")
        return 0
    out(f"updating jarvis-desktop {running} -> {latest} …")
    code = run_update(index_root=index_root, runner=runner)
    if code == 0:
        out(f"installed jarvis-desktop {latest}. Restart the agent to run it.")
    else:
        out(f"pip exited {code}; jarvis-desktop was not updated")
    return code
