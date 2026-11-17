# ADR-0028: The SSRF address parser is one file kept in two trees

- **Status:** Accepted (Phase 2)
- **Reference:** the v2 plan §10 (D28)

## Context

Two trees have to read the host out of a URL before deciding anything about it. `jarvis-desktop` is a
pip package that runs on a person's own machine, where `http_request` fetches public pages and the LAN
is off limits. `jarvis-core` is the house hub, where a tool pointing at the NAS is the ordinary case
rather than an attack. The policies are opposite, but the reading underneath them is the same problem:
`inet_aton` accepts decimal, octal, hex and IPv4-in-IPv6 spellings of loopback, so `127.0.0.1`,
`0177.0.0.1`, `0x7f.1` and `::ffff:127.0.0.1` are one address wearing four hats. A guard that parses
only the spelling it expects is not a guard.

Sharing that reading by import was considered and rejected on deployment grounds, not taste. The two
artefacts release separately and neither may depend on the other: the desktop package cannot pull in
the hub, the hub image is built from `aura-core/` alone, and publishing a third distribution to
carry 120 lines buys a release pipeline nobody wants to run.

## Decision

The reading lives once, in `aura-desktop/aura_desktop/actions/ssrf_parse.py`.
`aura-core/aura/helpers/ssrf_parse.py` is generated from it by `make ssrf-sync`, which writes an
`@generated` header and wraps the text in `jscpd:ignore` markers, the same mechanism the token files
use. `aura-core/tests/test_ssrf.py` fails when the generated file is stale or has been hand-edited,
so a fix lands in the desktop source and is regenerated rather than applied twice.

Each tree's `ssrf.py` stays policy only — its blocked-range tables, its `check`, and on the desktop
the DNS re-check. The same test refuses a private `parse_ipv4` reappearing beside the shared one, so
the pair cannot quietly become three.

## Consequences

The duplication gates stay honest without being lied to: jscpd skips the generated file because of the
markers, and `py-dup`'s pylint scope does not reach jarvis-desktop, so neither gate reports a pair that
is deliberate. The cost is that a change to address parsing is two commands rather than one, and a
contributor who edits the generated file learns this from a failing test rather than from the file's
own header — which is why the header names the source file and the command.

This does **not** cover the Kotlin mirror. `SsrfGuard.kt` remains the android-app's own implementation,
bound to its Python mirror by a shared table under `tests/contracts/` the way the other Kotlin↔Python
pairs are, because generating Kotlin from Python would need a translator no one would maintain.

Related: [ADR-0023](0023-design-system-token-source.md) for the generated-file pattern this borrows,
and [ADR-0025](0025-sandbox-disposable-ct.md) for the other half of the egress boundary.
