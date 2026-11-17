"""`python -m aura_adapter` — run the adapter with uvicorn."""
from __future__ import annotations

import logging

import uvicorn

from .app import create_app
from .config import Settings


def main() -> None:
    # One validated read of the environment (aura-schema via config.Settings); the
    # server's bind address and log level come from it too, not a second lookup.
    settings = Settings.from_environment()
    logging.basicConfig(level=settings.log_level,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    uvicorn.run(create_app(), host=settings.host, port=settings.port, log_level="info",
                ws_max_size=64 * 1024 * 1024, timeout_keep_alive=75)


if __name__ == "__main__":
    main()
