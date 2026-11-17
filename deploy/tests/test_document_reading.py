"""The image can read a PDF, because the skill that reads them is not self-sufficient.

Hermes bundles ``skills/productivity/pdf`` — create, read, merge, fill, OCR — and every one
of its read paths imports a wheel the base image does not install. In the deployed container
the whole skill answered exactly one sentence::

    $ docker exec jarvis /opt/hermes/.venv/bin/python \\
        /opt/hermes/skills/productivity/pdf/scripts/pdf_read.py --text /tmp/table.pdf
    Missing dependency: install with 'python3 -m pip install pdfplumber'

That message is advice the agent cannot take. ``/opt/hermes`` is owned by root and the
container runs as ``hermes``, so the venv it is told to install into is the one directory it
may not write, and runtime installs are sealed for the same reason. Asked
for a revenue figure out of a PDF, JARVIS fell back to ``terminal``, checksummed the bytes
and answered about the checksum (bench ``pdf-read-004``).

So the wheels belong in the image, and this pins them there. The static test is the one that
runs everywhere: it fails the moment somebody drops the install line, which is the drift that
would put the skill back to answering about checksums. The live test is the real proof and is
opt-in, because it needs a built image and a Docker daemon.

The ``docx`` and ``xlsx`` skills were dead in exactly the same way, so they are fixed here
too: "read a document" that means "read a PDF" is the kind of half-capability somebody finds
out about while holding a spreadsheet.

python-pptx joined them in T-0826 (the making-documents skill writes a deck, ~2 MB unpacked:
its other dependencies were already in the image).

Not covered: the rest of those skills. PDF creating, form-filling and OCR need reportlab,
pymupdf and marker, which are hundreds of megabytes for capabilities no task or runbook in
this repo asks for. A future task that needs them should add its own wheel with its own reason.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "deploy" / "Dockerfile"

#: The fixture ``bench/tasks/multimodal/pdf-read-004.yaml`` scores against: a quarterly sales
#: table whose East row reads ``East 150 3000``.
FIXTURE = ROOT / "bench" / "fixtures" / "documents" / "table.pdf"

#: Where the bundled skill's reader lives inside the image.
PDF_READ = "/opt/hermes/skills/productivity/pdf/scripts/pdf_read.py"


#: What the three bundled reading skills import, and the module each one is imported as.
WHEELS = {"pdfplumber": "pdfplumber", "pypdf": "pypdf", "python-docx": "docx",
          "openpyxl": "openpyxl", "python-pptx": "pptx"}


def test_the_image_installs_what_the_bundled_reading_skills_import() -> None:
    """Every wheel is installed into Hermes' own venv, and the layer proves it imports."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    for wheel, module in WHEELS.items():
        assert f'"{wheel}==' in text, f"{wheel} is imported by a skill this image ships"
        # An install line with no import check is how a broken wheel reaches production
        # green: uv resolves it, and nothing loads it until an agent does.
        assert module in text, f"the build must import {module}, not only install {wheel}"


def test_the_image_makes_documents_with_a_pinned_pptx_and_proves_the_import() -> None:
    """T-0826: the making-documents skill writes .docx, .xlsx and .pptx, so all three libraries
    are pinned in one install and one import proof. python-pptx was left out while nothing asked
    for a deck; without the pin the skill's third script dies on ``No module named 'pptx'``
    and ``/opt/hermes`` is not the agent's to install into."""
    lines = DOCKERFILE.read_text(encoding="utf-8").splitlines()
    install = next((i for i, line in enumerate(lines) if '"python-pptx==' in line), None)
    assert install is not None, "python-pptx must be pinned exactly in the Dockerfile"
    assert re.search(r'"python-pptx==\d+\.\d+\.\d+"', lines[install]), lines[install]
    # The RUN that installs it is the RUN that proves it: a wheel that resolves but cannot
    # import (a lxml ABI drift) must stop the build, not the first deck.
    run = lines[install]
    for j in range(install + 1, min(install + 4, len(lines))):
        run += "\n" + lines[j]
        if "print(" in lines[j]:
            break
    assert "import" in run and "pptx" in run.split("import", 1)[1], run
    assert '"python-docx==' in "\n".join(lines) and '"openpyxl==' in "\n".join(lines)


def test_the_dockerfile_no_longer_calls_pptx_unwanted() -> None:
    """The comment that said python-pptx was deliberately left out is false once it is in."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "marker-pdf and python-pptx" not in text


def test_the_image_installs_jq_for_the_activity_hook() -> None:
    """``hermes/agent-hooks/activity-log.sh`` pipes every tool call through jq with stderr
    sent to /dev/null, and the base image has no jq: ``logs/activity.jsonl`` stayed empty
    from 1 Sep to 18 Sep 2026 while the HUD's activity feed and the nightly self-review read
    it. Pinned the way the media tools are — in an apt line, with a version print so the
    build fails rather than the hook."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    apt_lines = [line for line in text.split("\n") if "imagemagick" in line]
    assert apt_lines and all(" jq" in line for line in apt_lines), apt_lines
    assert "jq --version" in text, "the build must prove jq runs, not only install it"
    hook = (ROOT / "hermes" / "agent-hooks" / "activity-log.sh").read_text(encoding="utf-8")
    assert "| jq " in hook, "the hook no longer uses jq; retire it from the image with this test"


def test_runtime_installs_are_sealed_once_and_never_reopened() -> None:
    """``ENV HERMES_DISABLE_LAZY_INSTALLS=0`` sat two hundred lines above the ``=1`` that
    actually won, so the Dockerfile read as if installs were open and only ``docker exec``
    could say what the container did. Exactly one setting, and it seals — the config-side
    switch that wins in every Hermes mode is pinned in test_config_upgrade.py."""
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert re.findall(r"HERMES_DISABLE_LAZY_INSTALLS=(\S+)", text) == ["1"]


def test_every_wheel_is_pinned_exactly() -> None:
    """``>=`` here would let a resolver change the extractor between two pipeline runs.

    The extract service's requirements file says why in full: a benchmark regression caused
    by a dependency moving under the image is one nobody thinks to look for.
    """
    for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
        for wheel in WHEELS:
            if f'"{wheel}' in line:
                assert f'"{wheel}==' in line, f"{wheel} must be pinned exactly, not {line.strip()}"


@pytest.mark.skipif(
    os.environ.get("AURA_LIVE_PDF_PROOF") != "1",
    reason="live proof: needs a built image and a Docker daemon; set AURA_LIVE_PDF_PROOF=1",
)
def test_the_built_image_reads_the_benchmark_pdf() -> None:
    """The whole capability, end to end, through the image's own skill script.

    Deliberately the bundled script rather than a hand-rolled ``pdfplumber.open`` — what the
    agent reaches for is the skill, so the skill is what has to work.
    """
    if not shutil.which("docker"):
        pytest.skip("docker is not on PATH")
    image = os.environ.get("AURA_IMAGE", "jarvis:dev")
    proc = subprocess.run(
        ["docker", "run", "--rm", "--network", "none", "--entrypoint",
         "/opt/hermes/.venv/bin/python", "-v", f"{FIXTURE}:/tmp/table.pdf:ro", image,
         PDF_READ, "--text", "/tmp/table.pdf"],
        capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"
    pages = json.loads(proc.stdout)["pages"]
    assert "East" in pages[0] and "3000" in pages[0], pages
