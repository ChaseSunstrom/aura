"""What the Hermes image may call on its own, pinned (T-0844).

T-0839 shipped because nothing in the repo looked at what the third-party Hermes process
fetches with nobody asking; the T-0767 capture was the first look. This file pins what that
look found: each host is off, and stays off, or the test fails.

Not covered: a fetch that a Hermes release adds in its own code. That cannot be seen from
this repo, so the image tag the capture was read against is pinned instead: a bump fails
here until someone reads a new capture (`docs/privacy/egress.md`, "The live
capture") and moves the pin. Whether the deployed container really stays quiet is the live
sample's job (`scripts/egress-audit.sh --live`), not this file's.
"""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

DEPLOY = Path(__file__).resolve().parents[1]
TEMPLATE = (DEPLOY / "config.template.yaml").read_text(encoding="utf-8")
DOCKERFILE = (DEPLOY / "Dockerfile").read_text(encoding="utf-8")

# The Hermes release whose own fetches were read (the T-0767 capture, T-0839's fixes; v0.23.2
# on the T-1519 throwaway gateway, where its baked Chrome called Google until chromium_args).
REVIEWED_TAG = "v0.23.2"

# Hosts a URL in Hermes's config may name: the container itself and services on the stack's
# own network. Anything else is a call out of the house and needs a row in
# docs/privacy/egress.md before it is added here.
STACK_HOSTS = {"127.0.0.1", "localhost", "searxng"}

# What the capture saw Hermes ask for on its own, and the text that keeps each off.
OFF = {
    "hermes-agent.nousresearch.com": (TEMPLATE, r"^model_catalog:\n  enabled: false$"),
    "models.dev": (TEMPLATE, r'^models_dev:\n  url: "http://127\.0\.0\.1:9/[^"]*"$'),
    "the update check": (TEMPLATE, r"^updates:\n  check: false$"),
    "openrouter.ai": (DOCKERFILE, r"s#\^_OPENROUTER_CATALOG_URL = \.\*#_OPENROUTER_CATALOG_URL = \"http://127\.0\.0\.1:9/"),
}


def outside_hosts(config: str) -> set[str]:
    """The hosts of every URL in [config] that are not the stack's own."""
    hosts = {urlsplit(u).hostname or "" for u in re.findall(r"https?://[^\s\"'#]+", config)}
    return hosts - STACK_HOSTS


def test_hermes_egress_config_names_no_host_outside_the_stack():
    assert outside_hosts(TEMPLATE) == set()


def test_hermes_egress_check_sees_a_new_outside_url():
    grown = TEMPLATE + '\nnew_feature:\n  url: "https://catalogue.example.com/v1/list"\n'
    assert outside_hosts(grown) == {"catalogue.example.com"}


@pytest.mark.parametrize("host", sorted(OFF))
def test_hermes_egress_each_fetch_the_capture_saw_is_off(host):
    text, switch = OFF[host]
    assert re.search(switch, text, re.M), f"{host} is no longer switched off (docs/privacy/egress.md)"


def test_hermes_egress_was_read_for_this_hermes_release():
    tag = re.search(r"^ARG HERMES_TAG=(\S+)$", DOCKERFILE, re.M).group(1)
    assert tag == REVIEWED_TAG, (
        f"Hermes moved to {tag}; its own fetches were read for {REVIEWED_TAG}. Take a new capture "
        "(docs/privacy/egress.md), switch off what is new, then move REVIEWED_TAG."
    )
