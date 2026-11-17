"""convert-document.sh, with pandoc, pdftotext, qpdf and libreoffice stubbed on PATH.

The stubs record what they were asked and write the file the script expects, so the
routing table in the header (which engine for which pair) and every exit code are
driven on a host with none of the tools; the one test that needs the real pandoc skips
with the reason when it is absent. Not covered: whether pandoc's reader for a given
extension exists (its exit 21 is the script's exit 4 either way).
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "convert-document.sh"


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


#: libreoffice is not in the default stub set: the image ships without it, and the tests
#: that need it add it by hand.
LIBREOFFICE = '''
echo "$@" >> "${STUB_LOG:-/dev/null}"
fmt=""; outdir=""; src=""
while [ $# -gt 0 ]; do case "$1" in --convert-to) fmt="$2"; shift 2;; --outdir) outdir="$2"; shift 2;; --headless) shift;; *) src="$1"; shift;; esac; done
[ -n "${STUB_LO_NOTHING:-}" ] || printf 'office' > "$outdir/$(basename "${src%.*}").$fmt"
'''


@pytest.fixture()
def tools(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _stub(bin_dir, "pandoc", '''
echo "$@" >> "${STUB_LOG:-/dev/null}"
out=""; while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift 2;; *) shift;; esac; done
[ -z "${STUB_FAIL:-}" ] || { printf 'partial' > "$out"; exit 1; }
if [ -n "${STUB_EMPTY:-}" ]; then : > "$out"; else printf 'converted' > "$out"; fi
''')
    _stub(bin_dir, "pdftotext", '''
echo "$@" >> "${STUB_LOG:-/dev/null}"
if [ -n "${STUB_NO_TEXT:-}" ]; then : > "$2"; else printf 'Page one.\\n\\fPage two.\\n' > "$2"; fi
''')
    _stub(bin_dir, "qpdf", '''
echo "$@" >> "${STUB_LOG:-/dev/null}"
printf '%%PDF' > "${@: -1}"
''')
    return bin_dir


@pytest.fixture()
def work(tmp_path: Path) -> Path:
    root = tmp_path / "work"
    root.mkdir()
    for name in ("in.md", "in.pdf", "in.pptx"):
        (root / name).write_bytes(b"in")
    return root


def run(tools: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    full = {**os.environ, "PATH": f"{tools}:{os.environ['PATH']}", **(env or {})}
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=full, timeout=30)


def roots(work: Path, log: Path | None = None, **more: str) -> dict[str, str]:
    return {"AURA_DATA_ROOTS": str(work), **({"STUB_LOG": str(log)} if log else {}), **more}


def convert(tools, work, src, dst, *extra, **env):
    log = work / "stub.log"
    r = run(tools, "--in", str(work / src), "--out", str(work / dst), *extra, env=roots(work, log, **env))
    return r, (log.read_text() if log.exists() else "")


def test_pandoc_is_the_ordinary_route_and_the_output_is_one_json_line(tools, work):
    r, log = convert(tools, work, "in.md", "out/in.html", "--to", "html5")
    assert r.returncode == 0, r.stderr
    lines = r.stdout.strip().splitlines()
    assert len(lines) == 1, "stdout is one JSON line and nothing else"
    body = json.loads(lines[0])
    assert body["ok"] is True and body["engine"] == "pandoc" and body["bytes"] > 0
    assert body["path"] == str(work / "out" / "in.html")
    assert log.split() == ["--standalone", "-t", "html5", "-o", str(work / "out" / "in.html"), str(work / "in.md")]


def test_a_pdf_to_text_is_pdftotext_alone(tools, work):
    r, log = convert(tools, work, "in.pdf", "in.txt")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["engine"] == "pdftotext"
    assert "--standalone" not in log, "pandoc has no PDF reader and was not asked"
    assert (work / "in.txt").read_text().startswith("Page one.")


def test_a_pdf_to_markdown_selects_pages_then_extracts_then_pandocs_as_commonmark(tools, work):
    r, log = convert(tools, work, "in.pdf", "text.md", "--pages", "1-3")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["engine"] == "pandoc"
    calls = log.splitlines()
    assert calls[0].startswith(f"--empty --pages {work / 'in.pdf'} 1-3 --"), "qpdf first"
    assert "pages.pdf" in calls[1], "pdftotext reads the selected pages, not the whole file"
    assert "-f commonmark" in calls[2] and calls[2].endswith("text.txt")


def test_a_pdf_to_pdf_is_qpdf(tools, work):
    r, log = convert(tools, work, "in.pdf", "part.pdf", "--pages", "2")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["engine"] == "qpdf"
    assert log.split()[-1] == str(work / "part.pdf")


def test_a_scanned_pdf_is_exit_5_not_an_empty_document(tools, work):
    r, _ = convert(tools, work, "in.pdf", "in.txt", STUB_NO_TEXT="1")
    assert r.returncode == 5 and "no extractable text" in r.stderr
    assert not (work / "in.txt").exists()


@pytest.mark.skipif(shutil.which("libreoffice") is not None, reason="this host has libreoffice on PATH")
@pytest.mark.parametrize("src, dst, what", [("in.pptx", "in.html", "reading .pptx"), ("in.md", "in.pdf", "writing a PDF")])
def test_what_only_libreoffice_can_do_is_exit_2_and_says_it_is_optional(tools, work, src, dst, what):
    r = run(tools, "--in", str(work / src), "--out", str(work / dst),
            env={**roots(work), "PATH": f"{tools}:/usr/bin:/bin"})
    assert r.returncode == 2, r.stderr
    assert what in r.stderr and "optional" in r.stderr


def test_an_office_file_goes_through_libreoffice_when_it_is_there(tools, work):
    _stub(tools, "libreoffice", LIBREOFFICE)
    r, log = convert(tools, work, "in.pptx", "slides.html")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["engine"] == "libreoffice"
    assert "--headless --convert-to html --outdir" in log and log.rstrip().endswith("in.pptx")
    assert (work / "slides.html").read_text() == "office"


def test_a_pdf_out_is_pandoc_to_docx_then_libreoffice(tools, work):
    _stub(tools, "libreoffice", LIBREOFFICE)
    r, log = convert(tools, work, "in.md", "note.pdf")
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["engine"] == "libreoffice"
    calls = log.splitlines()
    assert "-t docx" in calls[0] and calls[0].endswith("in.md")
    assert "--convert-to pdf" in calls[1] and calls[1].endswith("document.docx")


def test_libreoffice_writing_nothing_is_exit_4(tools, work):
    _stub(tools, "libreoffice", LIBREOFFICE)
    r, _ = convert(tools, work, "in.pptx", "slides.md", STUB_LO_NOTHING="1")
    assert r.returncode == 4 and "wrote no .md" in r.stderr


@pytest.mark.parametrize("args, why", [
    ((), "required"),
    (("--nonsense",), "unknown argument"),
    (("--in", "in.md", "--out", "out"), "needs an extension"),
    (("--in", "in.md", "--out", "out.html", "--pages", "1"), "not one"),
    (("--in", "in.md", "--out", "in.md"), "same file"),
])
def test_usage_is_exit_1(tools, work, args, why):
    r = run(tools, *[str(work / a) if a in ("in.md", "out.html", "out") else a for a in args], env=roots(work))
    assert r.returncode == 1, r.stderr
    assert why in r.stderr


def test_a_missing_tool_is_exit_2_and_says_not_to_install_it(tools, work):
    (tools / "pandoc").unlink()
    r = run(tools, "--in", str(work / "in.md"), "--out", str(work / "out.html"),
            env={**roots(work), "PATH": f"{tools}:/usr/bin:/bin"})
    assert r.returncode == 2 and "do not install it at runtime" in r.stderr


def test_a_path_outside_the_shared_dirs_is_exit_3(tools, work, tmp_path):
    r = run(tools, "--in", str(work / "in.md"), "--out", str(work / "out.html"), env={"AURA_DATA_ROOTS": ""})
    assert r.returncode == 3 and "/opt/data/work:/opt/data/files" in r.stderr, r.stderr
    r = run(tools, "--in", str(work / "in.md"), "--out", str(tmp_path / "out.html"), env=roots(work))
    assert r.returncode == 3 and "--out is outside" in r.stderr
    outside = tmp_path / "secret.md"
    outside.write_bytes(b"x")
    (work / "link.md").symlink_to(outside)
    r = run(tools, "--in", str(work / "link.md"), "--out", str(work / "out.html"), env=roots(work))
    assert r.returncode == 3 and "--in is outside" in r.stderr
    r = run(tools, "--in", str(work / "missing.md"), "--out", str(work / "out.html"), env=roots(work))
    assert r.returncode == 3 and "does not exist" in r.stderr


def test_a_pandoc_failure_is_exit_4_and_leaves_no_file(tools, work):
    r, _ = convert(tools, work, "in.md", "out.html", STUB_FAIL="1")
    assert r.returncode == 4 and "pandoc could not convert" in r.stderr
    assert not (work / "out.html").exists()


def test_an_empty_output_is_exit_5(tools, work):
    r, _ = convert(tools, work, "in.md", "out.html", STUB_EMPTY="1")
    assert r.returncode == 5 and "empty file" in r.stderr
    assert not (work / "out.html").exists()


@pytest.mark.skipif(shutil.which("pandoc") is None, reason="pandoc is not on this host; the image has it (deploy/Dockerfile)")
def test_the_real_pandoc_turns_markdown_into_html(work):
    (work / "note.md").write_text("# Title\n\nA paragraph.\n")
    r = subprocess.run(["bash", str(SCRIPT), "--in", str(work / "note.md"), "--out", str(work / "note.html")],
                       capture_output=True, text=True, env={**os.environ, "AURA_DATA_ROOTS": str(work)}, timeout=60)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["engine"] == "pandoc"
    assert "<h1" in (work / "note.html").read_text()


def test_the_image_ships_it_on_path_and_the_skill_calls_it_by_name():
    root = SCRIPT.parents[2]
    dockerfile = (root / "deploy" / "Dockerfile").read_text()
    assert "COPY scripts/document /opt/jarvis/bin/document" in dockerfile
    assert "/opt/jarvis/bin/*/*.sh" in dockerfile
    assert "!scripts/document/" in (root / ".dockerignore").read_text()
    skill = (root / "hermes" / "skills" / "document-convert" / "SKILL.md").read_text()
    assert "convert-document.sh --in" in skill and "scripts/document/convert-document.sh" not in skill
