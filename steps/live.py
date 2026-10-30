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
import os
import re
import sys
from pathlib import Path

ISSUE = "evidence-network/loop: the live lab run, which fills population/live"
# A gap that is still open and that no open issue names yet: it is listed with
# this until one is filed, never with an issue that has closed.
NO_ISSUE_YET = "no open issue yet (to be filed)"
ORDER = {"PASS": 0, "NOT_CHECKED": 1, "FAIL": 2}


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
