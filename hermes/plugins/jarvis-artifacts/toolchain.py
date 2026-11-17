"""What the image is supposed to contain, and what it actually does.

The media brief's Part 5 opens with the failure:

    "The tools weren't there. It rediscovered and reinstalled a toolchain that
    should have been in the image. That work is also lost on the next deploy,
    since production is immutable -- and runtime installs punch through the
    egress allowlist, which is not something I want the agent doing casually."

So this module exists to make "is ffmpeg here?" a fact the agent is *given*
rather than something it probes for with `command -v` in a reasoning block. The
manifest below is the promise; `check()` is whether the image kept it.

The manifest is deliberately a list of what the brief asks for rather than a
list of what happens to be installed. A preflight check that only reports what
is present cannot tell you the image regressed.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class Tool:
    name: str
    #: What it is for, in the agent's terms -- this is what makes a gap report useful.
    purpose: str
    #: How to ask it its version. Kept short; anything that needs a pipe is a script.
    version_args: tuple[str, ...] = ("--version",)
    #: A missing optional tool is a smaller graph, not a broken one.
    required: bool = True


#: Everything the media brief implies, pinned by name. Versions are reported
#: rather than pinned here: the image manifest pins them, and duplicating a
#: version string in two places is how they drift.
MANIFEST: tuple[Tool, ...] = (
    Tool("ffmpeg", "transcode, extract frames, check a download is what was asked for"),
    Tool("ffprobe", "read a media file's real duration, codecs and streams"),
    Tool("yt-dlp", "download a video from a URL"),
    Tool("node", "run a JS tool without installing one"),
    Tool("python3", "run the scripts library"),
    Tool("magick", "resize, convert and compare images", version_args=("-version",)),
    Tool("pandoc", "convert documents between formats"),
    Tool("pdftoppm", "render a PDF page to an image", version_args=("-v",)),
    Tool("qpdf", "split, merge and repair PDFs"),
    Tool("sox", "resample and normalise audio for the voice pipeline"),
    Tool("libreoffice", "office formats, when pandoc cannot", required=False),
)


@dataclass
class Found:
    name: str
    present: bool
    version: str
    purpose: str
    required: bool


def _version(tool: Tool) -> str:
    try:
        out = subprocess.run(
            [tool.name, *tool.version_args],
            capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    first = (out.stdout or out.stderr or "").strip().splitlines()
    return first[0][:120] if first else ""


def check(manifest: tuple[Tool, ...] = MANIFEST) -> list[Found]:
    """Every tool in the manifest, present or not, with its version if it is."""
    out: list[Found] = []
    for tool in manifest:
        present = shutil.which(tool.name) is not None
        out.append(
            Found(
                name=tool.name,
                present=present,
                version=_version(tool) if present else "",
                purpose=tool.purpose,
                required=tool.required,
            )
        )
    return out


def missing(found: list[Found]) -> list[Found]:
    """The required ones that are not there — what a capability gap is filed about."""
    return [f for f in found if f.required and not f.present]


def gap_report(found: list[Found]) -> str:
    """What to say when something is missing.

    Names the tool, what it was for, and the one action that is allowed: a
    capability gap and a merge request that adds it to the image. Not a pip
    install -- production is immutable, so the install is lost on the next deploy,
    and reaching out to fetch it punches through the egress allowlist.
    """
    gaps = missing(found)
    if not gaps:
        return ""
    lines = [f"{len(gaps)} tool(s) the image should carry are missing:"]
    lines += [f"  {f.name} — {f.purpose}" for f in gaps]
    lines.append(
        "File a capability gap and open the merge request that adds them to the image. "
        "Do not install them at runtime: production is immutable, so the install is lost on "
        "the next deploy, and fetching them reaches past the egress allowlist."
    )
    return "\n".join(lines)
