"""jarvis-adapter: bearer-auth front door for the Hermes interfaces JARVIS's apps use."""
from .app import create_app

__all__ = ["create_app"]
