#!/usr/bin/env python3
"""Check controller binding verdicts: every bindings/<controller_key_id>.json.

Usage: check_binding.py [DIR] [--lock versions.lock]   (default: bindings, versions.lock)

A binding verdict says whether a controller binding (subjects under one
controller key) was verified, and by what. This repository never verifies a
binding itself: a verdict is written by a pinned verifier, and nothing
produces one today (Controller Binding v0 has no released verifier). The
file, profile binding-verdict/0, is exactly:

    {"profile": "binding-verdict/0",
     "controller_key_id": "<64 lowercase hex>",
     "subjects": ["<node_id or subject id>", ...],   sorted, unique, at least 2
     "status": "verified" | "revoked" | "not_checked",
     "verifier": "<name>@<pin>" | null,
     "checked_at": "YYYY-MM-DDTHH:MM:SSZ",
     "cites": "<owner/repo#N>" | null}

It is refused when a member is missing, unknown or malformed; when its file
is not named <controller_key_id>.json; when it is `verified` and `verifier`
is not BINDING_VERIFIER at the pin versions.lock gives it (a line
`<name> <repository> <pin>`); or when it is `not_checked` without the open
issue it cites. Only a `verified` verdict from the pinned verifier groups its
subjects, and only when each subject's commitment carries the verdict's
controller_key_id as its controller_binding digest (history_cards.bound_groups);
`revoked` and `not_checked` never do. A verdict carries no signature of its
verifier, so until a verifier signs its verdicts, one is trusted on the review
of the pull request that adds it.
Exit 0 = clean, 1 = refused.
"""
import json
import re
import sys
from pathlib import Path

PROFILE = "binding-verdict/0"
# The one tool whose verdict is `verified`: the Controller Binding v0 verifier, once versions.lock pins it.
# Any other pinned tool (the reporter, capsulectl) is not a binding verifier.
BINDING_VERIFIER = "binding-verifier"
STATUSES = ("verified", "revoked", "not_checked")
MEMBERS = ("profile", "controller_key_id", "subjects", "status", "verifier", "checked_at", "cites")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
VERIFIER = re.compile(r"^([a-z0-9][a-z0-9-]*)@(\S+)$")
ISSUE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#[1-9][0-9]*$")
SUBJECT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")

# The named refusals.
SHAPE = "binding.malformed"
FILE_NAME = "binding.file_name"
UNPINNED = "binding.verifier_not_pinned"
NO_CITE = "binding.not_checked_cites_nothing"


def read_lock(path):
    """{name: pin} from a versions.lock (lines `<name> <repository> <pin>`, # comments)."""
    pins = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        parts = raw.split("#", 1)[0].split()
        if len(parts) == 3:
            pins[parts[0]] = parts[2]
    return pins


def pinned(verifier, pins):
    """True when verifier is `binding-verifier@<pin>` and the lock pins binding-verifier at exactly pin."""
    m = VERIFIER.match(verifier) if isinstance(verifier, str) else None
    return bool(m) and m.group(1) == BINDING_VERIFIER and pins.get(BINDING_VERIFIER) == m.group(2)


def verdict_errors(doc, pins):
    """Every reason to refuse one parsed verdict."""
    if not isinstance(doc, dict):
        return [f"{SHAPE}: not a JSON object"]
    errs = [f"{SHAPE}: missing {m}" for m in MEMBERS if m not in doc]
    errs += [f"{SHAPE}: unknown field {x}" for x in sorted(set(doc) - set(MEMBERS))]
    if errs:
        return errs
    if doc["profile"] != PROFILE:
        errs.append(f"{SHAPE}: profile must be {PROFILE}")
    if not (isinstance(doc["controller_key_id"], str) and HEX64.fullmatch(doc["controller_key_id"])):
        errs.append(f"{SHAPE}: controller_key_id is not 64 lowercase hex")
    subjects = doc["subjects"]
    if not (isinstance(subjects, list) and all(isinstance(s, str) and SUBJECT.match(s) for s in subjects)):
        errs.append(f"{SHAPE}: subjects must be a list of subject ids")
    elif len(subjects) < 2 or subjects != sorted(set(subjects)):
        errs.append(f"{SHAPE}: subjects must be sorted, unique, and at least 2")
    if doc["status"] not in STATUSES:
        errs.append(f"{SHAPE}: status must be one of {', '.join(STATUSES)}")
    if doc["verifier"] is not None and not (isinstance(doc["verifier"], str) and VERIFIER.match(doc["verifier"])):
        errs.append(f"{SHAPE}: verifier must be <name>@<pin> or null")
    if not (isinstance(doc["checked_at"], str) and TIMESTAMP.fullmatch(doc["checked_at"])):
        errs.append(f"{SHAPE}: checked_at must be YYYY-MM-DDTHH:MM:SSZ")
    if doc["cites"] is not None and not (isinstance(doc["cites"], str) and ISSUE.match(doc["cites"])):
        errs.append(f"{SHAPE}: cites must be <owner/repo#N> or null")
    if doc["status"] == "verified" and not pinned(doc["verifier"], pins):
        errs.append(f"{UNPINNED}: a verified verdict needs {BINDING_VERIFIER} at the pin versions.lock gives it; "
                    f"{doc['verifier'] or 'none'} is not pinned there")
    if doc["status"] == "not_checked" and doc["cites"] is None:
        errs.append(f"{NO_CITE}: a not_checked verdict cites the open issue that owns the verifier")
    return errs


def check_file(path, pins):
    """(the parsed verdict or None, its errors)."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, [f"{SHAPE}: not valid JSON ({e})"]
    errs = verdict_errors(doc, pins)
    if isinstance(doc, dict) and isinstance(doc.get("controller_key_id"), str) and \
            Path(path).name != f"{doc['controller_key_id']}.json":
        errs.append(f"{FILE_NAME}: file name must be <controller_key_id>.json")
    return doc, errs


def main(argv):
    args = argv[1:]
    lock = "versions.lock"
    if "--lock" in args:
        i = args.index("--lock")
        lock = args[i + 1]
        del args[i:i + 2]
    root = Path(args[0] if args else "bindings")
    files = [root] if root.is_file() else sorted(root.glob("*.json")) if root.is_dir() else []
    if not files:
        print(f"{root}: no binding verdicts")
        return 0
    pins = read_lock(lock)
    bad = 0
    for f in files:
        _, errs = check_file(f, pins)
        for e in errs:
            print(f"{f.name}: {e}", file=sys.stderr)
        bad += bool(errs)
    if bad:
        return 1
    print(f"{root}: {len(files)} binding verdict{'' if len(files) == 1 else 's'} ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
