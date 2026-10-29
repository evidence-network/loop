"""The mini Terms Pack (terms-pack.json, or the operator's --terms FILE), evaluated over one offer.

A pack is a list of rules. Each rule names one field on the offer, an operator,
a bound and the bound's unit: {"id", "field", "op", "bound", "unit"}. The
offer states each field as {"value": "<decimal>", "unit": "<unit>"}. A rule
applies when the offer's unit for its field is the rule's unit. evaluate()
gives NO when any rule that applies is unmet, or when a field the pack bounds
is missing from the offer, is not a plain decimal, or is in a unit the pack
has no bound for; otherwise GO. Numbers are compared as decimals, never as
floats. Nothing else is read: no wording, no history.
"""
import hashlib
import json
import operator
import re
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT = HERE / "terms-pack.json"
OUTCOMES = ("GO", "NO")
OPS = {"<=": operator.le, "<": operator.lt, ">=": operator.ge, ">": operator.gt}
DECIMAL = re.compile(r"^[0-9]+(\.[0-9]+)?$")


def load(path=DEFAULT):
    """(pack, sha256 of the pack's bytes); ValueError when a rule is not well formed."""
    data = Path(path).read_bytes()
    pack = json.loads(data)
    rules = pack.get("rules") if isinstance(pack, dict) else None
    if not (isinstance(pack, dict) and pack.get("pack") and pack.get("version") and isinstance(rules, list) and rules):
        raise ValueError(f"{path}: a terms pack is {{pack, version, rules: [...]}} with at least one rule")
    ids = set()
    for r in rules:
        if not (isinstance(r, dict) and all(isinstance(r.get(k), str) and r[k] for k in ("id", "field", "op", "bound", "unit"))
                and r["op"] in OPS and DECIMAL.match(r["bound"]) and r["id"] not in ids):
            raise ValueError(f"{path}: rule {r!r} needs a unique id, a field, an op ({', '.join(OPS)}), a decimal "
                             f"bound as a string and a unit")
        ids.add(r["id"])
    return pack, hashlib.sha256(data).hexdigest()


def stated(offer, field):
    """(Decimal value, unit) of a field on the offer, or (None, why)."""
    v = offer.get(field) if isinstance(offer, dict) else None
    if not (isinstance(v, dict) and isinstance(v.get("value"), str) and isinstance(v.get("unit"), str)):
        return None, f"the offer states no {field} as {{value, unit}}"
    if not DECIMAL.match(v["value"]):
        return None, f"{field} {v['value']!r} is not a plain decimal"
    return Decimal(v["value"]), v["unit"]


def evaluate(pack, offer):
    """{outcome, rules}: one row per rule (met, unmet or not_applicable), and an unmet row per field the pack
    bounds but cannot read on the offer."""
    rows = []
    for field in dict.fromkeys(r["field"] for r in pack["rules"]):
        value, unit = stated(offer, field)
        mine = [r for r in pack["rules"] if r["field"] == field]
        if value is None:
            rows.append({"id": f"{field}-stated", "field": field, "verdict": "unmet", "detail": unit})
            continue
        if not any(r["unit"] == unit for r in mine):
            rows.append({"id": f"{field}-unit", "field": field, "verdict": "unmet",
                         "detail": f"the pack has no bound for {field} in {unit}"})
        for r in mine:
            row = {k: r[k] for k in ("id", "field", "op", "bound", "unit")}
            if r["unit"] != unit:
                row.update(verdict="not_applicable", detail=f"the offer states {field} in {unit}")
            else:
                ok = OPS[r["op"]](value, Decimal(r["bound"]))
                row.update(value=str(value), verdict="met" if ok else "unmet",
                           detail=f"{value} {unit} {r['op'] if ok else 'not ' + r['op']} {r['bound']} {r['unit']}")
            rows.append(row)
    outcome = "NO" if any(r["verdict"] == "unmet" for r in rows) else "GO"
    return {"outcome": outcome, "rules": rows}
