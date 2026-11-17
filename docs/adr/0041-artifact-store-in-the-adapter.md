# ADR-0041: The artifact store lives in the adapter, and an id is a digest

- **Status:** Accepted (2026-09-09)
- **Comes from:** the media brief, whose build order puts the store first — *"Artifact store service, artifact tool, delivery events, retention — with tests, before any UI"*
- **Relates to:** [ADR-0004](0004-thin-adapter.md) (the adapter adds no agent logic)

## Context

The brief names the pattern it wants gone: *"Retire the 'here's a path on the container' pattern
entirely."* A path is an answer only to something with a shell on that container. It is not an answer
to the phone, the desktop app, or a person reading the console.

It also names the store's constraints: *"a single service with a content-addressed store on a
volume … auth by the same bearer key as the API server; nothing reachable off the tailnet."*

## Decision

**The store is part of the adapter, not a new service.** The adapter already terminates the apps'
bearer key, already binds to the tailnet only, and already mounts `jarvis-hermes-home` at
`/opt/data`. A second container would need its own copy of all three and would still have to be
reached through this one. ADR-0004 says the adapter adds no *agent* logic; a read-write blob store
with no model in it does not violate that, and putting it anywhere else would.

**The agent reaches it as a client.** `hermes/plugins/jarvis-artifacts` holds no store logic —
otherwise the retention rules and the size caps would exist twice and drift.

**An id is the SHA-256 of the bytes.** Two tools that produce the same image produce one artifact, a
re-publish after a retry is free, and an id can never point at content that changed underneath it.
The cost is that metadata lives beside the blob rather than in its name, which is what the sidecar
JSON is for — and a sidecar per blob survives a crash mid-write in a way a single index does not: a
half-written sidecar loses one artifact's metadata, not the catalogue.

**No database.** A store this size does not need one, and the failure mode above is better.

### Two rules that came out of the security review, and are the interesting part

**An id is validated by shape, at the choke point.** Every id becomes a filesystem path, and
`_blob(root, '../../../../etc/passwd')` resolved to `/etc/passwd` — which `delete` would `unlink()`
and `read_range` would serve. The guard is `^[0-9a-f]{64}$` rather than path normalisation, because
an id is a digest *by construction*: there is no legitimate id this rejects, which is what makes it
a guard and not a heuristic. It is enforced **on both sides of the boundary** — the plugin builds a
URL from an id before the adapter ever sees it, so each side protects a different step.

**Browser-executable types are served as downloads.** The store holds whatever a tool produced,
including a web page. A published `text/html` or SVG served inline runs its script on the tailnet
origin that also serves the console and this API. Media types are untouched, so a video still
streams inline and an image still displays.

## Consequences

- Delivery events — the third piece of the brief's first step — are **not** decided by this ADR. The
  HUD panel contract that the console and Android both read has an `image` kind but no `video`,
  `audio` or `document`, so an image artifact could be delivered today and nothing else could.
  Adding kinds is a change across two renderers and belongs with the media panels.
- The store is bounded by two caps checked *before* a write rather than after: one artifact's size,
  enforced as bytes arrive, and the whole store's. The volume is shared with the model cache and the
  Hermes state, so one careless download must not fill it.
- Retention is a TTL with a pin, swept on demand. An expired artifact cannot be pinned, because
  pinning goes through the same read that hides it — otherwise "expired" would mean "expired unless
  someone knows the id".

## Addendum (2026-09-18): the store's own volume, and a URL that proves itself

Three things the store could not do in production, found by the 2026-09-18 research and fixed
in the adapter and the compose file.

**The store never wrote.** The decision above leaned on the adapter *already mounting
`jarvis-hermes-home` at `/opt/data`*, and put the blobs at `/opt/data/artifacts`. That mount is
read-only on purpose: the kill switch is read off it, and releasing the switch must stay a
host-side act. So `publish()`'s first `mkdir` was an EROFS, and every publish an unhandled 500.
The fix is a volume of the store's own, `jarvis-artifacts`, mounted read-write at `/opt/artifacts`
— a **sibling** of the home, not a child. Nesting a writable volume under the read-only one was
the obvious cut and does not work: runc makes the mountpoint on the parent filesystem, and on a
read-only parent the container fails to start (`make mountpoint "/opt/data/artifacts": ... read-only
file system`, tried on the host with two throwaway volumes). The image creates `/opt/artifacts`
owned by the adapter user, because a fresh named volume takes the image directory's owner and a
root-owned one is an EACCES from uid 10001. A store that still cannot write answers **507** with
the reason, and logs it, instead of a traceback. `deploy/tests/test_pipeline.py` pins the two
mounts, the ownership line and the path the store writes to.

**Retention never ran.** `sweep()` had no caller, so "expired" meant hidden, never deleted, and the
store only grew. The adapter now sweeps once at startup and every 24 hours (`sweep_forever` in
`app.py`); a sweep that raises is logged and the next one still runs; the task is cancelled on
shutdown.

**A media element could not fetch.** `GET /api/artifacts/{id}` required the bearer, and a
`<video src>` or `<img src>` cannot send one. Every JSON row (publish, list, pin) now carries a
`signed_url`: `/api/artifacts/{id}?exp=<unix>&sig=<hmac>`, with `sig` the HMAC-SHA256 over
`id|exp` under the apps' bearer key and `exp` one hour out. The route accepts a valid signature
*or* the bearer; the check is constant-time and the expiry enforced; a bad, stale or borrowed
signature falls through to the bearer check and is refused with the same 401 as no key at all.
Range requests work over the signed route, so a video still seeks. The existing `url` field the
plugin adds is unchanged.

**What this does not cover.**

- A leaked signed URL is valid until its `exp`. The hour is the window; the tailnet bind is the
  outer wall, and it is the same wall the bearer has.
- The signature is over the id and the expiry only. It does not bind a viewer, a session or a
  range, so anyone on the tailnet holding the URL can read that artifact for the hour.
- The `jarvis-artifacts` volume is **not in the backup**: `jarvis-backup` is one tar of the home.
  Whether a day's generated media is worth a tarball is an open decision, not one this makes.
- The sweep is per-process and time-based; two adapters on one volume would both sweep (harmless,
  `delete` is idempotent), and a store the sweep cannot read is logged, not repaired.
