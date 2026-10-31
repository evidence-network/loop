#!/usr/bin/env python3
"""Check that no payment preimage is in the published trees.

A capsules settlement record keeps the payment's preimage, bearer proof that the payment
completed. It never leaves the lab host: steps 05 and 09 copy ledgers through
steps/live.py copy_redacted, lab/lab.sh bundles keeps the signed bundles on the host, and
lab/pack.py refuses a tarball that would carry one. This scans every file under each PATH
(default: the whole checkout but tests/ and .git), failing closed, for:

- a line where a key naming a preimage (preimage, payment_preimage, preImage...) is followed
  within a few characters by 64 hex digits, in any file format;
- a JSON record holding a preimage value under such a key, at any depth, also inside a string
  that is itself JSON;
- 64 hex digits whose sha256 is itself a 64-hex value somewhere in the scanned trees (a preimage
  beside its payment hash), whatever the member's name or the file's format.

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

SKIP = {".git", "tests", "node_modules", "__pycache__"}
NAMED = re.compile(rb"(?i)pre_?image(?!_check)[^0-9a-fA-F\n]{0,24}[0-9a-fA-F]{64}(?![0-9a-fA-F])")
HEX64 = re.compile(rb"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])")


def hexes(data):
    """Every 64-hex value in data, lowercased: what a preimage's sha256 is looked up in."""
    return {h.lower().decode() for h in HEX64.findall(data)}


def files(paths):
    for root in paths:
        root = Path(root)
        if root.is_file():
            yield root
            continue
        for p in sorted(root.rglob("*")):
            if p.is_file() and not p.is_symlink() and not SKIP.intersection(p.relative_to(root).parts):
                yield p


def hits_in(data, hashes=frozenset()):
    """[line number] of each line of data (bytes) that holds a preimage by any of the three rules."""
    found = []
    for n, line in enumerate(data.split(b"\n"), 1):
        hit = bool(NAMED.search(line))
        if not hit and b"preimage" in line.lower():
            try:
                hit = carries_preimage(json.loads(line))
            except ValueError:
                pass
        if not hit and hashes:
            hit = any(hashlib.sha256(bytes.fromhex(h.decode())).hexdigest() in hashes for h in HEX64.findall(line))
            # (a 64-hex value whose sha256 is in the trees: the payment hash it opens)
        if hit:
            found.append(n)
    return found


def hits(path, hashes=frozenset()):
    return hits_in(Path(path).read_bytes(), hashes)


def scan(paths):
    paths = list(paths)
    found = list(files(paths))
    hashes = frozenset(h for f in found for h in hexes(f.read_bytes()))
    return [f"{f}:{n}" for f in found for n in hits(f, hashes)]


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    found = scan(argv or [ROOT])
    for h in found:
        print(f"{h}: a payment preimage (value not shown)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
