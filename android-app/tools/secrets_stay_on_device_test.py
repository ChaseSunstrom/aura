#!/usr/bin/env python3
"""Executable spec for "the phone's secrets never leave the phone".

`JarvisConfig` keeps the house token, the adapter key and the scanned WireGuard
config (with its private key) in plain `jarvis_config` SharedPreferences. That
is safe only because nothing copies the prefs anywhere: backup is off, the API
31+ rules exclude them from cloud backup *and* device-to-device transfer, and
no log line prints one. Wrapping them in a Keystore key was weighed (T-0392)
and not done: the prefs already sit in credential-encrypted storage, and an
attacker who can run as the app can use its Keystore key anyway. So the
guarantee lives in these three facts, and this pins each of them.

Not covered: a rooted phone, or a log call that passes a secret through a
variable with another name.

Run:  python3 android-app/tools/secrets_stay_on_device_test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SECRETS = ("token", "adapterKey", "wireGuardConf")
DOMAINS = ("root", "file", "database", "sharedpref")
LOG_CALL = re.compile(r"\bLog\.[vdiwe]\(|\bprintln\(")
TEMPLATE = re.compile(r"\$\{([^}]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)")


def check_backup_is_off(manifest: str) -> int:
    failures = 0
    for attr in ('android:allowBackup="false"', 'android:fullBackupContent="false"'):
        if attr not in manifest:
            print(f"FAIL  the manifest lost {attr}: Auto Backup would upload jarvis_config")
            failures += 1
    return failures


def check_transfer_rules(android: Path, manifest: str) -> int:
    m = re.search(r'android:dataExtractionRules="@xml/([^"]+)"', manifest)
    if not m:
        print("FAIL  no dataExtractionRules: device-to-device transfer ignores allowBackup")
        return 1
    rules = (android / f"app/src/main/res/xml/{m.group(1)}.xml").read_text(encoding="utf-8")
    failures = 0
    for section in ("cloud-backup", "device-transfer"):
        body = re.search(rf"<{section}>(.*?)</{section}>", rules, re.S)
        excluded = set(re.findall(r'<exclude domain="([^"]+)"', body.group(1))) if body else set()
        for domain in DOMAINS:
            if domain not in excluded:
                print(f"FAIL  {section} does not exclude {domain}: the secrets can leave the phone")
                failures += 1
    return failures


def check_no_log_prints_a_secret(android: Path) -> int:
    failures = 0
    for path in sorted((android / "app/src/main/kotlin").rglob("*.kt")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not LOG_CALL.search(line):
                continue
            for expr in (a or b for a, b in TEMPLATE.findall(line)):
                if "Redact" in expr:
                    continue
                if any(re.search(rf"\b{s}\b", expr) for s in SECRETS):
                    print(f"FAIL  {path.relative_to(android)}:{n} logs ${{{expr}}} unredacted")
                    failures += 1
    return failures


def main() -> int:
    android = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1]
    manifest = (android / "app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
    failures = (
        check_backup_is_off(manifest)
        + check_transfer_rules(android, manifest)
        + check_no_log_prints_a_secret(android)
    )
    if failures:
        print(f"\n{failures} failure(s)")
        return 1
    print("secrets: backup off, cloud backup and device transfer exclude the prefs, no log prints one")
    return 0


if __name__ == "__main__":
    sys.exit(main())
