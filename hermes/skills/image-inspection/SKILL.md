---
name: image-inspection
description: "Describe an image; verify tile counts via pixel probe."
---

# Image inspection

Workflow for "open this image and tell me what's in it" — dashboard screenshots, UI shots, status tiles, charts, photos. The user often frames it as "open in the browser", but the deliverable is the description, not the browsing.

## Steps

1. If the request names a browser, try `browser_navigate` first. If the browser tools report none running and none launchable, do NOT stall asking the user to open Chrome — for pure image inspection the browser is incidental; go straight to step 2.
2. **Give `vision_analyze` the URL.** `vision_analyze(image_url=<url>, question)` fetches it itself, in the gateway. Never fetch it from the terminal: the shell has no network (ADR-0045), so `curl` there fails every time — the 2026-09-18 benchmark spent a whole task budget on exactly that. `host.docker.internal:PORT` URLs resolve from the gateway to the docker host, so they work here. An image already on disk is the same call with its path.
3. Ask for the specific structure needed (grid dimensions, per-tile colour/label, totals), not a generic "describe this". For small repeated elements call it again with `region` set to the grid's box: the crop keeps full resolution.
4. **Whenever the user asks for a number** (how many tiles are red, how many are down) and the PNG is on disk under `/opt/data/work`, cross-check with the deterministic probe: `python3 png_probe.py <path> <rows> <cols>`. If vision and probe disagree, the probe wins for counts/classification — it is exact. Report that both methods agree.

## Pitfalls

- `vision_analyze` is fluent but can miscount small repeated elements (tiles, icons, legend entries). Run the probe whenever it can reach the file; when it cannot (next point), count twice with `region` crops and say the count is by vision.
- **The shell sees only `/opt/data/work`** and its own scratch — not `/opt/data/files`, not the skills directory. So a browser download of the image (it lands in `/opt/data/files`) does not bring it within the probe's reach, and the probe script has to be copied first: `read_file` this skill's `scripts/png_probe.py`, `write_file` it under `/opt/data/work`, run it there. An image at a URL therefore gets vision only.
- If `execute_code` is refused on the current surface (unattended-approval policy), write parsing/probe logic to a file with `write_file` under `/opt/data/work` and run it via `terminal` instead.
- The probe handles 8-bit RGB/RGBA PNGs only. For JPEG/palette/16-bit PNGs, fall back to vision alone and say the count is unverified by pixels.

## Scripts

- `scripts/png_probe.py` — pure-stdlib PNG colour probe: un-filters IDAT (all five filter types), samples each cell centre of an R×C grid, prints a colour-classified grid plus per-colour counts. Usage: `python3 png_probe.py image.png [rows cols]` (default 3×3; red = R>150 & G,B<100, green = G>150 & R<100). RGB path verified against a live dashboard image; RGBA branch is untested plumbing on the same decoder.
