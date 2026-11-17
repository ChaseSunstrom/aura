# ADR-0004: A thin jarvis-adapter fronts Hermes with one bearer key

- **Status:** Accepted
- **Reference:** the migration plan §2, §6 (D4)

## Context

The apps need one origin and one credential, not direct access to two Hermes surfaces (the API server and `hermes serve`) with two tokens.

## Decision

A thin `jarvis-adapter` (FastAPI, its own image) fronts `hermes serve` (loopback) and the API server with one bearer key.

## Consequences

The apps hold one key and one URL. The adapter proxies `/v1/*`, `/api/sessions*`, `/api/jobs*` and `/api/audio/*`, and relays the HUD and speak sockets. It shares the `jarvis` container's network namespace so `:8643` rides its port map. Endpoints are in `docs/reference/api-endpoints.md`.
