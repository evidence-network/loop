#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""DCO check: every commit in a range carries a Developer Certificate of Origin sign-off.

A commit is signed off when its message has a `Signed-off-by: Name <email>` line whose email is
the commit author's (compared without case), which is what `git commit -s` writes. Merge
commits are skipped: they carry no new authorship of their own. See CONTRIBUTING.md for what
the sign-off certifies.

Usage: python3 dco.py BASE HEAD     (checks the commits in BASE..HEAD)
Exit 0 = every commit signed off; 1 = at least one is not; 2 = bad range.
"""
from __future__ import annotations

import re
import subprocess
import sys

SIGNED_OFF = re.compile(r"^Signed-off-by:\s*(?P<name>.+?)\s*<(?P<email>[^<>\s]+)>\s*$", re.M)


def commits(base: str, head: str) -> list[dict]:
    """The commits in base..head: sha, author email, parent count, message."""
    out = subprocess.run(
        ["git", "log", "--format=%H%x00%ae%x00%P%x00%B%x1e", f"{base}..{head}"],
        capture_output=True, text=True, check=True,
    ).stdout
    found = []
    for rec in out.split("\x1e"):
        rec = rec.strip("\n")
        if not rec:
            continue
        sha, email, parents, body = rec.split("\x00", 3)
        found.append({"sha": sha, "email": email, "parents": len(parents.split()), "body": body})
    return found


def problem(commit: dict) -> str | None:
    """Why `commit` fails the DCO check, or None when it passes."""
    if commit["parents"] > 1:
        return None
    emails = [m.group("email").lower() for m in SIGNED_OFF.finditer(commit["body"])]
    if not emails:
        return "no Signed-off-by line"
    if commit["email"].lower() not in emails:
        return f"no Signed-off-by for the author's email <{commit['email']}>"
    return None


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        found = commits(*argv)
    except subprocess.CalledProcessError as exc:
        print(f"dco: cannot read {argv[0]}..{argv[1]}: {exc.stderr.strip()}", file=sys.stderr)
        return 2
    bad = [(c["sha"], why) for c in found if (why := problem(c))]
    for sha, why in bad:
        print(f"dco: {sha[:12]}: {why}")
    if bad:
        print("Sign off each commit with `git commit -s` (amend: `git commit --amend -s`; "
              "a series: `git rebase --signoff <base>`), then force-push the branch.")
        return 1
    print(f"dco: {len(found)} commit(s), all signed off.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
