"""T-1057: a failed apk:build keeps what names its failure. Pipeline 811 failed after 541 s with no
failed test in the pipeline's report and only the trace behind it: the junit report read the app's
unit tests alone, so a failing car-module test left nothing in the view, and the log had no marker
for the Gradle step that stopped. These pins read the yaml; the failing run itself stays unread.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ANDROID = "android-app"
JOB = yaml.safe_load((ROOT / ".gitlab" / "ci" / "android-apk.yml").read_text())["apk:build"]


def _car_dirs() -> dict[str, str]:
    """Gradle project name -> its directory under android-app/, as settings.gradle.kts maps it."""
    text = (ROOT / ANDROID / "settings.gradle.kts").read_text()
    return dict(re.findall(r'project\(":([\w-]+)"\)\.projectDir = file\("([^"]+)"\)', text))


def test_apk_build_keeps_the_test_reports_and_roborazzi_output_when_it_fails():
    art = JOB["artifacts"]
    assert art["when"] == "always", "a failed build must still hand its reports over"
    paths = set(art["paths"])
    junit = {art["reports"]["junit"]} if isinstance(art["reports"]["junit"], str) else set(art["reports"]["junit"])
    assert f"{ANDROID}/app/build/test-results/testDebugUnitTest/*.xml" in junit
    assert f"{ANDROID}/app/build/reports/tests/testDebugUnitTest" in paths
    assert f"{ANDROID}/app/build/outputs/roborazzi/" in paths, "the screen differences Roborazzi wrote"
    # The car modules' JVM tests (the `test` task writes build/test-results/test): the XML is the
    # only thing the pipeline view can read a failed car test from.
    tested = re.findall(r":(car-[\w-]+):test\b", "\n".join(JOB["script"]))
    assert tested, "the car tests are no longer in apk:build"
    dirs = _car_dirs()
    for name in tested:
        module = f"{ANDROID}/{dirs[name]}/build"
        assert f"{module}/test-results/test/*.xml" in junit, name
        assert f"{module}/test-results/test" in paths, name
        assert f"{module}/reports/tests/test" in paths, name


def test_each_gradle_step_is_announced_on_its_own_line_before_it_runs():
    """The log's last `step:` line names where a failed build stopped, for the failures before any
    test runs (assemble, toolchain) that leave no report. Every gradle line must have a `step:`
    echo above it with no other gradle line between them."""
    lines = [line.strip() for line in "\n".join(JOB["script"]).splitlines()]
    gradle = [i for i, line in enumerate(lines) if "./gradlew" in line]
    assert len(gradle) >= 4, gradle
    for i in gradle:
        echoes = [j for j in range(i) if lines[j].startswith('echo "step: ')]
        assert echoes, f"no step echo above: {lines[i]}"
        between = lines[echoes[-1] + 1:i]
        assert not any("./gradlew" in line for line in between), f"a gradle step ran unannounced: {lines[i]}"
