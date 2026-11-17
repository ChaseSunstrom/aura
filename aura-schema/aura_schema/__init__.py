"""The one JARVIS settings schema (V2 D17).

Import surface:

* :class:`~aura_schema.settings.Settings` — the model, one typed attribute per
  setting; :func:`~aura_schema.settings.from_env` reads the environment once
  and returns a validated instance (what the plugins and the adapter read).
* :data:`~aura_schema.settings.SETTINGS` — the registry, one :class:`Setting`
  per setting, derived from the model.
* :func:`~aura_schema.settings.validate_settings` — fail-fast validation of a
  settings mapping (unknown key / wrong type both name the key);
  :func:`~aura_schema.settings.coerce` is the same for one value.

The generators (config template, ``jsettings`` contract, settings reference) live
in :mod:`aura_schema.generate` and run behind ``make schema``.
"""
from __future__ import annotations

from .settings import (
    GROUP_DESCRIPTIONS,
    HOST_OWNED_ENV,
    OVERRIDES_REL,
    SETTINGS,
    SETTINGS_BY_ENV,
    SETTINGS_BY_HERMES_KEY,
    SETTINGS_BY_KEY,
    Group,
    Kind,
    LayoutDocument,
    Setting,
    Settings,
    SettingsError,
    Widget,
    applies,
    coerce,
    env_string,
    from_env,
    load_overrides,
    overrides_path,
    resolve,
    settable_settings,
    validate_settings,
)

__all__ = [
    "GROUP_DESCRIPTIONS",
    "HOST_OWNED_ENV",
    "OVERRIDES_REL",
    "SETTINGS",
    "SETTINGS_BY_ENV",
    "SETTINGS_BY_HERMES_KEY",
    "SETTINGS_BY_KEY",
    "Setting",
    "Settings",
    "LayoutDocument",
    "Kind",
    "Group",
    "Widget",
    "SettingsError",
    "applies",
    "coerce",
    "env_string",
    "from_env",
    "load_overrides",
    "overrides_path",
    "resolve",
    "settable_settings",
    "validate_settings",
]
