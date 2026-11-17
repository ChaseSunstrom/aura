"""`python -m aura_voice` — the container's entry point."""

from __future__ import annotations

import logging
import os

import uvicorn

from .app import create_app

#: Loopback by default. The service has no authentication (see `app.py`), so
#: binding it anywhere reachable would hand the house voice to the LAN.
HOST = os.environ.get("COSYVOICE_HOST", "127.0.0.1")
PORT = int(os.environ.get("COSYVOICE_PORT", "8890"))


def main() -> None:
    logging.basicConfig(level=os.environ.get("COSYVOICE_LOG_LEVEL", "INFO"))
    uvicorn.run(create_app(), host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
