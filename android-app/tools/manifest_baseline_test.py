#!/usr/bin/env python3
"""Every Android permission and exported component is one somebody reviewed (T-0574).

A permission is something the phone asks its owner for, and an exported component is a
door another app on the phone can open. Neither should arrive in a diff unnoticed, so the
set of them, read from every module's ``src/main/AndroidManifest.xml``, is pinned in
``manifest-baseline.json`` beside this file. An addition fails, and so does an entry the
manifests no longer have, so the baseline stays the exact list. After reviewing a change,
``--update`` rewrites the baseline; commit it with the change.

Does not read the merged manifest (a library's own manifest is read, but a permission a
dependency adds at merge time is not): that needs a gradle build, which this job has not.

Run:  python3 android-app/tools/manifest_baseline_test.py [--update]
"""
from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET  # the repo's own manifests, never fetched input
from pathlib import Path

ANDROID = Path(__file__).resolve().parents[1]
BASELINE = Path(__file__).with_name("manifest-baseline.json")
NS = "{http://schemas.android.com/apk/res/android}"
COMPONENTS = ("activity", "activity-alias", "service", "receiver", "provider")


def surface(manifest: str) -> dict[str, set[str]]:
    """The permissions and the exported components one manifest declares."""
    root = ET.fromstring(manifest)
    permissions = {e.get(NS + "name", "") for tag in ("uses-permission", "uses-permission-sdk-23")
                   for e in root.iter(tag)}
    exported = {f"{e.tag}:{e.get(NS + 'name', '')}" for tag in COMPONENTS for e in root.iter(tag)
                if e.get(NS + "exported") == "true"}
    return {"permissions": permissions - {""}, "exported": exported}


def current() -> dict[str, list[str]]:
    found: dict[str, set[str]] = {"permissions": set(), "exported": set()}
    for path in sorted(ANDROID.glob("**/src/main/AndroidManifest.xml")):
        if "build" in path.parts:
            continue
        for key, values in surface(path.read_text(encoding="utf-8")).items():
            found[key] |= values
    return {key: sorted(values) for key, values in found.items()}


def compare(now: dict[str, list[str]], pinned: dict[str, list[str]]) -> list[str]:
    problems = []
    for key in ("permissions", "exported"):
        added = sorted(set(now.get(key, [])) - set(pinned.get(key, [])))
        gone = sorted(set(pinned.get(key, [])) - set(now.get(key, [])))
        problems += [f"FAIL  {key}: {item} is not in manifest-baseline.json (review it, then --update)"
                     for item in added]
        problems += [f"FAIL  {key}: {item} is in manifest-baseline.json but no manifest has it (--update)"
                     for item in gone]
    return problems


def self_test() -> None:
    """The check catches what it exists for: an unreviewed permission and a new door."""
    base = ('<manifest xmlns:android="http://schemas.android.com/apk/res/android">'
            '<uses-permission android:name="android.permission.RECORD_AUDIO"/>'
            '<application><activity android:name=".Main" android:exported="true"/>'
            '<service android:name=".Quiet" android:exported="false"/></application></manifest>')
    pinned = {k: sorted(v) for k, v in surface(base).items()}
    assert pinned == {"permissions": ["android.permission.RECORD_AUDIO"], "exported": ["activity:.Main"]}
    grown = base.replace('<application>', '<uses-permission android:name="android.permission.READ_SMS"/>'
                                           '<application><receiver android:name=".Door" android:exported="true"/>')
    problems = compare({k: sorted(v) for k, v in surface(grown).items()}, pinned)
    assert any("READ_SMS" in p for p in problems) and any("receiver:.Door" in p for p in problems), problems
    assert compare(pinned, pinned) == []


def main() -> int:
    self_test()
    now = current()
    if "--update" in sys.argv:
        BASELINE.write_text(json.dumps(now, indent=2) + "\n", encoding="utf-8")
        print(f"manifest baseline written: {len(now['permissions'])} permissions, {len(now['exported'])} exported")
        return 0
    pinned = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}
    problems = compare(now, pinned)
    for line in problems:
        print(line)
    if not problems:
        print(f"manifest baseline: {len(now['permissions'])} permissions and {len(now['exported'])} exported "
              f"components, each reviewed")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
