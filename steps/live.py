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
import shutil
import subprocess
import sys
from pathlib import Path

ISSUE = "evidence-network/loop: the live lab run, which fills population/live"
# A gap that is still open and that no open issue names yet: it is listed with
# this until one is filed, never with an issue that has closed.
NO_ISSUE_YET = "no open issue yet (to be filed)"
ORDER = {"PASS": 0, "NOT_CHECKED": 1, "FAIL": 2}


# A settlement record the plugin seals keeps the payment's preimage (capsules-plugin lightning-v0, "preimage"). It is
# bearer proof of a payment and never leaves the lab host. A ledger copied into a proof keeps every record, with only
# the preimage's value replaced by REDACTED: the evaluator step 10 runs on 05's chain still sees each settlement
# (phase, source, payment_hash, amounts), and line numbers (held-<n>) stay. Such a line no longer hashes to its
# capsule_id; no step verifies an observation's bytes (05 checks citing records, own halves and inclusions).
# A line that does not parse but names a preimage (a ledger copied while its node appends to it) is a stub instead.
REDACTED = "redacted: a payment preimage never leaves the lab host"
UNPARSED = "an unparsable ledger line that names a preimage, withheld"
REDACTION = "x-loop-redacted"  # the member a redacted record gains: the sha256 of its line as the node holds it
# Key names that carry a preimage's value; preimage_check (06's outcome) is the only other key that names one.
NOT_A_PREIMAGE = ("preimage_check",)


def preimage_key(key):
    return isinstance(key, str) and "preimage" in key.lower() and key not in NOT_A_PREIMAGE


def carries_preimage(value):
    """Whether a parsed record holds a non-empty preimage value under any key naming one (preimage, payment_preimage,
    preImage...) at any depth, other than the REDACTED marker; a string that is itself JSON is looked into."""
    if isinstance(value, dict):
        return any((preimage_key(k) and v not in (None, "", REDACTED)) or carries_preimage(v) for k, v in value.items())
    if isinstance(value, list):
        return any(carries_preimage(v) for v in value)
    if isinstance(value, str) and value[:1] in "{[" and "preimage" in value.lower():
        try:
            return carries_preimage(json.loads(value))
        except ValueError:
            return True  # names a preimage and does not parse: treated as one
    return False


def redacted(value):
    """value with every preimage value (as carries_preimage finds them) replaced by REDACTED."""
    if isinstance(value, dict):
        return {k: (REDACTED if preimage_key(k) and v not in (None, "") else redacted(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redacted(v) for v in value]
    if isinstance(value, str) and value[:1] in "{[" and "preimage" in value.lower():
        try:
            return json.dumps(redacted(json.loads(value)), separators=(",", ":"), sort_keys=True)
        except ValueError:
            return UNPARSED
    return value


def copy_redacted(src, dest):
    """Copy the JSONL ledger file src to dest line for line, each preimage value replaced by REDACTED (the rest of a
    record kept as it parsed, re-encoded only when it held one, plus an x-loop-redacted member naming the sha256 of
    the line as the node holds it); an unparsable line that names a preimage becomes a
    stub with the sha256 of its bytes. Returns how many lines changed."""
    out, n = [], 0
    for raw in Path(src).read_bytes().split(b"\n"):
        try:
            record = json.loads(raw) if raw.strip() else None
        except ValueError:
            record = None
            if b"preimage" in raw.lower():
                n += 1
                raw = json.dumps({"redacted": UNPARSED, "line_sha256": hashlib.sha256(raw).hexdigest()}).encode()
        if record is not None and carries_preimage(record):
            n += 1
            kept = redacted(record)
            if isinstance(kept, dict):  # the line as the node holds it, for whoever holds that line to confirm it
                kept[REDACTION] = {"line_sha256": hashlib.sha256(raw).hexdigest()}
            raw = json.dumps(kept, separators=(",", ":")).encode()
        out.append(raw)
    Path(dest).write_bytes(b"\n".join(out))
    return n


def path(node, field):
    """The node's `field` as a path, with environment variables expanded; None when unset."""
    value = node.get(field)
    return Path(os.path.expandvars(value)) if isinstance(value, str) and value else None


CHAIN_FILE = "capsules.jsonl"  # the node's own chain, as the loop names every copy of it
CHAIN_STORE = "shelf.db"  # a node on the EvidenceBook shelf keeps its chain here; capsules.jsonl is archived (loop#296)
CAPSULES_BIN = "LOOP_CAPSULES_BIN"  # the plugin's `capsules` binary, when it is not on PATH


def chain_present(ledger_dir):
    """Whether the ledger dir holds a readable chain: the shelf store, or the flat capsules.jsonl of a node not yet
    migrated (capsules-plugin#208)."""
    d = Path(ledger_dir)
    return any(os.access(d / f, os.R_OK) and (d / f).is_file() for f in (CHAIN_STORE, CHAIN_FILE))


def chain_bytes(ledger_dir):
    """The node's chain, today's capsules.jsonl bytes (padding included, one line per leaf), from its ledger dir
    (<data dir>/ledger): `capsules chain-lines --data-dir <data dir>` when the node is on the shelf (read-only, it
    beside a running node), else the flat file. Raises OSError when it cannot be read."""
    d = Path(ledger_dir)
    if not (d / CHAIN_STORE).is_file():
        return (d / CHAIN_FILE).read_bytes()
    bin_ = os.environ.get(CAPSULES_BIN) or shutil.which("capsules")
    if not bin_:
        raise FileNotFoundError(f"{d / CHAIN_STORE} holds the chain but no capsules binary is installed "
                                f"(PATH, or {CAPSULES_BIN})")
    try:
        run = subprocess.run([bin_, "chain-lines", "--data-dir", str(d.parent)], stdin=subprocess.DEVNULL,
                             capture_output=True, timeout=120)
    except subprocess.SubprocessError as e:
        raise OSError(f"capsules chain-lines: {type(e).__name__}: {e}") from e
    if run.returncode != 0:
        raise OSError(f"capsules chain-lines exited {run.returncode}: {run.stderr.decode('utf-8', 'replace')[:300]}")
    return run.stdout


def readable(node, field, *files):
    """The node's `field` dir when it and every file in it named here are readable, else None. The chain
    (ledger/capsules.jsonl) is readable when chain_present says so."""
    d = path(node, field)
    if d is None or not d.is_dir() or not os.access(d, os.R_OK | os.X_OK):
        return None
    return d if all(_readable_file(d, f) for f in files) else None


def _readable_file(d, f):
    if f == f"ledger/{CHAIN_FILE}":
        return chain_present(d / "ledger")
    return os.access(d / f, os.R_OK)


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
            if not _readable_file(d, f):
                return f"{field} has no readable {f}"
    return f"{field} not set or unreadable"


def not_checked(name, field="data_dir", why=None):
    return {"what": f"live node {name}", "why": why or f"{field} not set or unreadable", "issue": ISSUE}


def worst(results):
    """The worst result over the nodes: FAIL, then NOT_CHECKED, then PASS."""
    return max(results, key=ORDER.__getitem__)
