"""calculate: arithmetic in Decimal, units from one table of exact factors, dates in a named zone.

A model's own arithmetic is a guess said with confidence ("17% of 340 is 58.3"). This tool
works it out instead, needs no approval (it reads nothing and changes nothing) and is not
``execute_code``, which can call every other tool and so asks the owner each time.

The expression is walked node by node: numbers, + - * / // % **, parentheses and four
functions (sqrt, abs, round, min, max). No names, attributes, strings or other calls exist
here, so there is nothing to escape into.

What it does NOT cover: currency (a rate is live data: web_search it, then calculate the sum),
algebra or calculus, US customary units beyond the table, and dates in words ("next Tuesday":
the model turns those into YYYY-MM-DD first).
"""
from __future__ import annotations

import ast
import datetime
import decimal
import json
import operator
from collections.abc import Callable
from decimal import Decimal
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: A spoken sum is short; this bounds the parse and the walk, not any real question.
MAX_LEN = 300
#: Beyond this an exponent is a mistake or an attack, never a household number.
MAX_EXPONENT = 1000
_CTX = decimal.Context(prec=28, rounding=decimal.ROUND_HALF_EVEN,
                       traps=[decimal.DivisionByZero, decimal.Overflow, decimal.InvalidOperation])

_BINARY: dict[type, Callable[[Decimal, Decimal], Decimal]] = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod}


def _round(x: Decimal, places: Decimal = Decimal(0)) -> Decimal:
    # Half up, as a till rounds: Python's round() would take 2.675 to 2.67.
    return x.quantize(Decimal(1).scaleb(-int(places)), rounding=decimal.ROUND_HALF_UP)


_FUNCS: dict[str, Callable[..., Decimal]] = {"sqrt": lambda x: x.sqrt(), "abs": abs, "round": _round, "min": min, "max": max}


def _literal(node: ast.Constant, text: str) -> Decimal:
    # The digits as typed: Python has already rounded 1.23456789012345678 to a float.
    try:
        return Decimal(ast.get_source_segment(text, node) or str(node.value))
    except decimal.InvalidOperation:  # 0x10, 1_0.5 and the like: the parsed value is exact enough
        return Decimal(str(node.value))


def _walk(node: ast.AST, text: str) -> Decimal:
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        number = _literal(node, text)
        if not number.is_finite():
            raise ValueError("the number is too large")
        return number
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        value = _walk(node.operand, text)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
        base, exponent = _walk(node.left, text), _walk(node.right, text)
        if abs(exponent) > MAX_EXPONENT:
            raise ValueError(f"an exponent above {MAX_EXPONENT} is not a number anyone says")
        return base ** exponent
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        return _BINARY[type(node.op)](_walk(node.left, text), _walk(node.right, text))
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS
            and not node.keywords and node.args):
        return _FUNCS[node.func.id](*(_walk(a, text) for a in node.args))
    if isinstance(node, ast.Tuple):
        raise ValueError("write numbers without thousands commas (1000, not 1,000)")
    raise ValueError(f"only numbers, + - * / // % **, parentheses and {', '.join(_FUNCS)} are worked out here")


def _say(x: Decimal) -> str:
    """Plain digits, no exponent and no trailing zeros: what is read out.

    Past 28 integer digits the value was rounded, so it keeps its exponent
    (1.267650600228229401496703205E+30): plain digits would end in zeros that
    are not the answer.
    """
    if x == 0:
        return "0"
    if x.adjusted() >= _CTX.prec:
        return str(x.normalize())
    return format(x.normalize(), "f")


def arithmetic(expression: str, places: Optional[int] = None) -> str:
    text = expression.strip().replace("×", "*").replace("÷", "/").replace("^", "**")
    if not text:
        raise ValueError("expression is empty")
    if len(text) > MAX_LEN:
        raise ValueError(f"expression is over {MAX_LEN} characters; split it")
    try:
        tree = ast.parse(text, mode="eval")
    except (SyntaxError, RecursionError) as exc:
        raise ValueError(f"not an arithmetic expression: {exc.__class__.__name__}") from None
    with decimal.localcontext(_CTX):
        try:
            value = _walk(tree.body, text)
            if places is not None:
                value = _round(value, Decimal(places))
        except decimal.DivisionByZero:
            raise ValueError("division by zero") from None
        except decimal.Overflow:
            raise ValueError("the result is too large") from None
        except decimal.InvalidOperation:
            raise ValueError("no answer: a root of a negative, 0 to a negative power, "
                             "or more than 28 digits") from None
        return _say(value)


# unit -> (kind, how many of the kind's base unit one of it is). Factors are the exact
# definitions (an inch is 0.0254 m, a pound 0.45359237 kg, a US gallon 3.785411784 L).
_UNITS: dict[str, tuple[str, Decimal]] = {}
for _kind, _rows in {
    "length": {"m": "1", "km": "1000", "cm": "0.01", "mm": "0.001", "in": "0.0254", "ft": "0.3048",
               "yd": "0.9144", "mi": "1609.344", "nmi": "1852"},
    "mass": {"kg": "1", "g": "0.001", "mg": "0.000001", "t": "1000", "lb": "0.45359237",
             "oz": "0.028349523125", "st": "6.35029318"},
    "volume": {"l": "1", "ml": "0.001", "m3": "1000", "gal": "3.785411784", "qt": "0.946352946",
               "pt": "0.473176473", "cup": "0.2365882365", "floz": "0.0295735295625",
               "tbsp": "0.01478676478125", "tsp": "0.00492892159375", "impgal": "4.54609"},
    "speed": {"m/s": "1", "km/h": "0.2777777777777777777777777778", "mph": "0.44704",
              "kn": "0.5144444444444444444444444444"},
    "area": {"m2": "1", "km2": "1000000", "ft2": "0.09290304", "acre": "4046.8564224", "ha": "10000"},
    "time": {"s": "1", "min": "60", "h": "3600", "day": "86400", "week": "604800"},
    "data": {"b": "1", "kb": "1000", "mb": "1000000", "gb": "1000000000", "tb": "1000000000000",
             "kib": "1024", "mib": "1048576", "gib": "1073741824", "tib": "1099511627776"},
    "energy": {"j": "1", "kj": "1000", "kwh": "3600000", "wh": "3600", "cal": "4.184", "kcal": "4184"},
    "pressure": {"pa": "1", "kpa": "1000", "bar": "100000", "psi": "6894.757293168361"},
}.items():
    for _name, _factor in _rows.items():
        _UNITS[_name] = (_kind, Decimal(_factor))

_TEMPERATURE = {"c", "f", "k"}
_ALIASES = {
    "meter": "m", "metre": "m", "kilometer": "km", "kilometre": "km", "centimeter": "cm",
    "centimetre": "cm", "millimeter": "mm", "millimetre": "mm", "inch": "in", "inches": "in",
    "foot": "ft", "feet": "ft", "yard": "yd", "mile": "mi", "kilogram": "kg", "kilo": "kg",
    "gram": "g", "milligram": "mg", "tonne": "t", "pound": "lb", "lbs": "lb", "ounce": "oz",
    "stone": "st", "liter": "l", "litre": "l", "milliliter": "ml", "millilitre": "ml",
    "gallon": "gal", "quart": "qt", "pint": "pt", "fl oz": "floz", "tablespoon": "tbsp",
    "teaspoon": "tsp", "kph": "km/h", "kmh": "km/h", "knot": "kn", "hectare": "ha",
    "second": "s", "sec": "s", "minute": "min", "hour": "h", "hr": "h", "hrs": "h",
    "kms": "km", "byte": "b", "kilojoule": "kj", "joule": "j", "calorie": "cal",
    "celsius": "c", "centigrade": "c", "fahrenheit": "f", "kelvin": "k",
    "°c": "c", "°f": "f", "sq ft": "ft2", "square feet": "ft2", "square meters": "m2",
}


def _unit(name: str) -> str:
    key = " ".join(str(name).lower().split())
    # A plural is stripped only past three letters: "ms" must not become metres.
    for candidate in (key, key.rstrip("s") if len(key) > 3 else key):
        candidate = _ALIASES.get(candidate, candidate)
        if candidate in _UNITS or candidate in _TEMPERATURE:
            return candidate
    raise ValueError(f"unknown unit {name!r}")


def _to_kelvin(value: Decimal, unit: str) -> Decimal:
    if unit == "c":
        return value + Decimal("273.15")
    if unit == "f":
        return (value - 32) * 5 / 9 + Decimal("273.15")
    return value


def _from_kelvin(value: Decimal, unit: str) -> Decimal:
    if unit == "c":
        return value - Decimal("273.15")
    if unit == "f":
        return (value - Decimal("273.15")) * 9 / 5 + 32
    return value


def convert(value: Any, from_unit: str, to_unit: str, places: Optional[int] = None) -> str:
    src, dst = _unit(from_unit), _unit(to_unit)
    with decimal.localcontext(_CTX):
        amount = Decimal(str(value))
        if src in _TEMPERATURE or dst in _TEMPERATURE:
            if not (src in _TEMPERATURE and dst in _TEMPERATURE):
                raise ValueError(f"{from_unit} and {to_unit} measure different things")
            result = _from_kelvin(_to_kelvin(amount, src), dst)
        else:
            (kind_a, factor_a), (kind_b, factor_b) = _UNITS[src], _UNITS[dst]
            if kind_a != kind_b:
                raise ValueError(f"{from_unit} is {kind_a} and {to_unit} is {kind_b}")
            result = amount * factor_a / factor_b
        if places is not None:
            result = _round(result, Decimal(places))
        return _say(result)


def _now(zone: Optional[ZoneInfo]) -> datetime.datetime:
    # The container's TZ is the house's (compose sets it), so no zone means the house's.
    return datetime.datetime.now(zone) if zone else datetime.datetime.now().astimezone()


def _day(text: str, today: datetime.date) -> datetime.date:
    if str(text).strip().lower() == "today":
        return today
    try:
        return datetime.date.fromisoformat(str(text).strip())
    except ValueError:
        raise ValueError(f"{text!r} is not a date: give YYYY-MM-DD or today") from None


def dates(args: dict, now: Optional[datetime.datetime] = None) -> dict:
    zone = None
    if args.get("zone"):
        try:
            zone = ZoneInfo(str(args["zone"]))
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"unknown time zone {args['zone']!r}: give an IANA name like Asia/Tokyo") from None
    moment = (now.astimezone(zone) if zone else now.astimezone()) if now else _now(zone)
    today = moment.date()
    if not (args.get("date") or args.get("until") or args.get("add_days") is not None):
        return {"date": today.isoformat(), "weekday": today.strftime("%A"),
                "time": moment.strftime("%H:%M"), "zone": str(zone or moment.tzname())}
    start = _day(args.get("date") or "today", today)
    if args.get("until"):
        end = _day(args["until"], today)
        return {"days": (end - start).days, "date_weekday": start.strftime("%A"),
                "until_weekday": end.strftime("%A")}
    if args.get("add_days") is not None:
        end = start + datetime.timedelta(days=int(args["add_days"]))
        return {"result": end.isoformat(), "weekday": end.strftime("%A")}
    return {"date": start.isoformat(), "weekday": start.strftime("%A")}


def handle_calculate(args: dict, now: Optional[datetime.datetime] = None, **_: Any) -> str:
    places = args.get("places")
    try:
        places = None if places is None else int(places)
        if args.get("expression") is not None:
            return json.dumps({"result": arithmetic(str(args["expression"]), places)})
        if args.get("value") is not None and args.get("from_unit") and args.get("to_unit"):
            return json.dumps({"result": convert(args["value"], args["from_unit"], args["to_unit"], places)})
        if any(args.get(k) for k in ("date", "until", "zone")) or args.get("add_days") is not None:
            return json.dumps(dates(args, now))
    except (ValueError, TypeError, ArithmeticError) as exc:
        return json.dumps({"error": str(exc)})
    return json.dumps({"error": "give an expression, or value + from_unit + to_unit, or a date / zone"})
