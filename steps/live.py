"""A live node's own inputs, as population/live/population.json names them.

Each live node carries `data_dir` (its capsules plugin data dir: `keys/node-key.pem`,
`keys/node-key.pub.pem`) and `book_dir` (the `--home` join.sh made its book in).
Both are null until lab/lab.sh fills them, and may start with `$LAB_DIR`, expanded
from the environment here. A step checks a live node only when the paths it needs
are set and readable by the running user; otherwise that node is NOT_CHECKED with
`not_checked(name, field)`, never a crash and never a FAIL. `why_not(node, field, ...)`
says which: a `$VAR` the step's environment does not set (expandvars leaves it in the
path), a named file that is not readable, or the field unset or unreadable.
"""
import hashlib
import json
import os
import re
import sys
from pathlib import Path

ISSUE = "evidence-network/loop: the live lab run, which fills population/live"
# A gap that is still open and that no open issue names yet: it is listed with
# this until one is filed, never with an issue that has closed.
NO_ISSUE_YET = "no open issue yet (to be filed)"
ORDER = {"PASS": 0, "NOT_CHECKED": 1, "FAIL": 2}


# A settlement record the plugin seals keeps the payment's preimage (capsules-plugin lightning-v0, "preimage"). It is
# bearer proof of a payment and never leaves the lab host: a ledger copied into a proof keeps each such record only
# as a stub at its own line, so line numbers (held-<n>) and every other record stay as the node holds them.
REDACTED = "a record carrying a payment preimage, which never leaves the lab host"


def carries_preimage(value):
    """Whether a parsed record holds a non-empty `preimage` member at any depth."""
    if isinstance(value, dict):
        return any((k == "preimage" and v not in (None, "")) or carries_preimage(v) for k, v in value.items())
    if isinstance(value, list):
        return any(carries_preimage(v) for v in value)
    return False


def copy_redacted(src, dest):
    """Copy the JSONL ledger file src to dest, each record that carries a preimage replaced by a stub naming its
    capsule_id and the sha256 of its line. Returns how many were replaced. A line that does not parse is copied as is."""
    out, n = [], 0
    for raw in Path(src).read_bytes().splitlines(keepends=True):
        try:
            record = json.loads(raw)
        except ValueError:
            out.append(raw)
            continue
        if carries_preimage(record):
            n += 1
            cid = record.get("capsule_id") if isinstance(record, dict) else None
            stub = {"redacted": REDACTED, "capsule_id": cid, "line_sha256": hashlib.sha256(raw.rstrip(b"\n")).hexdigest()}
            out.append(json.dumps(stub, sort_keys=True).encode() + (b"\n" if raw.endswith(b"\n") else b""))
        else:
            out.append(raw)
    Path(dest).write_bytes(b"".join(out))
    return n


def path(node, field):
    """The node's `field` as a path, with environment variables expanded; None when unset."""
    value = node.get(field)
    return Path(os.path.expandvars(value)) if isinstance(value, str) and value else None


def readable(node, field, *files):
    """The node's `field` dir when it and every file in it named here are readable, else None."""
    d = path(node, field)
    if d is None or not d.is_dir() or not os.access(d, os.R_OK | os.X_OK):
        return None
    return d if all(os.access(d / f, os.R_OK) for f in files) else None


def unexpanded(node, field):
    """The variables the node's `field` still names after expansion: ones the environment does not set."""
    p = path(node, field)
    return sorted(set(re.findall(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)", str(p)))) if p else []


def why_not(node, field, *files):
    """Why `readable(node, field, *files)` is None, as a not_checked reason. Says it on stderr too
    when the path names a variable the environment does not set: that is the run's setup, not the node."""
    missing = unexpanded(node, field)
    if missing:
        why = (f"{field} names {', '.join('$' + v for v in missing)}, which this step's environment does not set "
               f"(export it, or run the loop through lab/lab.sh loop)")
        print(f"live: {why}", file=sys.stderr)
        return why
    d = path(node, field)
    if d is not None and d.is_dir() and os.access(d, os.R_OK | os.X_OK):
        for f in files:
            if not os.access(d / f, os.R_OK):
                return f"{field} has no readable {f}"
    return f"{field} not set or unreadable"


def not_checked(name, field="data_dir", why=None):
    return {"what": f"live node {name}", "why": why or f"{field} not set or unreadable", "issue": ISSUE}


def worst(results):
    """The worst result over the nodes: FAIL, then NOT_CHECKED, then PASS."""
    return max(results, key=ORDER.__getitem__)
