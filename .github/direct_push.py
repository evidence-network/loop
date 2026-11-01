#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""direct-push guard: a push to main that did not come from a merged pull request may touch only
stats/data/.

main takes changes by pull request, except the stats publisher's: loop-data's publish-scrubbed
workflow pushes the scrubbed set, stats/data/, straight to main with its deploy key (the ruleset
lets deploy keys bypass the pull-request rule). The reporter's raw reports go to loop-data, never
here. GitHub cannot limit a deploy key to a path, so this check, run on every push to main,
turns red when a direct push touched anything else. See scripts/apply_branch_protection.sh.

Each commit of the push is checked against its first parent, not only the push's net diff: a
commit that changes nodes/ or findings/ and a later one that restores them must not pass as a
push that touched nothing. A merge commit in a direct push is refused outright: its first-parent
diff can hide what its second parent brings.

Usage: python3 direct_push.py BEFORE AFTER VIA_PR
  VIA_PR: "true" when AFTER is the merge commit of a merged pull request (the workflow asks the
  API), anything else otherwise.
Exit 0 = allowed; 1 = a direct push outside stats/data/; 2 = bad arguments.
"""
from __future__ import annotations

import subprocess
import sys

ALLOWED_PREFIX = "stats/data/"
ZERO = "0" * 40


def git(*args: str) -> list[str]:
    out = subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout
    return [p for p in out.splitlines() if p]


def changed_paths(before: str, after: str) -> list[str]:
    """The paths any commit of the push before..after changes against its first parent (all of after's tree if
    before is zero)."""
    if before == ZERO:
        return git("ls-tree", "-r", "--name-only", after)
    paths: set[str] = set()
    for commit in git("rev-list", f"{before}..{after}"):
        paths.update(git("diff", "--no-renames", "--name-only", f"{commit}^1", commit))
    return sorted(paths)


def merges(before: str, after: str) -> list[str]:
    """The merge commits the push before..after brings."""
    return git("rev-list", "--min-parents=2", after if before == ZERO else f"{before}..{after}")


def outside(paths: list[str]) -> list[str]:
    """The paths a direct push may not touch."""
    return [p for p in paths if not p.startswith(ALLOWED_PREFIX)]


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    before, after, via_pr = argv
    if via_pr == "true":
        print(f"direct-push: {after[:12]} is a merged pull request; nothing to check.")
        return 0
    merged = merges(before, after)
    if merged:
        print(f"direct-push: {before[:12]}..{after[:12]} was pushed to main without a pull request and "
              f"brings {len(merged)} merge commit(s): {', '.join(m[:12] for m in merged)}.")
        print("A direct push never carries a merge. Revert this push by pull request and find out which key "
              "made it.")
        return 1
    bad = outside(changed_paths(before, after))
    if bad:
        print(f"direct-push: {before[:12]}..{after[:12]} was pushed to main without a pull "
              f"request and touches {len(bad)} path(s) outside {ALLOWED_PREFIX}:")
        for p in bad:
            print(" ", p)
        print("Only the stats publisher pushes directly, and only stats/data/. Revert this "
              "push by pull request and find out which key made it.")
        return 1
    print(f"direct-push: {before[:12]}..{after[:12]} touches only {ALLOWED_PREFIX}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
