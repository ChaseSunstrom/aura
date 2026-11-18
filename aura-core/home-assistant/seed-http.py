"""Seed Home Assistant's HTTP config store before its first start, so its page never leaves AURA_HA_BIND.

HA 2026.11 keeps its HTTP settings in ``.storage/http`` as a stable/pending pair. With no store,
it migrates configuration.yaml's ``http:`` block as a five-minute PENDING trial, and an admin
must promote that trial or HA reverts to STABLE, which is the default: every interface. Under
host networking, an HA that AURA has not onboarded within those five minutes (jarvis-core down,
say) would then serve its onboarding page, which has no login yet, to the whole LAN (T-1525).

This writes the store as HA would after a promote: STABLE = HA's own default with
``server_host`` set to the bind, no pending trial, and the YAML migration marked done. HA's own
module supplies the format, so this runs inside HA's image (the compose entrypoint calls it,
then execs ``/init``).

Not covered: an existing store, which is never touched, so an HA someone already configured
keeps its settings and AURA's keeper (setup._hold_bind) holds its bind instead. A later HA that
changes the store's format: the image is pinned by digest, and the live check runs this seed
in that image.
"""
from __future__ import annotations

import json
import os
import sys
from enum import Enum
from pathlib import Path
from types import ModuleType
from typing import Any


def store_body(conf: ModuleType, bind: str) -> dict[str, Any]:
    """The ``.storage/http`` document HA writes after a promoted config of ``server_host: [bind]``."""
    # An enum (ssl_profile) is stored as its value: HA reads it back with SSLProfile(value).
    stable = {k: (v.value if isinstance(v, Enum) else v) for k, v in conf._DEFAULT_CONFIG.items()}
    stable["server_host"] = [bind]
    return {"version": conf.STORAGE_VERSION, "minor_version": conf.STORAGE_MINOR_VERSION,
            "key": conf.STORAGE_KEY,
            "data": {conf.KEY_STABLE: stable, conf.KEY_PENDING: None, conf.KEY_YAML_MIGRATION_DONE: True}}


def seed(config_dir: Path, bind: str, conf: ModuleType) -> bool:
    """Write the store unless one exists. True when it wrote one."""
    path = config_dir / ".storage" / conf.STORAGE_KEY
    if not bind or path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".aura-seed")
    # HA keeps this store private (Store(private=True)): 0600 from the first byte.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(store_body(conf, bind), fh, indent=4, default=str)
    os.replace(tmp, path)
    return True


def main() -> int:
    from homeassistant.components.http import config as conf  # only inside HA's image

    bind = os.environ.get("AURA_HA_BIND", "127.0.0.1").strip()
    wrote = seed(Path(sys.argv[1] if len(sys.argv) > 1 else "/config"), bind, conf)
    print(f"[aura-seed] http store {'seeded on ' + bind if wrote else 'left as it is'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
