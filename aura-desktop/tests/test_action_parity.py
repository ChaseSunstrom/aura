"""The desktop side of the PC <-> phone parity table (tests/contracts/action-parity.json, T-0287).

The phone side is android-app/tools/action_table_test.py. Not covered: whether a placed pair
behaves alike; the table says which family each action serves, nothing more.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

from aura_desktop.actions.builtins import all_actions

ROOT = Path(__file__).resolve().parents[2]
TABLE = json.loads((ROOT / "tests" / "contracts" / "action-parity.json").read_text(encoding="utf-8"))["families"]
#: A gap still to fill names the task that fills it, so it cannot pass for a decision.
TRACKED = re.compile(r"^todo T-\d{4}: ")


def test_parity_every_desktop_action_is_placed_once():
    placed = [a for fam in TABLE.values() for a in fam["desktop"]]
    assert len(placed) == len(set(placed)), "an action is placed twice"
    names = {a.id for a in all_actions()}
    assert set(placed) == names, (f"not in the table: {sorted(names - set(placed))}; "
                                  f"in the table but gone: {sorted(set(placed) - names)}")


def test_parity_gaps_every_phone_family_has_a_desktop_action_or_a_reason():
    for family, fam in TABLE.items():
        if fam["phone"] and not fam["desktop"]:
            why = fam.get("desktop_gap", "")
            assert why, f"{family}: no desktop action and no reason"
            assert not why.startswith("todo") or TRACKED.match(why), f"{family}: an open gap must name its task"
        elif "desktop_gap" in fam:
            raise AssertionError(f"{family}: has desktop actions but still says why it has none")


def _phone_tiers() -> dict[str, int]:
    spec = importlib.util.spec_from_file_location("action_table", ROOT / "android-app/tools/action_table_test.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return {k: {"AUTO": 1, "NOTIFY": 2, "CONFIRM": 3}[v["tier"]] for k, v in mod.parse_actions().items()}


def test_parity_a_desktop_action_is_no_looser_than_the_phones_same_action():
    # The phone's tier is the reviewed one; a desktop twin asking less is a quiet downgrade.
    phone = _phone_tiers()
    looser = {a.id: (int(a.tier), phone[a.id]) for a in all_actions() if a.id in phone and int(a.tier) < phone[a.id]}
    assert not looser, f"desktop tier below the phone's (desktop, phone): {looser}"
