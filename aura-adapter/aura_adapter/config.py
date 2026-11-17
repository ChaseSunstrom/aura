"""Settings for the JARVIS adapter, read once from the one settings schema.

The adapter is the single origin the apps talk to. It holds two upstream
credentials that never leave the container network namespace:

* ``HERMES_API_KEY``   — the Hermes API server's ``API_SERVER_KEY`` (port 8642)
* ``HERMES_SERVE_TOKEN`` — the ``HERMES_DASHBOARD_SESSION_TOKEN`` of the
  loopback-bound ``hermes serve`` backend (port 9119)

and one credential the apps present: ``AURA_API_KEY``.

Every value comes from ``aura_schema.from_env()`` (V2 D17): the variable
names, types and defaults live in the schema, a mistyped value fails at start
naming the variable, and this module never reads the process environment itself. The
dataclass keeps the attribute names the rest of the adapter (and its tests,
which build one by hand) already use.

The settings plugin's override file (ADR-0044) is read here too, from the Hermes
home the compose file mounts read-only at ``/opt/data``: a value an operator set
for an adapter leaf (``adapter.*``, ``network.*``) then applies on this process's
next start, the same way the entrypoint applies it to the gateway. The file is
``0640`` to the gateway's group, which the adapter's uid is in; when this uid cannot read it the adapter says so
once and runs on its environment alone, which is what it did before the file.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from aura_schema import from_env, load_overrides, overrides_path

_LOGGER = logging.getLogger(__name__)

#: The Hermes home as the compose file mounts it here (``hermes-home:/opt/data:ro``) —
#: the same fixed path ``status.py`` reads the kill switch from.
HERMES_HOME: str = "/opt/data"

#: The schema's own defaults (no environment): what a hand-built ``Settings()``
#: falls back to, so a test and the runtime agree on every unset value.
_DEFAULTS = from_env({})


def _origins(raw: str) -> list[str]:
    return [o.strip() for o in raw.split(",") if o.strip()]


def _overrides() -> dict[str, str]:
    """The env overrides this process may apply, or none with one warning."""
    path = overrides_path(HERMES_HOME)
    try:
        return load_overrides(path)
    except (OSError, ValueError) as exc:
        _LOGGER.warning("settings overrides at %s not applied (%s); running on the environment alone",
                        path, exc)
        return {}


@dataclass(frozen=True)
class Settings:
    api_key: str = _DEFAULTS.apps_api_key
    hermes_api_url: str = _DEFAULTS.hermes_api_url
    hermes_api_key: str = _DEFAULTS.hermes_api_key
    hermes_serve_url: str = _DEFAULTS.hermes_serve_url
    hermes_serve_token: str = _DEFAULTS.hermes_serve_token
    llama_swap_url: str = _DEFAULTS.llama_swap_url
    llm_api_key: str = _DEFAULTS.model_api_key
    model: str = _DEFAULTS.adapter_model
    # The model manager inside jarvis-models (deploy/models/manager.py). Empty: the llama-swap
    # host on port 8001, which is where the entrypoint starts it; not a schema setting because
    # nothing but that pairing ever sets it.
    models_manager_url: str = ""
    # Presented to the manager on every relayed call; the manager refuses anything without it.
    models_api_key: str = _DEFAULTS.models_api_key
    cors_origins: list[str] = field(default_factory=list)
    public_url: str = _DEFAULTS.network_public_url
    # Long: the model may be cold-starting (~2-3 minutes) behind any request.
    upstream_timeout: float = _DEFAULTS.adapter_upstream_timeout
    # Where the ASGI server binds and how loud it logs.
    log_level: str = _DEFAULTS.adapter_log_level
    host: str = _DEFAULTS.adapter_host
    port: int = _DEFAULTS.adapter_port
    # The voice. `cosyvoice` makes /api/audio/speak call the CosyVoice shim itself,
    # with the persona's voice, instead of relaying to hermes serve, whose speak has
    # no per-request voice (one `tts.openai.voice` for everything it says).
    tts_engine: str = _DEFAULTS.tts_engine
    tts_url: str = _DEFAULTS.tts_url
    # The house (jarvis-core), for the speaker gate: who is speaking, before
    # the transcript reaches the agent. Empty token: nobody is asked, every
    # turn is the key holder's.
    house_url: str = _DEFAULTS.house_url
    house_token: str = _DEFAULTS.house_token

    @classmethod
    def from_environment(cls) -> Settings:
        """One validated read of the environment plus the override file, mapped onto the adapter's names."""
        cfg = from_env(overrides=_overrides())
        return cls(
            api_key=cfg.apps_api_key,
            hermes_api_url=cfg.hermes_api_url,
            hermes_api_key=cfg.hermes_api_key,
            hermes_serve_url=cfg.hermes_serve_url,
            hermes_serve_token=cfg.hermes_serve_token,
            llama_swap_url=cfg.llama_swap_url,
            llm_api_key=cfg.model_api_key,
            models_api_key=cfg.models_api_key,
            model=cfg.adapter_model,
            cors_origins=_origins(cfg.adapter_cors_origins),
            public_url=cfg.network_public_url,
            upstream_timeout=cfg.adapter_upstream_timeout,
            log_level=cfg.adapter_log_level,
            host=cfg.adapter_host,
            port=cfg.adapter_port,
            tts_engine=cfg.tts_engine,
            tts_url=cfg.tts_url,
            house_url=cfg.house_url,
            house_token=cfg.house_token,
        )

    def validate(self) -> None:
        if len(self.api_key) < 16:
            raise RuntimeError("AURA_API_KEY must be set (>= 16 characters)")
        if not self.hermes_api_key:
            raise RuntimeError("HERMES_API_KEY must be set (the Hermes API_SERVER_KEY)")
        if not self.hermes_serve_token:
            raise RuntimeError("HERMES_SERVE_TOKEN must be set (HERMES_DASHBOARD_SESSION_TOKEN)")

    @property
    def speech_url(self) -> str:
        """The CosyVoice shim's OpenAI-shaped endpoint; empty AURA_TTS_URL means its own port here."""
        return f"{(self.tts_url or 'http://127.0.0.1:8890').rstrip('/')}/v1/audio/speech"

    @property
    def transcribe_url(self) -> str:
        """The same shim's Whisper on the card (aura_voice/stt.py), tried before Hermes' CPU one."""
        return f"{(self.tts_url or 'http://127.0.0.1:8890').rstrip('/')}/v1/audio/transcriptions"

    @property
    def serve_ws_url(self) -> str:
        base = self.hermes_serve_url.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
        return f"{base}/api/ws?token={self.hermes_serve_token}"

    @property
    def serve_speak_ws_url(self) -> str:
        base = self.hermes_serve_url.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
        return f"{base}/api/audio/speak-stream?token={self.hermes_serve_token}"


def reasoning_efforts() -> tuple[str, str]:
    """(voice, typed) reasoning effort, re-read from the override file on every call.

    The adapter's other settings are read once at start; this one is read per prompt so
    "think less when I talk" said by voice applies on the next turn (effort.py).
    ponytail: validates the whole schema per prompt (a few ms); cache on the file's mtime if it shows.
    """
    cfg = from_env(overrides=_overrides())
    return cfg.voice_reasoning_effort, cfg.model_reasoning_effort


def voice_speech() -> tuple[str, str]:
    """(voice.pronunciations, voice.quiet_hours), re-read on every speak-pcm call.

    So a name taught by voice, or quiet hours moved, apply to the next sentence (T-0587);
    the same per-call read as :func:`reasoning_efforts`, with the same ponytail.
    """
    cfg = from_env(overrides=_overrides())
    return cfg.voice_pronunciations, cfg.voice_quiet_hours


def load() -> Settings:
    s = Settings.from_environment()
    s.validate()
    return s
