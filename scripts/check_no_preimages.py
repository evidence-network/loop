#!/usr/bin/env python3
"""Check that no payment preimage is in the published trees.

A capsules settlement record keeps the payment's preimage, bearer proof that the payment
completed. It never leaves the lab host: steps 05 and 09 copy ledgers through
steps/live.py copy_redacted, and lab/lab.sh bundles keeps the signed bundles on the host.
This scans every .json and .jsonl file under each PATH (default: findings baseline lab/runs
reports) for a `preimage` member with a non-empty value, at any depth, and fails on one.

Usage: python3 scripts/check_no_preimages.py [PATH ...]
Exit 0 = none found; 1 = found (each printed as path:line, never the value).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "steps"))
from live import carries_preimage  # noqa: E402

DEFAULT = ("findings", "baseline", "lab/runs", "reports")


def hits(path):
    """[line number] of each record in a .json or .jsonl file that carries a preimage."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".json":
        try:
            return [1] if carries_preimage(json.loads(text)) else []
        except ValueError:
            return []
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        try:
            if carries_preimage(json.loads(line)):
                found.append(n)
        except ValueError:
            continue
    return found


def scan(paths):
    out = []
    for root in paths:
        root = Path(root)
        files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.suffix in (".json", ".jsonl"))
        for f in files:
            if f.is_file() and not f.is_symlink():
                out += [f"{f}:{n}" for n in hits(f)]
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    found = scan(argv or DEFAULT)
    for h in found:
        print(f"{h}: a preimage member (value not shown)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
