#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""harvest-allowlist: a live-harvest pull request (scripts/live_harvest.py, loop#309) is data only.

The daily harvest opens its pull request from a `live-harvest/*` branch. Such a pull request may ADD files under
findings/<date>-live-<N>/ and may change baseline/live.json; nothing else, and nothing already in findings/ may be
changed, renamed or deleted. Anything else leaves the pull request for a human (it is red here and is never auto-merged).

Usage: python3 harvest_allowlist.py BASE HEAD
  Compares BASE..HEAD (two refs of the checkout) and prints each refused path. Exit 0 = data only; 1 = refused;
  2 = bad arguments or git failure.
"""
from __future__ import annotations

import re
import subprocess
import sys

FINDINGS = re.compile(r"findings/\d{4}-\d\d-\d\d-live-[1-9][0-9]*/[^\n]+")
BASELINE = "baseline/live.json"


def refused(name_status: bytes) -> list[str]:
    """The paths a `git diff --name-status -z --no-renames` output refuses."""
    parts = name_status.split(b"\0")
    out = []
    for i in range(0, len(parts) - 1, 2):
        status, path = parts[i].decode(), parts[i + 1].decode("utf-8", "replace")
        if status == "A" and FINDINGS.fullmatch(path):
            continue
        if status == "M" and path == BASELINE:
            continue
        out.append(f"{status} {path}")
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[-4], file=sys.stderr)
        return 2
    p = subprocess.run(["git", "diff", "--name-status", "-z", "--no-renames", f"{argv[1]}...{argv[2]}"],
                       capture_output=True)
    if p.returncode != 0:
        print(p.stderr.decode(), file=sys.stderr)
        return 2
    bad = refused(p.stdout)
    for b in bad:
        print(f"harvest-allowlist: {b} is not data the harvest may change", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
