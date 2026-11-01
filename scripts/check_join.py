#!/usr/bin/env python3
"""Check signed joins and leaves: one file, or every *.json in a directory.

Usage: check_join.py [PATH] [--now YYYY-MM-DDTHH:MM:SSZ]   (default: joins)

A join (`"kind": "join/1"`) or a leave (`"kind": "leave/1"`) is one JSON object
signed by the node's plugin key (joins/README.md is the format). This refuses one:

- that is missing a field or has an unknown one, or whose fields have the wrong shape;
- whose spki is not an Ed25519 key, or is not the key key_id names;
- whose signature does not verify under spki, over its RFC 8785 (JCS) canonical
  form without `sig` (the canonicalisation of check_commitment.py);
- whose consent_sha256 is not the sha256 of today's switch statement
  (joins/SWITCH_CONSENT.txt, its bytes); a digest of the nodes/ operator statement
  (check_nodes.CONSENT) or of a known older statement is named;
- whose `at` is more than five minutes in the future;
- that carries personal data, by the rules of check_personal_data.py.

Over a directory it also refuses a leave with no earlier join for its node_id,
and a leave signed by another key than that join.

Signing and verifying use Ed25519 through the `openssl` command (3.0 or later).
Exit 0 = clean, 1 = refused.
"""
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_commitment  # noqa: E402
import check_nodes  # noqa: E402
import check_personal_data  # noqa: E402

KINDS = ("join/1", "leave/1")
FIELDS = {"kind": str, "node_id": str, "key_id": str, "consent_sha256": str, "at": str, "spki": str, "sig": dict}
HEX64 = re.compile(r"^[0-9a-f]{64}$")
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SKEW = dt.timedelta(minutes=5)
# The statement shown under the join switch in mesh-llm setup, kept only in this file.
CONSENT_FILE = Path(__file__).resolve().parent.parent / "joins" / "SWITCH_CONSENT.txt"
CONSENT = CONSENT_FILE.read_bytes().decode("utf-8")


def consent_sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


CONSENT_SHA256 = consent_sha256(CONSENT)
# A nodes/ entry agrees to check_nodes.CONSENT ("remove this entry"); a join does not.
NODES_CONSENT_SHA256 = consent_sha256(check_nodes.CONSENT)
# Statements operators agreed to before today's, word for word, so a stale join says which one it carries.
OLDER_CONSENTS = {
    "the first operator statement (replaced 2026-10-27)": (
        "I run this node. The evidence network's daily reporter, signing with the key in "
        "reporter/reporter.json, may ask it checkpoints and history_card/1 once a day. "
        "What it answers, or that it did not answer, is published in the evidence-network/loop "
        "repository, including after that repository is public. I leave by removing this entry "
        "by pull request: the asks stop from the next run, and reports already published stay."
    ),
    "the nodes/ operator statement before it named the /watch/ pair edges (replaced 2026-10-31)": (
        "I add this node to the evidence network to help improve the evidence layer. "
        "The network may ask it for its history summary, usually daily, and never for the "
        "content of any request, answer or payment. A summary of its answers, or that it did "
        "not answer, is published at https://evidence-network.github.io/loop/stats/. "
        "To leave, remove this entry: the asks stop on the next run, and past reports stay."
    ),
}


def parse_at(text):
    return dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def field_errors(doc):
    """Missing, unknown and malformed fields."""
    if not isinstance(doc, dict):
        return ["not a JSON object"]
    errs = [f"missing {name}" for name in FIELDS if name not in doc]
    errs += [f"unknown field {x}" for x in sorted(set(doc) - set(FIELDS))]
    errs += [f"{name} must be a {t.__name__}" for name, t in FIELDS.items()
             if name in doc and not isinstance(doc[name], t)]
    if errs:
        return errs
    if doc["kind"] not in KINDS:
        errs.append(f"kind must be one of {', '.join(KINDS)}")
    for name in ("node_id", "key_id", "consent_sha256"):
        if not HEX64.fullmatch(doc[name]):
            errs.append(f"{name} is not 64 lowercase hex")
    if not TIMESTAMP.fullmatch(doc["at"]):
        errs.append("at must be YYYY-MM-DDTHH:MM:SSZ (UTC)")
    else:
        try:
            parse_at(doc["at"])
        except ValueError:
            errs.append("at is not a real time")
    return errs


def consent_errors(doc):
    if doc["consent_sha256"] == CONSENT_SHA256:
        return []
    if doc["consent_sha256"] == NODES_CONSENT_SHA256:
        return ["consent_sha256 is the digest of the nodes/ operator statement; "
                "a join agrees to the switch statement in joins/SWITCH_CONSENT.txt"]
    for label, text in OLDER_CONSENTS.items():
        if doc["consent_sha256"] == consent_sha256(text):
            return [f"consent_sha256 is the digest of {label}, not of today's switch statement; join again"]
    return ["consent_sha256 is not the digest of the switch statement in joins/SWITCH_CONSENT.txt"]


def verify(doc, now):
    """Every check on one parsed join or leave. Returns the errors that refuse it."""
    errs = field_errors(doc)
    if errs:
        return errs
    kerrs, der = check_commitment.key_errors(doc)
    errs += kerrs
    if der is not None and not check_commitment.verify_sig(doc, der):
        errs.append("signature does not verify under spki")
    errs += consent_errors(doc)
    if parse_at(doc["at"]) > now + SKEW:
        errs.append(f"at {doc['at']} is in the future")
    return errs


def personal_data_errors(text):
    return [f"line {i}: {kind}" for i, line in enumerate(text.splitlines(), start=1)
            for kind in check_personal_data.find(line)]


def check_file(path, now):
    """(errors, doc): doc is the parsed object when it passed, else None."""
    try:
        text = Path(path).read_text(encoding="utf-8")
        doc = json.loads(text)
    except (OSError, ValueError) as e:
        return [f"not valid JSON ({e})"], None
    errs = verify(doc, now) + personal_data_errors(text)
    return errs, (None if errs else doc)


def order_errors(docs):
    """Over (name, doc) pairs that passed: each leave follows a join of its node_id, under that join's key."""
    joins = {}
    for _, doc in docs:
        if doc["kind"] == "join/1":
            joins.setdefault(doc["node_id"], []).append(doc)
    errs = []
    for name, doc in docs:
        if doc["kind"] != "leave/1":
            continue
        node = joins.get(doc["node_id"], [])
        if not node:
            errs.append(f"{name}: a leave with no join for its node_id")
            continue
        earlier = [j for j in node if parse_at(j["at"]) <= parse_at(doc["at"])]
        if not earlier:
            errs.append(f"{name}: the leave at {doc['at']} is older than its join")
            continue
        join = max(earlier, key=lambda j: parse_at(j["at"]))
        if join["key_id"] != doc["key_id"]:
            errs.append(f"{name}: the leave is signed by another key than its join")
    return errs


def check_path(root, now):
    """(errors, number of files) for one file or every *.json in a directory."""
    files = [root] if root.is_file() else sorted(root.glob("*.json"))
    errs, good = [], []
    for f in files:
        ferrs, doc = check_file(f, now)
        errs += [f"{f.name}: {e}" for e in ferrs]
        if doc is not None:
            good.append((f.name, doc))
    if root.is_dir():
        errs += order_errors(good)
    return errs, len(files)


def main(argv):
    args = argv[1:]
    now = dt.datetime.now(dt.timezone.utc)
    if "--now" in args:
        i = args.index("--now")
        now = parse_at(args[i + 1])
        del args[i:i + 2]
    root = Path(args[0] if args else "joins")
    if not root.exists():
        print(f"{root}: no joins")
        return 0
    errs, count = check_path(root, now)
    for e in errs:
        print(e, file=sys.stderr)
    if errs:
        return 1
    print(f"{root}: {count} join/leave file{'' if count == 1 else 's'} ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
