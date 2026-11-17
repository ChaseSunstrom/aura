# ADR-0050: the apps update themselves from the tailnet, and the console hands out the first copy

- **Status:** Accepted (2026-09-24, on the owner's request)
- **Comes from:** the owner, 2026-09-24: *"can you have the console allow for downloading the apk/desktop app? and in the android app/desktop app allow for auto updating via the tialnet in the app and updating the APK from it automatically (it should be updated automatically when the apk/desktop builds in the pipeline, so it should always be the latest)"*
- **Reverses:** the judgement in `UpdateChecker.kt` that a phone update always waits for a person's INSTALL tap

## Context

Both apps already had an updater. The phone read the project's GitLab releases once a day and
offered a build for INSTALL; the desktop shell polled a generic-package feed every six hours,
downloaded in the background and installed on quit. Neither had moved since 2026-09-08,
because nothing had been *published*: `apk:release` and the desktop publish ran only for `main`
and tags, while every pipeline of the branch production runs is a merge-request pipeline
(and until `d9932365` none of those even deployed).

## Decision

- **The pipeline publishes the branch production runs.** `apk:release`, the desktop shell's
  upload and `desktop:shell:release` also run for a merge-request pipeline whose source branch is
  `claude/jarvis-overhaul`, as production builds (notes that do not open with "Automatic build",
  which the phone reads as a test build). So a green pipeline is the latest the apps move to.
- **The phone installs a production build by itself.** It checks hourly — on resume, and from an
  hourly WorkManager job for the phone nobody opens — and `AutoUpdate.act` installs a new
  production build unattended when **Install updates automatically** is on (the default).
  Unattended is `USER_ACTION_NOT_REQUIRED` on the install session (API 31+, with Android 13's
  `UPDATE_PACKAGES_WITHOUT_USER_ACTION`): a request the platform grants only to the app that
  installed the current version, for a package signed with the same key. Where it refuses —
  below API 31, or the first update after a browser install — the prompt arrives as a
  notification. A test build never installs itself.
- **The desktop shell checks hourly and applies an update when it is idle.** Downloaded builds
  still install on quit; a tray app that never quits now also relaunches into the update once the
  agent is idle, the window is not the one in use, and two minutes have passed
  (`readyToApply`, tested) — never mid-turn or mid-consent.
- **The console offers the first copy.** Settings shows the newest production phone build and
  the newest desktop build from the same releases, with a QR of the APK's link for the phone's
  camera. Links are kept only when they are https on the GitLab host.

## What this costs

The old paragraph was right about the risk: an app that replaces itself without a tap needs only
one bad release to become something else. What stands between a bad actor and the phone now is
the signing key (Android refuses an update signed differently) and the tailnet (the feed and the
APK are on GitLab's tailnet host, and `ReleaseFeed` refuses any other). A stolen GitLab token can
publish a release; it cannot sign one. A stolen signing key can. The setting turns it off.

Not proven here: an unattended install on a real handset (GrapheneOS included), and the desktop
relaunch on a real AppImage install. Both are in `docs/verification.md` as Unproven.

## Addendum, 2026-11-08: a second source, the Jarvis store (T-0735)

The owner put it as "how can we have the download behaviour work with either one": hand-built
APKs had gone into the adapter's artifact store, where the phone's updater never looked. The phone
now also reads the paired adapter's `/api/artifacts` listing with its key. An APK titled
`Jarvis <versionName>` there is offered beside GitLab's releases, and the newer build wins. It
downloads from that adapter's own `/api/artifacts/<64-hex id>` with the key, by exact prefix, in
whatever scheme the phone was paired with. The console's Devices screen lists it the same way,
through its own `/hermes` proxy.

What this costs: anything holding an adapter key can store a file, so the store, unlike a GitLab
release, does not need a GitLab token. The signing key is still what installs: a file signed
differently is refused by Android. What a key holder *can* do is store a bogus "Jarvis 1.0.99999"
that the phone keeps choosing and failing to install, hiding newer real builds until it is deleted
or expires. Not covered: CI's builds reaching a phone that cannot reach GitLab. The adapter does not
mirror GitLab.
