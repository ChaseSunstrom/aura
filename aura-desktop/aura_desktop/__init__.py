"""jarvis-desktop — the Jarvis device agent for Linux, macOS and Windows.

The same automation capability as the phone app, with the same policy model
enforced in the same order. The LLM runs on the server; this process decides,
locally and outside the model, what is actually allowed to happen to the
machine it runs on.

Read in this order:

* :mod:`aura_desktop.policy`   — the tier/decision model. Start here.
* :mod:`aura_desktop.actions`  — what can be done, and the one door it goes through.
* :mod:`aura_desktop.consent`  — the Tier-3 prompt.
* :mod:`aura_desktop.channel`  — the wire protocol.
* :mod:`aura_desktop.audit`    — what was done, written down.
"""

import os as _os

# Env names say AURA_ since T-0959, but a desktop's own environment (a systemd unit, a
# shell profile) may still say JARVIS_: each JARVIS_ name is read under its AURA_ name
# unless that is set. Here, before any module reads the environment at import.
for _name, _value in list(_os.environ.items()):
    if _name.startswith("JARVIS_"):
        _os.environ.setdefault("AURA_" + _name[len("JARVIS_"):], _value)

from .policy import ActionTier, Decision, PolicyEngine, TrustLevel, UserPolicy  # noqa: E402

__version__ = "0.1.0"

__all__ = [
    "ActionTier",
    "Decision",
    "PolicyEngine",
    "TrustLevel",
    "UserPolicy",
    "__version__",
]
