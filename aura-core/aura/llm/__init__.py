"""Clients of the model server, and what it serves.

`ollama.OllamaClient` speaks Ollama's own wire and `openai_compat.OpenAICompatClient`
`/v1/chat/completions`; both present the same `chat` / `list_models` /
`is_available` / `aclose` surface, so the house's own helpers (vision,
research, the coding job) take either without knowing which. `rerank.Reranker`
orders candidates through a reranking server and `catalogue` resolves what the
model server actually serves for the settings page. `spoken_answers` reads a
spoken yes or no.

No conversation agent lives here any more. Hermes is the brain (ADR-0012 and
its addendum); the tool registry the model used to call moved to
:mod:`aura.tools`, and console-authored tools to :mod:`aura.authored_tools`.
"""

from __future__ import annotations
