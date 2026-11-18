#!/usr/bin/env python3
"""Executable spec for choosing which GitHub or GitLab release to install.

`app/src/main/kotlin/ai/aura/app/update/ReleaseFeed.kt` turns a JSON document
fetched over the internet into "download this file and ask the user to install
it". That is the most dangerous sentence in the app, so the rules it applies
are written down twice: once in Kotlin, which this container cannot compile,
and once here, where they run.

The cases both languages run are `tests/contracts/release-feed.json`, read here
and by `ReleaseFeedTest.kt`; a case added there is checked on both sides.

The rules, and why each exists:

  * Newer only. Android refuses a package whose versionCode is not greater than
    the installed one, so offering an equal-or-older release produces a prompt
    that can only fail. Compared as an integer, never as tag text — `v1.10.0`
    sorts before `v1.9.0` as a string.
  * https, to a GitHub host, matched exactly. A release body is
    attacker-controllable text if the repo is ever public or a token leaks, and
    "fetch whatever URL the JSON said" is how an updater becomes a malware
    delivery service. Host equality rather than suffix: `evilgithub.com` ends
    with `github.com`.
  * Drafts are not releases: GitHub's `draft`, GitLab's `upcoming_release`.
  * GitLab's shape is read as well as GitHub's: `assets.links[]` with the URL in
    `direct_asset_url` (then `url`), the notes in `description`, and no prerelease
    flag, so a release whose notes open with "Automatic build" (the pipeline's
    per-push test build) is the prerelease.
  * A hand-built APK in the paired Jarvis adapter's artifact store is offered
    too (T-0735): its mime is an APK, its title `Jarvis <versionName>` names
    the code, its id is a 64-hex content hash, and it downloads only from the
    adapter's own `/api/artifacts/<id>`. The newer of GitLab's and Jarvis's
    builds wins.

Run:  python3 android-app/tools/release_feed_test.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# --- the rules, mirrored from ReleaseFeed.kt -------------------------------

#: The house's GitLab is the build's (BuildConfig.GITLAB_BASE, from CI's server, T-0841); this is
#: the placeholder a build without CI gets, which the Kotlin tests run with.
GITLAB_HOST = "gitlab.home.example"
ALLOWED_HOSTS = {GITLAB_HOST, "github.com", "api.github.com", "objects.githubusercontent.com"}


APK_MIME = "application/vnd.android.package-archive"
STORED_TITLE = re.compile(r"^Jarvis v?(\d+\.\d+\.(\d+))\b")
ARTIFACT_ID = re.compile(r"^[0-9a-f]{64}$")


def is_allowed_download(url: str, adapter_base: str | None = None) -> bool:
    text = url.strip()
    if any(ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F for c in text):
        return False
    base = (adapter_base or "").strip().rstrip("/")
    store = f"{base}/api/artifacts/" if base else None
    if store and text.startswith(store):
        return bool(ARTIFACT_ID.match(text[len(store):]))
    if not text.startswith("https://"):
        return False
    authority = text[len("https://") :].split("/")[0]
    if "@" in authority:
        return False
    host = authority.split(":")[0].lower()
    return host in ALLOWED_HOSTS


def pick_stored(listing: str, adapter_base: str, installed: int):
    base = adapter_base.strip().rstrip("/")
    if not base:
        return None
    try:
        rows = json.loads(listing).get("artifacts")
    except (ValueError, TypeError, AttributeError):
        return None
    best = None
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get("mime") != APK_MIME:
            continue
        ident = str(row.get("id") or "")
        match = STORED_TITLE.search(str(row.get("title") or ""))
        if not ARTIFACT_ID.match(ident) or not match:
            continue
        code = int(match.group(2))
        if code <= 0 or code <= installed or (best and code <= best["versionCode"]):
            continue
        best = {"versionCode": code, "versionName": match.group(1), "url": f"{base}/api/artifacts/{ident}"}
    return best


def newest(a, b):
    return b if a is None or (b is not None and b["versionCode"] > a["versionCode"]) else a


def _code_after_plus(text: str) -> int | None:
    plus = text.rfind("+")
    if plus < 0 or plus == len(text) - 1:
        return None
    digits = text[plus + 1 :].strip()
    if not digits or not digits.isdigit():
        return None
    value = int(digits)
    return value if value > 0 else None


def version_code_of(tag: str, label: str | None) -> int | None:
    return _code_after_plus(tag) or _code_after_plus(label or "")


def version_name_of(tag: str) -> str:
    stripped = tag[1:] if tag.startswith("v") else tag
    return stripped.split("+")[0] or tag


def pick(feed: str, installed: int, allow_prerelease: bool):
    try:
        releases = json.loads(feed)
    except (ValueError, TypeError):
        return None
    if not isinstance(releases, list):
        return None

    best = None
    for release in releases:
        if not isinstance(release, dict):
            continue
        # GitLab has `upcoming_release` for a draft and no prerelease flag: its per-push test
        # builds are the releases whose notes open with "Automatic build" (android-apk.yml).
        if release.get("draft") or release.get("upcoming_release"):
            continue
        notes = str(release.get("body" if "body" in release else "description") or "")
        prerelease = bool(release.get("prerelease")) or notes.lstrip().startswith("Automatic build")
        if prerelease and not allow_prerelease:
            continue
        tag = str(release.get("tag_name") or "").strip()
        if not tag:
            continue
        # GitHub: `assets` is a list. GitLab: `assets` is `{links: [...]}`.
        assets = release.get("assets")
        if isinstance(assets, dict):
            assets = assets.get("links")
        if not isinstance(assets, list):
            continue
        chosen = None
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            name = str(asset.get("name") or "")
            if not name.lower().endswith(".apk"):
                continue
            url = str(asset.get("browser_download_url") or asset.get("direct_asset_url")
                      or asset.get("url") or "")
            if not is_allowed_download(url):
                continue
            code = version_code_of(tag, str(asset.get("label") or ""))
            if code is None:
                continue
            chosen = {"versionCode": code, "tag": tag, "url": url, "notes": notes[:2000]}
            break
        if chosen is None or chosen["versionCode"] <= installed:
            continue
        if best is None or chosen["versionCode"] > best["versionCode"]:
            best = chosen
    return best


# --- the shared cases: tests/contracts/release-feed.json -------------------

CONTRACT_PATH = Path(__file__).resolve().parents[2] / "tests" / "contracts" / "release-feed.json"

#: The contract's placeholders, filled the way ReleaseFeedTest.kt fills them. The GitLab host is
#: this build's placeholder here and the build's own GitLab host there.
PLACEHOLDERS = {
    "{gitlab-host}": GITLAB_HOST,
    "{github-host}": "github.com",
    "{github-api-host}": "api.github.com",
    "{github-objects-host}": "objects.githubusercontent.com",
}


def load_contract() -> dict:
    text = CONTRACT_PATH.read_text(encoding="utf-8")
    for placeholder, host in PLACEHOLDERS.items():
        text = text.replace(placeholder, host)
    return json.loads(text)


def offered(got: dict | None) -> dict | None:
    """The fields a contract `want` names, as ReleaseFeedTest.kt reads them off Update."""
    if got is None:
        return None
    return {"versionCode": got["versionCode"], "versionName": version_name_of(got["tag"]),
            "url": got["url"], "notes": got["notes"]}


def check(label: str, got, want) -> int:
    if got != want:
        print(f"FAIL  {label}: got {got!r}, wanted {want!r}")
        return 1
    return 0


def check_contract(c: dict) -> int:
    failures = 0
    for case in c["downloads"]:
        got = is_allowed_download(case["url"], case["adapter"])
        failures += check(f"download {case['name']}", got, case["allowed"])
    for case in c["picks"]:
        view = offered(pick(json.dumps(case["feed"]), case["installed"], case["prerelease"]))
        want = case["want"]
        if want is None:
            failures += check(f"pick {case['name']}", view, None)
        else:
            failures += check(f"pick {case['name']}", {k: (view or {}).get(k) for k in want}, want)
    for bad in c["garbage"]:
        failures += check(f"garbage feed {bad!r}", pick(bad, 1, True), None)
    for case in c["stored"]:
        got = pick_stored(json.dumps(case["listing"]), case["adapter"], case["installed"])
        want = case["want"]
        if want is None:
            failures += check(f"stored {case['name']}", got, None)
        else:
            failures += check(f"stored {case['name']}", {k: (got or {}).get(k) for k in want}, want)
    garbage = c["storedGarbage"]
    for raw in garbage["raw"]:
        failures += check(f"stored garbage {raw!r}", pick_stored(raw, garbage["adapter"], 1), None)
    for case in c["newest"]:
        gitlab = pick(json.dumps(case["feed"]), case["installed"], False)
        store = pick_stored(json.dumps(case["listing"]), case["adapter"], case["installed"])
        best = newest(gitlab, store)
        failures += check(f"newest {case['name']}", best["versionCode"] if best else None, case["want"])
    for case in c["versions"]:
        tag = case["tag"]
        failures += check(f"version code of {tag!r}", version_code_of(tag, None), case["code"])
        failures += check(f"version name of {tag!r}", version_name_of(tag), case["version"])
    return failures


def check_kotlin_agrees(android: Path) -> int:
    src = android / "app/src/main/kotlin/ai/aura/app/update/ReleaseFeed.kt"
    if not src.is_file():
        print(f"FAIL  {src} is missing")
        return 1
    text = src.read_text(encoding="utf-8")
    failures = 0
    for host in ALLOWED_HOSTS - {GITLAB_HOST}:
        if f'"{host}"' not in text:
            print(f"FAIL  ReleaseFeed.kt no longer allows {host!r}")
            failures += 1
    if "URI(BuildConfig.GITLAB_BASE).host" not in text:
        print("FAIL  ReleaseFeed.kt no longer allows the build's GitLab (BuildConfig.GITLAB_BASE)")
        failures += 1
    gradle = (android / "app/build.gradle.kts").read_text(encoding="utf-8")
    if f'"https://{GITLAB_HOST}"' not in gradle:
        print(f"FAIL  build.gradle.kts's placeholder GitLab is no longer https://{GITLAB_HOST}")
        failures += 1
    for needed, why in [
        ('startsWith("https://")', "the https check"),
        ("draft", "the draft check"),
        ('"upcoming_release"', "GitLab's draft flag"),
        ('startsWith("Automatic build")', "the prerelease rule for the pipeline's test builds"),
        ('optJSONArray("links")', "GitLab's assets.links"),
        ('"direct_asset_url"', "GitLab's direct_asset_url"),
        ("prerelease", "the prerelease check"),
        ("isISOControl", "the control-character check"),
        ("ARTIFACT_ID.matches", "the store's content-id check"),
        ('"$it/api/artifacts/"', "the adapter's exact store prefix"),
        ("contains('@')", "the embedded-credentials check"),
    ]:
        if needed not in text:
            print(f"FAIL  ReleaseFeed.kt lost {why} ({needed})")
            failures += 1
    # Host membership must be exact, not a suffix test.
    if "endsWith" in text and "host" in text.split("endsWith")[0][-200:]:
        print("FAIL  ReleaseFeed.kt appears to match hosts by suffix")
        failures += 1
    return failures


def check_workflow_tags_the_code(repo: Path) -> int:
    """CI must put the versionCode in the tag, or nothing can read it.

    The app cannot know a release's versionCode without downloading the APK,
    so the tag is the contract. If the pipeline stops emitting `+<code>`, every
    release silently becomes un-offerable.
    """
    wf = repo / ".gitlab/ci/android-apk.yml"
    if not wf.is_file():
        print(f"FAIL  {wf} is missing")
        return 1
    text = wf.read_text(encoding="utf-8")
    if "AURA_VERSION_CODE" not in text:
        print("FAIL  the pipeline no longer sets AURA_VERSION_CODE")
        return 1
    if not re.search(r"\+\$\{?\s*(AURA_)?VERSION_CODE", text) and "+$CODE" not in text:
        print("FAIL  the pipeline's tag no longer carries +<versionCode>")
        return 1
    return 0


def main() -> int:
    here = Path(__file__).resolve()
    android = here.parents[1]
    repo = here.parents[2]
    c = load_contract()

    failures = (
        check_contract(c)
        + check_kotlin_agrees(android)
        + check_workflow_tags_the_code(repo)
    )
    if failures:
        print(f"\n{failures} failure(s)")
        return 1
    total = (len(c["downloads"]) + len(c["picks"]) + len(c["garbage"]) + len(c["stored"])
             + len(c["storedGarbage"]["raw"]) + len(c["newest"]) + len(c["versions"]))
    print(
        f"release feed: {total} shared cases from tests/contracts/release-feed.json, "
        "the Kotlin and the workflow all agree"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
