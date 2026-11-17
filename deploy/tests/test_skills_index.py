"""The words a person would say must survive the skills-index cut.

Hermes puts every skill's description into the system prompt, cut at
``SKILL_PROMPT_DESC_LIMIT`` (60) as ``desc[:57] + "..."``
(``hermes-agent/agent/skill_utils.py``, ``extract_skill_description``). The model
picks a skill from that line and only then calls ``skill_view`` for the body, so a
routing word after character 57 does not exist for it. The 2026-09-18 benchmark
lost calendar, mail, Jellyfin, contacts, files, scaffold and memory tasks this way:
the descriptions were honest and the words were all past the cut.

Not covered: whether the body says the right thing once opened (one exception: no
body may send the model to ``curl`` in the terminal, below), and whether the
prompt index really is built this way on the deployed image (that is Hermes's
code, read by hand; the constant below mirrors it).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "hermes" / "skills"
#: ``SKILL_PROMPT_DESC_LIMIT - 3``: the characters the model sees before ``...``.
VISIBLE = 57

#: skill → the words a person says (any one of them, case-insensitive) that must sit
#: inside the visible prefix. A new skill needs a row here; that is deliberate.
ROUTING_WORDS = {
    "audio-process": ["normalise", "resample", "audio file"],
    "build-capability": ["scaffold", "capability"],
    "capability-gap": ["cannot do"],
    "car-places": ["nearest", "navigate"],
    "changing-settings": ["setting"],
    "delegating-work": ["subagents", "delegate_task"],
    "desktop-control": ["desktop", "click"],
    "document-convert": ["convert a document", "docx", "epub"],
    "drive-routine": ["start driving"],
    "file-delivery": ["saving a file"],
    "generating-images": ["image", "picture", "draw"],
    "headless-browser": ["chromium"],
    "hermes-plugin-authoring": ["internals reference"],
    "homelab-runbook": ["calendar", "mail", "jellyfin", "contacts", "files"],
    "homelab-telemetry": ["telemetry"],
    "home-presence": ["who's home", "network"],
    "catch-me-up": ["catch me up", "what did i miss"],
    "what-did-you-do": ["what did you do"],
    "cooking-walkthrough": ["cook", "recipe", "walk me through"],
    "finding-things": ["where did i put", "find"],
    "texting-people": ["what did", "texts"],
    "travel-plans": ["trip", "flight"],
    "life-admin": ["what's due", "renewals", "warranty"],
    "drive-fun": ["radio show", "trivia", "hype me up"],
    "first-run": ["first-run setup", "setup"],
    "hud-layout": ["hud"],
    "image-edit": ["resize", "crop", "strip"],
    "image-inspection": ["describe an image"],
    "aura-cron-jobs": ["remind", "every morning"],
    "aura-voice": ["voice"],
    "making-documents": ["word document", "spreadsheet", "slide deck"],
    "media-convert": ["convert a video", "trim", "extract"],
    "media-fetch": ["video"],
    "model-routing": ["switch model", "load the 35b"],
    "n8n-automation": ["n8n"],
    "news-and-reading": ["news", "feeds", "for later"],
    "phone-music": ["play music", "song"],
    "phone-actions": ["text", "alarm"],
    "planning-a-request": ["plan", "todo", "goal"],
    "reading-documents": ["pdf"],
    "refactor": ["refactor"],
    "remembering-things": ["remember", "what do you remember", "from now on", "notes"],
    "routines-people-places": ["every time", "girlfriend", "when I get"],
    "sky-over-the-house": ["iss", "moon"],
    "web-search-fallback": ["web_search"],
}


def _description(skill_md: Path) -> str:
    text = skill_md.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{skill_md}: no front-matter"
    front = yaml.safe_load(text.split("\n---", 1)[0][4:])
    # Hermes strips surrounding quotes after YAML has already removed them; same net effect.
    return str(front.get("description", "")).strip().strip("'\"")


def test_every_skill_has_a_routing_row() -> None:
    on_disk = {p.parent.name for p in SKILLS.glob("*/SKILL.md")}
    assert on_disk == set(ROUTING_WORDS), (
        f"add a row for {sorted(on_disk - set(ROUTING_WORDS))}, "
        f"drop {sorted(set(ROUTING_WORDS) - on_disk)}"
    )


@pytest.mark.parametrize("skill", sorted(ROUTING_WORDS))
def test_routing_words_survive_the_prompt_cut(skill: str) -> None:
    desc = _description(SKILLS / skill / "SKILL.md")
    visible = desc[:VISIBLE].lower()
    assert any(w in visible for w in ROUTING_WORDS[skill]), (
        f"{skill}: none of {ROUTING_WORDS[skill]} in the first {VISIBLE} chars: {visible!r}"
    )


def test_the_first_run_skill_has_a_section_for_each_walkthrough_step() -> None:
    """The console sends ``First-run setup, step "<id>"``; a step with no section of that
    name leaves the model with nothing to follow. The list is the shared table both sides read.
    Not covered: whether a section's advice is good."""
    steps = json.loads((ROOT / "tests" / "contracts" / "first-run-steps.json").read_text(encoding="utf-8"))["steps"]
    text = (SKILLS / "first-run" / "SKILL.md").read_text(encoding="utf-8")
    assert re.findall(r"^## (\S+)$", text, re.M) == steps


#: A shell invocation of a network fetcher: at a line's start (a fenced block or a bullet),
#: after a prompt, ``$(``, a pipe, ``;``, ``&&`` or an opening inline code span, with any
#: ``VAR=value`` prefix. Prose that only names the command (```curl``` with no arguments)
#: does not match, so a pitfall may still say what not to type.
CURL_COMMAND = re.compile(r"(?:^\s*(?:[-$#]\s+)?|[`$(|;&]\s*)(?:[A-Z_]+=\S+\s+)*(?:curl|wget)\s")
#: A line that tells the model NOT to (the homelab-runbook's "no `curl`", a pitfall naming
#: the failure) is allowed to spell the command out.
SAYS_NOT_TO = re.compile(r"\b(?:no|not|never|cannot|don't|do not)\b", re.I)


def test_no_skill_tells_the_model_to_curl_in_the_terminal() -> None:
    """Since ADR-0045 the shell has no network: a ``curl`` step costs the whole task budget.

    The 2026-09-18 benchmark lost browser-download-003 and vision-count-002 to skills that
    said "curl the URL". Not covered: a skill's ``references/`` files, and a fetch spelled
    some other way (``python3 -c 'urllib…'``).
    """
    offenders = [
        f"{path.relative_to(ROOT)}:{n}: {line.strip()}"
        for path in sorted(SKILLS.glob("*/SKILL.md"))
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if CURL_COMMAND.search(line) and not SAYS_NOT_TO.search(line)
    ]
    assert not offenders, "the shell has no network; use the typed tools:\n" + "\n".join(offenders)


def test_drafts_to_a_person_are_written_from_the_owners_own_messages() -> None:
    """T-0828: the texting skill says to read his own sent messages to that person first.

    Does not cover whether a model follows it; that is the live persona eval's job.
    """
    text = (SKILLS / "texting-people" / "SKILL.md").read_text(encoding="utf-8")
    section = text.split("## Sounding like him", 1)[1].split("\n## ", 1)[0]
    for needed in ("box: sent", "texts_read", "mail_folders", "mail_search", "mail_read", "length", "greeting",
                   "never quote", "exact words"):
        assert needed in section, f"the section no longer says {needed!r}"


MAKING_DOCUMENTS = SKILLS / "making-documents" / "SKILL.md"
#: format → (the mime artifact_publish is told, the file the skill's example writes)
OFFICE = {
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "update.docx"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "sales.xlsx"),
    "pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", "trip.pptx"),
}


def test_making_documents_names_the_libraries_the_publish_step_and_each_mime() -> None:
    """T-0826: the skill is how the model learns the three libraries are there and that the
    file leaves the container through artifact_publish. Does not run anything: the examples
    are run by the test below, which needs the libraries on this machine."""
    text = MAKING_DOCUMENTS.read_text(encoding="utf-8")
    for needed in ("python-docx", "openpyxl", "python-pptx", "artifact_publish", "/opt/hermes/.venv/bin/python"):
        assert needed in text, f"making-documents no longer says {needed!r}"
    for mime, _ in OFFICE.values():
        assert mime in text, f"making-documents lost the mime {mime}"


def test_making_documents_examples_run_and_the_files_reopen(tmp_path: Path) -> None:
    """Each fenced example is run as written (its output folder pointed at a temp dir) and the
    file it wrote is reopened with the same library, so a renamed API or a typo in the skill
    fails here and not in front of the owner. Skips where a library is not installed (CI's
    deploy job installs none; the image build proves the imports instead)."""
    import subprocess
    import sys

    pytest.importorskip("docx")
    pytest.importorskip("openpyxl")
    pytest.importorskip("pptx")
    blocks = dict(re.findall(r"```python (\w+)\n(.*?)```", MAKING_DOCUMENTS.read_text(encoding="utf-8"), re.S))
    assert set(blocks) == set(OFFICE), f"one example per format, got {sorted(blocks)}"
    for fmt, code in blocks.items():
        assert "/opt/data/work" in code
        script = tmp_path / f"make_{fmt}.py"
        script.write_text(code.replace("/opt/data/work", str(tmp_path)), encoding="utf-8")
        # -I: the temp dir holds the script, and an interpreter next to a planted module would run it.
        done = subprocess.run([sys.executable, "-I", str(script)], capture_output=True, text=True, timeout=60)
        assert done.returncode == 0, f"{fmt} example failed:\n{done.stderr}"
        assert (tmp_path / OFFICE[fmt][1]).stat().st_size > 0, fmt

    import docx
    import openpyxl
    import pptx

    assert "Quarterly update" in [p.text for p in docx.Document(str(tmp_path / "update.docx")).paragraphs]
    ws = openpyxl.load_workbook(tmp_path / "sales.xlsx")["Sales"]
    assert ws["B2"].value == 150 and ws["B4"].value == "=SUM(B2:B3)"
    deck = pptx.Presentation(str(tmp_path / "trip.pptx"))
    assert len(deck.slides) == 3 and deck.slides[1].shapes.title.text == "Plan"
