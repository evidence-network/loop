#!/usr/bin/env python3
"""Check the commitment list: every nodes/<node_id>.json entry.

Usage: check_nodes.py [DIR]   (default: nodes)

Refuses an entry whose node_id or key_id is not 64 lowercase hex, whose file name
is not <node_id>.json, whose node_id or key_id repeats another entry's, that is
missing a field, whose consent is not the published operator statement (CONSENT),
or whose operator is not a GitHub handle (an email address or a phone number is
refused: the list holds no personal data). Also checks the leave marks in
<DIR>/left/<node_id>.json and the twins-only lab nodes in <DIR>/twins/<node_id>.json
(same shape, not asked by the reporter). Standard library only. Exit 0 = clean, 1 = refused.
"""
import datetime as dt
import json
import re
import sys
from pathlib import Path

HEX64 = re.compile(r"^[0-9a-f]{64}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REQUIRED = ("node_id", "key_id", "operator", "consent", "mesh", "ask_route", "disclosure", "host_build",
            "plugin_version")
# The operator statement every entry carries, word for word. Changing it is a
# change to what operators agreed to: every listed entry must carry the new text.
CONSENT = (
    "I add this node to the evidence network to help improve the evidence layer. "
    "The network may ask it for its history summary, usually daily, and never for the "
    "content of any request, answer or payment. A summary of its answers, or that it did "
    "not answer, is published at https://evidence-network.github.io/loop/stats/. "
    "The page https://evidence-network.github.io/loop/watch/ shows it day by day, and on a day a finding "
    "shows it dealt with another listed node it draws a line between the two, but only when both entries set "
    "disclosure.pair_edges to \"public\". The page's counts include its dealings even when no line is drawn, "
    "so with few nodes listed they can show who dealt with whom. "
    "To leave, remove this entry: the asks stop on the next run, and past reports stay."
)
# Whether CONSENT names the /watch/ page's public pair edges (who dealt with whom). It does since Steven's ruling on
# evidence-network/loop#77, so an entry may set disclosure.pair_edges to "public". This is a decision made with the
# consent text, never a string match: a revision that drops the pair edges turns it off again.
CONSENT_NAMES_PAIR_EDGES = True
# A GitHub handle: alphanumerics and single inner hyphens, at most 39 characters.
GITHUB_HANDLE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
PHONE = re.compile(r"^\+?[\d\s().-]{7,}$")
ASK_ROUTES = ("mesh",)
POLICIES = ("counterparties",)
STRANGER_SAFE = ("checkpoints", "history_card/1")


def check_operator(operator):
    """The contact: a GitHub handle, never an email address or a phone number."""
    if not (isinstance(operator, str) and operator.strip()):
        return ["operator must be a non-empty string"]
    if EMAIL.search(operator):
        return ["operator looks like an email address; give a GitHub handle (no personal data)"]
    if PHONE.fullmatch(operator) and sum(c.isdigit() for c in operator) >= 7:
        return ["operator looks like a phone number; give a GitHub handle (no personal data)"]
    if operator.startswith("@"):
        return ["operator is a GitHub handle without the leading @"]
    if not GITHUB_HANDLE.fullmatch(operator):
        return ["operator is not a GitHub handle"]
    return []


def check_entry(path, entry):
    errs = []
    if not isinstance(entry, dict):
        return [f"{path.name}: not a JSON object"]
    for field in REQUIRED:
        if field not in entry:
            errs.append(f"{path.name}: missing {field}")
    extra = sorted(set(entry) - set(REQUIRED))
    if extra:
        errs.append(f"{path.name}: unknown field(s) {', '.join(extra)}")
    node_id = entry.get("node_id")
    key_id = entry.get("key_id")
    if "node_id" in entry and not (isinstance(node_id, str) and HEX64.fullmatch(node_id)):
        errs.append(f"{path.name}: node_id is not 64 lowercase hex")
    if "key_id" in entry and not (isinstance(key_id, str) and HEX64.fullmatch(key_id)):
        errs.append(f"{path.name}: key_id is not 64 lowercase hex")
    if isinstance(node_id, str) and path.name != f"{node_id}.json":
        errs.append(f"{path.name}: file name must be <node_id>.json")
    if "operator" in entry:
        errs += [f"{path.name}: {e}" for e in check_operator(entry["operator"])]
    if "consent" in entry and entry["consent"] != CONSENT:
        errs.append(f"{path.name}: consent must be the operator statement in nodes/README.md, word for word")
    for field in ("mesh", "host_build", "plugin_version"):
        if field in entry and not (isinstance(entry[field], str) and entry[field].strip()):
            errs.append(f"{path.name}: {field} must be a non-empty string")
    if "ask_route" in entry and entry["ask_route"] not in ASK_ROUTES:
        errs.append(f"{path.name}: ask_route must be one of {', '.join(ASK_ROUTES)}")
    disc = entry.get("disclosure")
    if "disclosure" in entry:
        if not isinstance(disc, dict) or not {"policy", "stranger_safe"} <= set(disc) <= {
                "policy", "stranger_safe", "pair_edges"}:
            errs.append(f"{path.name}: disclosure must have policy and stranger_safe, and may have pair_edges")
        else:
            if disc["policy"] not in POLICIES:
                errs.append(f"{path.name}: disclosure.policy must be one of {', '.join(POLICIES)}")
            if not isinstance(disc["stranger_safe"], list) or sorted(disc["stranger_safe"]) != sorted(STRANGER_SAFE):
                errs.append(f"{path.name}: disclosure.stranger_safe must be {list(STRANGER_SAFE)}")
            # Opting in to public pair edges on /watch/ (who dealt with whom, evidence-network/loop#77) needs a
            # consent that names them (Sherlock2, Buzz d15c87ad); CONSENT does, since Steven's ruling.
            if "pair_edges" in disc and (disc["pair_edges"] != "public" or not CONSENT_NAMES_PAIR_EDGES):
                errs.append(f"{path.name}: disclosure.pair_edges must be \"public\", and only while the consent "
                            "statement names the /watch/ page's pair edges")
    return errs


def check_dir(root):
    errs = []
    seen_node, seen_key = {}, {}
    for path in sorted(root.glob("*.json")):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            errs.append(f"{path.name}: not valid JSON ({e})")
            continue
        errs += check_entry(path, entry)
        if not isinstance(entry, dict):
            continue
        for field, seen in (("node_id", seen_node), ("key_id", seen_key)):
            value = entry.get(field)
            if isinstance(value, str):
                value = value.lower()
                if value in seen:
                    errs.append(f"{path.name}: duplicate {field}, already in {seen[value]}")
                else:
                    seen[value] = path.name
    errs += check_left(root / "left", seen_node)
    errs += check_twins(root / "twins", seen_node, seen_key)
    return errs, len(seen_node)


def check_twins(twins, listed_node, listed_key):
    """twins/<node_id>.json: a lab node the step 09 twins run adds (lab/pack.py entries). Same shape as a listing, but
    not a listed node: the daily reporter does not ask it, and a node listed under nodes/ is not repeated here."""
    errs = []
    seen_node, seen_key = {}, {}
    for path in sorted(twins.glob("*.json")) if twins.is_dir() else []:
        where = f"twins/{path.name}"
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            errs.append(f"{where}: not valid JSON ({e})")
            continue
        errs += [f"twins/{e}" for e in check_entry(path, entry)]
        if not isinstance(entry, dict):
            continue
        for field, seen, listed in (("node_id", seen_node, listed_node), ("key_id", seen_key, listed_key)):
            value = entry.get(field)
            if isinstance(value, str):
                value = value.lower()
                if value in listed or value in seen:
                    errs.append(f"{where}: {field} repeats {listed.get(value) or seen[value]}")
                seen[value] = path.name
    return errs


def check_left(left, listed):
    """Leave marks: left/<node_id>.json = {"node_id", "left": "<YYYY-MM-DD>"}.

    A node that left is not also listed; to rejoin, add the entry back and
    remove its mark in the same pull request.
    """
    errs = []
    for path in sorted(left.glob("*")) if left.is_dir() else []:
        where = f"left/{path.name}"
        if path.name == ".gitkeep":
            continue
        try:
            mark = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            errs.append(f"{where}: not valid JSON ({e})")
            continue
        if not (isinstance(mark, dict) and set(mark) == {"node_id", "left"}):
            errs.append(f"{where}: a leave mark has exactly node_id and left")
            continue
        node_id, day = mark["node_id"], mark["left"]
        if not (isinstance(node_id, str) and HEX64.fullmatch(node_id)) or path.name != f"{node_id}.json":
            errs.append(f"{where}: file name must be <node_id>.json, node_id 64 lowercase hex")
        if not (isinstance(day, str) and DATE.fullmatch(day) and _is_date(day)):
            errs.append(f"{where}: left must be a YYYY-MM-DD date")
        if node_id in listed:
            errs.append(f"{where}: {node_id} is listed and marked left; remove the mark to rejoin")
    return errs


def _is_date(day):
    try:
        dt.date.fromisoformat(day)
        return True
    except ValueError:
        return False


def main(argv):
    root = Path(argv[1] if len(argv) > 1 else "nodes")
    if not root.is_dir():
        print(f"no such directory: {root}", file=sys.stderr)
        return 1
    errs, count = check_dir(root)
    for e in errs:
        print(e, file=sys.stderr)
    if errs:
        return 1
    print(f"{root}: {count} entr{'y' if count == 1 else 'ies'} ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
