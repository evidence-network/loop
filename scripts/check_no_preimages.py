#!/usr/bin/env python3
"""Check that no payment preimage is in the published trees.

A capsules settlement record keeps the payment's preimage, bearer proof that the payment
completed. It never leaves the lab host: steps 05 and 09 copy ledgers through
steps/live.py copy_redacted, and lab/lab.sh bundles keeps the signed bundles on the host.
This scans each PATH (default: findings baseline lab/runs reports) two ways and fails on a hit:

- every .json and .jsonl file for a `preimage` member with a non-empty value, at any depth;
- every file for a preimage-shaped value: 64 hex digits whose sha256 is itself a 64-hex value
  somewhere in the scanned trees (a preimage beside its payment hash), whatever the member's
  name or the file's format.

Usage: python3 scripts/check_no_preimages.py [PATH ...]
Exit 0 = none found; 1 = found (each printed as path:line, never the value).
"""
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "steps"))
from live import carries_preimage  # noqa: E402

DEFAULT = ("findings", "baseline", "lab/runs", "reports")
HEX64 = re.compile(rb"(?<![0-9A-Fa-f])[0-9A-Fa-f]{64}(?![0-9A-Fa-f])")


def hits(path):
    """[line number] of each record in a .json or .jsonl file that carries a preimage member."""
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


def files(paths):
    for root in map(Path, paths):
        for f in [root] if root.is_file() else sorted(root.rglob("*")):
            if f.is_file() and not f.is_symlink():
                yield f


def scan(paths):
    out, hexes, where = [], set(), []
    for f in files(paths):
        if f.suffix in (".json", ".jsonl"):
            out += [f"{f}:{n}: a preimage member" for n in hits(f)]
        for n, line in enumerate(f.read_bytes().splitlines(), 1):
            for m in HEX64.findall(line):
                h = m.lower()
                hexes.add(h)
                where.append((f, n, h))
    for f, n, h in where:
        if hashlib.sha256(bytes.fromhex(h.decode())).hexdigest().encode() in hexes:
            out.append(f"{f}:{n}: a value whose sha256 is in the trees")
    return list(dict.fromkeys(out))


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    found = scan(argv or DEFAULT)
    for h in found:
        print(f"{h} (value not shown)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
