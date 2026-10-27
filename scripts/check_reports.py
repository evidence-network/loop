#!/usr/bin/env python3
"""Check the daily reports: their layout, and that no day is silently missing.

Usage: check_reports.py [DIR] [--through YYYY-MM-DD [--reporter FILE]]
       (default DIR: reports/daily)

Every entry of DIR is a <YYYY-MM-DD> directory holding either RUN.json (a run:
one <node_id>/ per node it asked) or MISSED (no run that day, and why). A node
directory holds NODE, peer-key.json, and for each question one .request.json,
one .verification line, and exactly one of .response.json or .absence.txt.
Nothing else is allowed, so no score or ranking file can land beside them.
With --through, every day from the first report to that date must be present:
a day nobody marked is refused. With --reporter too, a reporter.json that
names a key (not the all-zero placeholder) and no report at all is refused:
the reporter is deployed but has never run. Standard library only. Exit 0 = clean, 1 = refused.
"""
import datetime as dt
import json
import re
import sys
from pathlib import Path

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
STEMS = ("checkpoints", "history_card")
STATES = ("verified", "no_checkpoint_yet", "refusal", "no_announced_key", "not_evidence", "absence")
NODE_LINE = re.compile(r"^(joined, discloses nothing|joined, no checkpoint yet|discloses: (checkpoints|history_card/1)(, history_card/1)?)$")
DAY_FILES = {"RUN.json", "README.md"}


def check_node(day, node_dir):
    errs = []
    where = f"{day}/{node_dir.name}"
    allowed = {"NODE", "peer-key.json"}
    if not (node_dir / "peer-key.json").is_file():
        errs.append(f"{where}: missing peer-key.json")
    for stem in STEMS:
        allowed |= {f"{stem}.request.json", f"{stem}.verification", f"{stem}.response.json", f"{stem}.absence.txt"}
        if not (node_dir / f"{stem}.request.json").is_file():
            errs.append(f"{where}: missing {stem}.request.json")
        answered = (node_dir / f"{stem}.response.json").is_file()
        absent = (node_dir / f"{stem}.absence.txt").is_file()
        if answered == absent:
            errs.append(f"{where}: needs exactly one of {stem}.response.json or {stem}.absence.txt")
        line_path = node_dir / f"{stem}.verification"
        if not line_path.is_file():
            errs.append(f"{where}: missing {stem}.verification")
            continue
        lines = line_path.read_text(encoding="utf-8").splitlines()
        state = lines[0].split(" ", 1)[0] if len(lines) == 1 and lines[0] else None
        if state not in STATES:
            errs.append(f"{where}: {stem}.verification is not one line starting with one of {', '.join(STATES)}")
        elif (state == "absence") != absent:
            errs.append(f"{where}: {stem}.verification says {state} but the answer is {'absent' if absent else 'present'}")
    node = node_dir / "NODE"
    if not node.is_file() or not NODE_LINE.match(node.read_text(encoding="utf-8").strip()):
        errs.append(f"{where}: NODE must say 'discloses: ...', 'joined, no checkpoint yet' or 'joined, discloses nothing'")
    extra = sorted(p.name for p in node_dir.iterdir() if p.name not in allowed)
    if extra:
        errs.append(f"{where}: unexpected file(s) {', '.join(extra)}")
    return errs


def check_day(day_dir):
    day = day_dir.name
    names = {p.name for p in day_dir.iterdir()}
    if "MISSED" in names:
        if names != {"MISSED"}:
            return [f"{day}: a MISSED day holds nothing else"]
        if not (day_dir / "MISSED").read_text(encoding="utf-8").strip():
            return [f"{day}: MISSED must say why"]
        return []
    if "RUN.json" not in names:
        return [f"{day}: neither RUN.json nor MISSED"]
    errs = []
    try:
        run = json.loads((day_dir / "RUN.json").read_text(encoding="utf-8"))
        asked = run["asked"]
        if not (isinstance(asked, list) and all(isinstance(n, str) for n in asked)):
            raise TypeError("asked is not a list of node ids")
        if run.get("date") != day:
            errs.append(f"{day}: RUN.json date is {run.get('date')}")
    except (ValueError, KeyError, TypeError) as e:
        return [f"{day}: RUN.json unreadable ({e})"]
    if "README.md" not in names:
        errs.append(f"{day}: missing README.md")
    nodes = {n for n in names if n not in DAY_FILES}
    for n in sorted(nodes):
        if not (HEX64.match(n) and (day_dir / n).is_dir()):
            errs.append(f"{day}: unexpected entry {n}")
    if sorted(n for n in nodes if HEX64.match(n)) != sorted(asked):
        errs.append(f"{day}: node directories do not match RUN.json asked")
    for n in sorted(nodes):
        if HEX64.match(n) and (day_dir / n).is_dir():
            errs += check_node(day, day_dir / n)
    return errs


def deployed(reporter):
    """Whether reporter.json names a real reporter key, not the placeholder."""
    key = json.loads(reporter.read_text(encoding="utf-8")).get("key_id")
    return isinstance(key, str) and HEX64.match(key) is not None and key != "0" * 64


def check(root, through=None, reporter=None):
    errs = []
    days = []
    for p in sorted(root.iterdir()) if root.is_dir() else []:
        if p.name in (".gitkeep", "README.md"):
            continue
        if not (p.is_dir() and DATE.match(p.name)):
            errs.append(f"{p.name}: not a <YYYY-MM-DD> directory")
            continue
        days.append(p.name)
        errs += check_day(p)
    if not days and through is not None and reporter is not None and deployed(reporter):
        errs.append(f"{reporter} names a reporter key, but {root} holds no report")
    if days and through is not None:
        day = dt.date.fromisoformat(days[0])
        while day <= through:
            if day.isoformat() not in days:
                errs.append(f"{day.isoformat()}: no report and no MISSED mark")
            day += dt.timedelta(days=1)
    return errs, days


def main(argv):
    args = argv[1:]
    through = None
    if "--through" in args:
        i = args.index("--through")
        through = dt.date.fromisoformat(args[i + 1])
        del args[i:i + 2]
    reporter = None
    if "--reporter" in args:
        i = args.index("--reporter")
        reporter = Path(args[i + 1])
        del args[i:i + 2]
    root = Path(args[0] if args else "reports/daily")
    errs, days = check(root, through, reporter)
    for e in errs:
        print(e, file=sys.stderr)
    if errs:
        return 1
    print(f"{root}: {len(days)} day{'' if len(days) == 1 else 's'} ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
