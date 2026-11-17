"""A red run of the clean-install proof leaves the committed proof as it was (T-1497).

``scripts/verify/install-clean.sh`` writes its record and its pictures to
``docs/verification/install`` by default, which is tracked: the claims register cites that
record. It used to clear that folder as it started and write the record there whatever the
outcome, so two red runs replaced the committed proof with a failed record and a ``failed.png``
in a tree whose claims still said the install was proven. Now everything is made in the run's
own folder and reaches the output folder only from a run that ends ``installed``.

The script needs Docker, an hour and 45 GB, so this reads its text: where each command that
names the output folder sits relative to the line that ends a failed run.

Not covered: that the copy itself works (the proof's own green run shows it), a run stopped by
a signal, and ``--page-only``, which makes no record.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify" / "install-clean.sh"


def _lines() -> list[str]:
    return SCRIPT.read_text(encoding="utf-8").splitlines()


def _commands_naming(variable: str) -> list[tuple[int, str]]:
    """Each line that uses ``$variable`` and is not a comment, numbered from 1."""
    use = re.compile(r"\$\{?" + variable + r"\b")
    return [(n, line) for n, line in enumerate(_lines(), 1)
            if use.search(line) and not line.lstrip().startswith("#")]


def _failed_exit() -> int:
    """The line where a run that did not end installed leaves the script."""
    lines = _lines()
    start = next(n for n, line in enumerate(lines, 1) if line.strip() == 'if [ "$result" != installed ]; then')
    end = next(n for n in range(start, len(lines) + 1) if lines[n - 1].strip() == "fi")
    assert any(lines[n - 1].strip() == "exit 1" for n in range(start, end)), "a failed run no longer exits there"
    return end


def test_the_script_still_parses():
    assert subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True).returncode == 0


def test_nothing_touches_the_output_folder_before_the_run_is_known_to_be_installed():
    failed_exit = _failed_exit()
    early = [(n, line.strip()) for n, line in _commands_naming("OUT")
             if n < failed_exit
             # Reading the option, the default, and the sentence a failed run prints about it.
             and not re.match(r'\s*(--out\)|\[ -n "\$OUT" \] \|\| OUT=|say ")', line)]
    assert not early, f"the output folder is written before the outcome is known: {early}"


def test_an_installed_run_replaces_the_earlier_record_and_copies_its_own():
    failed_exit = _failed_exit()
    late = [line.strip() for n, line in _commands_naming("OUT") if n > failed_exit]
    assert any(line.startswith("rm -f ") and "clean-container.log" in line and "failed.png" in line
               for line in late), late
    assert any(line.startswith("cp ") and "$RECORD" in line for line in late), late


def test_the_browser_and_the_record_write_into_the_runs_own_folder():
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'RECORD="$WORK/record"' in text
    assert 'SETUP_OUT="$RECORD"' in text and 'SETUP_OUT="$OUT"' not in text
    assert '} > "$RECORD/clean-container.log"' in text and '> "$OUT/clean-container.log"' not in text


def test_a_failed_run_says_where_its_record_is_and_that_the_folder_is_untouched():
    lines = _lines()
    failed_exit = _failed_exit()
    said = [line for line in lines[:failed_exit] if "RESULT: failed" in line and line.lstrip().startswith("say ")]
    assert len(said) == 1 and "$RECORD" in said[0] and "$OUT is as it was" in said[0], said
