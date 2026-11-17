"""The JARVIS Capability Benchmark (JCB) harness.

``bench`` loads YAML task files (:mod:`bench.schema`), runs each task against a live
JARVIS stack (:mod:`bench.runner`), scores the result with a deterministic or
model-judged checker (:mod:`bench.checkers`), and writes a JSON + Markdown report
with a regression gate (:mod:`bench.report`). See the v2 plan §2 (D15/D16) for the
design this implements.
"""

from __future__ import annotations

#: Ten benchmark categories from the v2 plan §2.4. The loader rejects any other
#: value so a typo in a task file fails validation instead of silently creating an
#: eleventh category that no report row would ever count.
CATEGORIES: tuple[str, ...] = (
    "web-research",
    "browser-action",
    "homelab-ops",
    "coding-selfext",
    "personal-data",
    "proactive-scheduled",
    "multimodal",
    "voice",
    "safety",
    "intelligence",
    # T-0731: a request carried through end to end, the way the owner asks for things.
    "everyday",
)

__all__ = ["CATEGORIES"]
