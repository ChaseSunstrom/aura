"""Kokoro behind an OpenAI-compatible speech endpoint, with Whisper and voiceprints, for the house voice."""

from __future__ import annotations

from .app import create_app

__all__ = ["create_app"]
