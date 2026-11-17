"""calculate (jarvis-hud/exact.py): the numbers Jarvis says are worked out, not guessed."""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_DIR.parent))
from _plugin_loader import load_plugin  # noqa: E402

exact = load_plugin("jarvis-hud").exact


def calc(**args):
    return json.loads(exact.handle_calculate(args))


@pytest.mark.parametrize("expression, answer", [
    ("0.1 + 0.2", "0.3"),                 # a float would say 0.30000000000000004
    ("340 * 17 / 100", "57.8"),           # 17% of 340
    ("82.50 * 1.18", "97.35"),            # a tip
    ("2 ** 10", "1024"),
    ("(3 + 4) * 2 - 1", "13"),
    ("-5 + abs(-2)", "-3"),
    ("sqrt(2)", "1.414213562373095048801688724"),
    ("round(10 / 3, 2)", "3.33"),
    ("max(3, 9, 4) - min(3, 9, 4)", "6"),
    ("7 // 2 + 7 % 2", "4"),
    ("12 × 3 ÷ 4", "9"),                  # what a person types
    ("2^8", "256"),                       # ^ is a power here, not xor
    ("1e3 + 1", "1001"),
    ("1.23456789012345678 * 1", "1.23456789012345678"),   # the digits typed, not a float's
    ("2 ** 100", "1.267650600228229401496703205E+30"),    # rounded, and says so
])
def test_arithmetic_is_exact(expression, answer):
    assert calc(expression=expression)["result"] == answer


def test_places_rounds_half_up_like_money():
    assert calc(expression="2.675", places=2)["result"] == "2.68"   # a float rounds this to 2.67
    assert calc(expression="1/3", places=0)["result"] == "0"


@pytest.mark.parametrize("expression", [
    "__import__('os').system('id')",      # a call to anything but the four functions
    "(1).__class__",                      # attribute access
    "x + 1",                              # a name
    "[1, 2]",
    "'a' * 3",
    "9 ** 9 ** 9",                        # a runaway exponent
    "1 / 0",
    "(-8) ** 0.5",
    "1,000 + 1",                          # a tuple to Python: say so, never guess
    "",
    "1 +" + " 1 +" * 400,                 # too long
])
def test_anything_else_is_refused_with_a_reason(expression):
    out = calc(expression=expression)
    assert "result" not in out and out["error"]


@pytest.mark.parametrize("value, src, dst, answer", [
    (72, "F", "C", "22.22"),
    (100, "celsius", "fahrenheit", "212"),
    (0, "C", "K", "273.15"),
    (26.2, "miles", "km", "42.16"),
    (6, "ft", "m", "1.83"),
    (180, "lbs", "kg", "81.65"),
    (1, "gallon", "liters", "3.79"),
    (60, "mph", "km/h", "96.56"),
    (1, "GiB", "MB", "1073.74"),
    (90, "minutes", "hours", "1.5"),
])
def test_units_convert_from_exact_factors(value, src, dst, answer):
    assert calc(value=value, from_unit=src, to_unit=dst, places=2)["result"] == answer


def test_units_of_different_kinds_are_refused():
    out = calc(value=1, from_unit="kg", to_unit="m")
    assert "error" in out and "kg" in out["error"]
    assert "unknown unit" in calc(value=1, from_unit="furlong", to_unit="m")["error"]


def test_days_between_two_dates_and_their_weekdays():
    out = calc(date="2026-10-29", until="2026-12-25")
    assert out["days"] == 57
    assert out["date_weekday"] == "Thursday" and out["until_weekday"] == "Friday"


def test_adding_days_to_a_date():
    out = calc(date="2026-10-29", add_days=100)
    assert out["result"] == "2027-02-06" and out["weekday"] == "Saturday"


def test_a_date_alone_is_its_weekday():
    assert calc(date="2027-03-03")["weekday"] == "Wednesday"


def test_today_and_the_time_elsewhere_use_the_named_zone():
    fixed = datetime.datetime(2026, 10, 29, 23, 30, tzinfo=datetime.timezone.utc)
    out = json.loads(exact.handle_calculate({"zone": "Asia/Tokyo"}, now=fixed))
    assert out["time"] == "08:30" and out["date"] == "2026-10-30" and out["weekday"] == "Friday"
    out = json.loads(exact.handle_calculate({"date": "today", "until": "2026-12-25", "zone": "Asia/Tokyo"}, now=fixed))
    assert out["days"] == 56
    assert exact._now(None).tzinfo is not None
    assert "unknown time zone" in calc(zone="Mars/Olympus")["error"]
    assert ZoneInfo("Asia/Tokyo")  # the image's tz database answers the same names


def test_nothing_to_work_out_says_what_it_takes():
    assert "expression" in calc()["error"]
    assert "YYYY-MM-DD" in calc(date="next tuesday")["error"]
