#!/usr/bin/env python3
"""Executable spec for the car link's place in the Jarvis app (ADR-0047).

## Why this exists

Headway's app code moved into ``ai.jarvis.app.car``. Four properties of that
move fail silently -- the APK builds, installs and runs, and the car simply
never connects, or the phone refuses the install with "App not installed":

1. **The service is declared with the types its code claims.** CarLinkService
   calls ``startForeground`` with a ladder of type sets (mediaProjection,
   connectedDevice, location). A type the manifest does not declare *throws*
   from ``startForeground`` (Headway PROGRESS, 2026-08-16), and the session
   never starts.
2. **The two device-global names are declared once.** The template-renderer
   permission and the ``androidx.car.app.connection`` authority can each have
   one owner per device (Headway ADR 0009).
3. **Nothing still points at Headway's app package.** A ``dev.headway.app``
   class name in the manifest or the code is a component Android cannot find;
   the ``dev.headway.<module>`` protocol packages are the vendored modules and
   are fine.
4. **Nothing privileged, and no test double in the release build.**
   ``INJECT_EVENTS`` and ``WRITE_SECURE_SETTINGS`` are the two permissions an
   unprivileged app must never ask for; ``FakeLibraryService`` (Headway B-016)
   lives in ``src/debug`` only.

Run:  python3 android-app/tools/car_manifest_test.py
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
MAIN = APP / "src" / "main"
#: Overridable so the check can be shown failing against an older manifest.
MANIFEST = Path(os.environ.get("CAR_MANIFEST", MAIN / "AndroidManifest.xml"))
KOTLIN = MAIN / "kotlin" / "ai" / "jarvis" / "app"
SERVICE = KOTLIN / "car" / "service" / "CarLinkService.kt"

#: The types CarLinkService's startForeground ladder can claim.
FOREGROUND_TYPES = {"mediaProjection", "connectedDevice", "location"}


def check_the_service_declares_its_types(manifest: str) -> list[str]:
    m = re.search(r'<service[^>]*android:name="\.car\.service\.CarLinkService"[^>]*>', manifest, re.S)
    if not m:
        return ["CarLinkService is not declared, so nothing can start the car session"]
    types = re.search(r'foregroundServiceType="([^"]+)"', m.group(0))
    declared = set(types.group(1).split("|")) if types else set()
    if declared != FOREGROUND_TYPES:
        return [f"CarLinkService declares {sorted(declared)}, its code claims {sorted(FOREGROUND_TYPES)}"]
    return []


def check_the_global_names_are_declared_once(manifest: str) -> list[str]:
    failures = []
    permission = re.findall(r'<permission\s[^>]*android:name="android\.car\.permission\.TEMPLATE_RENDERER"', manifest)
    if len(permission) != 1:
        failures.append(f"the template-renderer permission is defined {len(permission)} times, not once")
    uses = re.findall(r'<uses-permission\s[^>]*android:name="android\.car\.permission\.TEMPLATE_RENDERER"', manifest)
    if len(uses) != 1:
        failures.append(f"the template-renderer permission is requested {len(uses)} times, not once")
    authority = re.findall(r'android:authorities="androidx\.car\.app\.connection"', manifest)
    if len(authority) != 1:
        failures.append(f"the car-app connection authority is claimed {len(authority)} times, not once")
    return failures


def check_nothing_points_at_headways_app(manifest: str) -> list[str]:
    failures = []
    if "dev.headway.app" in manifest:
        failures.append("the manifest still names a dev.headway.app component")
    for path in sorted(MAIN.rglob("*")):
        # A class reference (`dev.headway.app.X`), not the package name as data:
        # RendererOwner names Headway's package to recognise it.
        if path.suffix in {".kt", ".xml"} and re.search(r"\bdev\.headway\.app\.\w", path.read_text(encoding="utf-8")):
            failures.append(f"{path.relative_to(APP)} still refers to dev.headway.app")
    # Every car component the manifest names has its class, wherever its file is.
    for name in re.findall(r'android:name="\.car\.([A-Za-z.]+)"', manifest):
        package, cls = name.rsplit(".", 1) if "." in name else ("", name)
        folder = KOTLIN / "car" / package.replace(".", "/")
        found = folder.is_dir() and any(
            re.search(rf"\bclass {cls}\b", f.read_text(encoding="utf-8")) for f in folder.glob("*.kt")
        )
        if not found:
            failures.append(f"the manifest names .car.{name}, and no such class exists")
    return failures


def check_nothing_privileged_and_no_double(manifest: str) -> list[str]:
    failures = []
    for forbidden in ("android.permission.INJECT_EVENTS", "android.permission.WRITE_SECURE_SETTINGS"):
        if forbidden in manifest:
            failures.append(f"{forbidden} is privileged; an unprivileged app never asks for it")
    for path in MAIN.rglob("FakeLibraryService.kt"):
        failures.append(f"{path.relative_to(APP)} ships a test double in the release build (Headway B-016)")
    return failures


def main() -> int:
    if not MANIFEST.is_file() or not SERVICE.is_file():
        print(f"FAIL  {MANIFEST if not MANIFEST.is_file() else SERVICE} is missing", file=sys.stderr)
        return 1
    manifest = MANIFEST.read_text(encoding="utf-8")
    failures = (
        check_the_service_declares_its_types(manifest)
        + check_the_global_names_are_declared_once(manifest)
        + check_nothing_points_at_headways_app(manifest)
        + check_nothing_privileged_and_no_double(manifest)
    )
    for failure in failures:
        print(f"FAIL  {failure}", file=sys.stderr)
    if failures:
        print(f"\n{len(failures)} failure(s)", file=sys.stderr)
        return 1
    print(
        "car manifest: CarLinkService declares the types it claims, the two global names "
        "are declared once, nothing points at Headway's app, nothing is privileged"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
