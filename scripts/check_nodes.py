#!/usr/bin/env python3
"""Check the commitment list: every nodes/<node_id>.json entry.

Usage: check_nodes.py [DIR]   (default: nodes)

Refuses an entry whose node_id or key_id is not 64 lowercase hex, whose file name
is not <node_id>.json, whose node_id or key_id repeats another entry's, or that is
missing a field. Standard library only. Exit 0 = clean, 1 = refused.
"""
import json
import re
import sys
from pathlib import Path

HEX64 = re.compile(r"^[0-9a-f]{64}$")
REQUIRED = ("node_id", "key_id", "operator", "mesh", "ask_route", "disclosure", "host_build", "plugin_version")
ASK_ROUTES = ("mesh",)
POLICIES = ("counterparties",)
STRANGER_SAFE = ("checkpoints", "history_card/1")


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
    if "node_id" in entry and not (isinstance(node_id, str) and HEX64.match(node_id)):
        errs.append(f"{path.name}: node_id is not 64 lowercase hex")
    if "key_id" in entry and not (isinstance(key_id, str) and HEX64.match(key_id)):
        errs.append(f"{path.name}: key_id is not 64 lowercase hex")
    if isinstance(node_id, str) and path.name != f"{node_id}.json":
        errs.append(f"{path.name}: file name must be <node_id>.json")
    for field in ("operator", "mesh", "host_build", "plugin_version"):
        if field in entry and not (isinstance(entry[field], str) and entry[field].strip()):
            errs.append(f"{path.name}: {field} must be a non-empty string")
    if "ask_route" in entry and entry["ask_route"] not in ASK_ROUTES:
        errs.append(f"{path.name}: ask_route must be one of {', '.join(ASK_ROUTES)}")
    disc = entry.get("disclosure")
    if "disclosure" in entry:
        if not isinstance(disc, dict) or set(disc) != {"policy", "stranger_safe"}:
            errs.append(f"{path.name}: disclosure must have exactly policy and stranger_safe")
        else:
            if disc["policy"] not in POLICIES:
                errs.append(f"{path.name}: disclosure.policy must be one of {', '.join(POLICIES)}")
            if not isinstance(disc["stranger_safe"], list) or sorted(disc["stranger_safe"]) != sorted(STRANGER_SAFE):
                errs.append(f"{path.name}: disclosure.stranger_safe must be {list(STRANGER_SAFE)}")
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
    return errs, len(seen_node)


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
