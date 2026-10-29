#!/usr/bin/env python3
"""Check the daily reports: their layout, and that no day is silently missing.

Usage: check_reports.py [DIR] [--set daily|rehearsal] [--through PERIOD [--reporter FILE]]
       (default DIR: reports/<set>, default set: daily)

Every entry of DIR is a <YYYY-MM-DD> directory holding either RUN.json (a run:
one <node_id>/ per node it asked, and the sha256 of the versions.lock it ran
under as versions_lock_sha256) or MISSED (no run that day, and why). A node
directory holds attempts/ (one <YYYYMMDDTHHMMSSZ>/ per time the node was asked,
see report_layout.py) and the day as the run settled it: NODE, peer-key.json,
and for each question one .request.json, one .verification line, and exactly
one of .response.json or .absence.txt. A settled answer must be the bytes of
an attempt's answer. Nothing else is allowed, so no score or ranking file can
land beside them.
With --through, every day from the first report to that date must be present:
a day nobody marked is refused. With --reporter too, a reporter.json that
names a key (not the all-zero placeholder) and no report at all is refused:
the reporter is deployed but has never run. With --set rehearsal, the same
checks hold for the rehearsal set, a test of the reporter: every entry is a
10-minute UTC slot <YYYY-MM-DD>T<HHMM>Z instead of a day, and --through names
a slot. Standard library only. Exit 0 = clean, 1 = refused.
"""
import datetime as dt  # noqa: F401  (tests build --through dates from it)
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import report_layout as layout  # noqa: E402

HEX64 = re.compile(r"^[0-9a-f]{64}$")
STEMS = layout.STEMS
STATES = layout.DAY_STATES
NODE_LINE = re.compile(r"^(joined, discloses nothing|joined, no checkpoint yet|discloses: (checkpoints|history_card/1)(, history_card/1)?)$")
DAY_FILES = {"RUN.json", "README.md"}


def check_answer(where, d, stem, states):
    """One question's files in `d`: request, one verification line, one answer or absence."""
    errs = []
    if not (d / f"{stem}.request.json").is_file():
        errs.append(f"{where}: missing {stem}.request.json")
    answered = (d / f"{stem}.response.json").is_file()
    absent = (d / f"{stem}.absence.txt").is_file()
    if answered == absent:
        errs.append(f"{where}: needs exactly one of {stem}.response.json or {stem}.absence.txt")
    line_path = d / f"{stem}.verification"
    if not line_path.is_file():
        return errs + [f"{where}: missing {stem}.verification"]
    lines = line_path.read_text(encoding="utf-8").splitlines()
    state = lines[0].split(" ", 1)[0] if len(lines) == 1 and lines[0] else None
    if state not in states:
        errs.append(f"{where}: {stem}.verification is not one line starting with one of {', '.join(states)}")
    # No bytes came back: a no-answer state, or a reply the plugin could not read.
    elif (absent and state not in layout.NO_ANSWER + ("not_evidence",)) or (answered and state in layout.NO_ANSWER):
        errs.append(f"{where}: {stem}.verification says {state} but the answer is {'absent' if absent else 'present'}")
    return errs


def check_attempt(where, a):
    """One attempt: ATTEMPT naming its own time, peer-key.json, the questions it asked."""
    where = f"{where}/{layout.ATTEMPTS}/{a.name}"
    if not layout.ATTEMPT_NAME.match(a.name) or not a.is_dir():
        return [f"{where}: not a <YYYYMMDDTHHMMSSZ> attempt directory"]
    errs = []
    allowed = {"ATTEMPT", "peer-key.json"}
    attempt = layout.read_attempt(a) if (a / "ATTEMPT").is_file() else None
    if attempt is None:
        errs.append(f"{where}: ATTEMPT must be one line 'at=<time> by=run pass=<n>' or 'at=<time> by=ask'")
    elif attempt[0] != layout.attempt_time(a.name):
        errs.append(f"{where}: ATTEMPT says at={attempt[0]}, not the directory's time")
    if not (a / "peer-key.json").is_file():
        errs.append(f"{where}: missing peer-key.json")
    asked = 0
    for stem in STEMS:
        files = {f"{stem}.request.json", f"{stem}.verification", f"{stem}.response.json", f"{stem}.absence.txt"}
        allowed |= files
        if any((a / f).exists() for f in files):
            asked += 1
            errs += check_answer(where, a, stem, layout.ATTEMPT_STATES)
    if not asked:
        errs.append(f"{where}: asks no question")
    extra = sorted(p.name for p in a.iterdir() if p.name not in allowed)
    if extra:
        errs.append(f"{where}: unexpected file(s) {', '.join(extra)}")
    return errs


def check_node(day, node_dir):
    errs = []
    where = f"{day}/{node_dir.name}"
    allowed = {"NODE", "peer-key.json", layout.ATTEMPTS}
    if not (node_dir / "peer-key.json").is_file():
        errs.append(f"{where}: missing peer-key.json")
    root = node_dir / layout.ATTEMPTS
    entries = sorted(root.iterdir()) if root.is_dir() else []
    if not entries:
        errs.append(f"{where}: no {layout.ATTEMPTS}/: every answer is kept with the attempt that got it")
    for a in entries:
        errs += check_attempt(where, a)
    day_answers = layout.answers(node_dir)
    for stem in STEMS:
        allowed |= {f"{stem}.request.json", f"{stem}.verification", f"{stem}.response.json", f"{stem}.absence.txt"}
        errs += check_answer(where, node_dir, stem, STATES)
        settled = node_dir / f"{stem}.response.json"
        if settled.is_file() and not any(
            (a / f"{stem}.response.json").is_file()
            and (a / f"{stem}.response.json").read_bytes() == settled.read_bytes()
            for a, _ in day_answers[stem]["tried"]
        ):
            errs.append(f"{where}: {stem}.response.json is not the answer of any attempt")
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
        lock = run.get("versions_lock_sha256")
        if not (isinstance(lock, str) and HEX64.match(lock)):
            errs.append(f"{day}: RUN.json versions_lock_sha256 must be the 64-hex sha256 of the versions.lock it ran under")
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


def check(root, through=None, reporter=None, period=layout.Daily):
    errs = []
    days = []
    form = "<YYYY-MM-DD>" if period is layout.Daily else "<YYYY-MM-DD>T<HHMM>Z 10-minute slot"
    for p in sorted(root.iterdir()) if root.is_dir() else []:
        if p.name in (".gitkeep", "README.md"):
            continue
        if not (p.is_dir() and period.pattern.match(p.name)):
            errs.append(f"{p.name}: not a {form} directory")
            continue
        days.append(p.name)
        errs += check_day(p)
    if not days and through is not None and reporter is not None and deployed(reporter):
        errs.append(f"{reporter} names a reporter key, but {root} holds no report")
    if days and through is not None:
        day = period.parse(days[0])
        while day <= through:
            if period.name(day) not in days:
                errs.append(f"{period.name(day)}: no report and no MISSED mark")
            day += period.step
    return errs, days


def main(argv):
    args = argv[1:]
    period = layout.Daily
    if "--set" in args:
        i = args.index("--set")
        if args[i + 1] not in layout.SETS:
            print(f"--set must be one of {', '.join(sorted(layout.SETS))}", file=sys.stderr)
            return 1
        period = layout.SETS[args[i + 1]]
        del args[i:i + 2]
    through = None
    if "--through" in args:
        i = args.index("--through")
        through = period.parse(args[i + 1])
        del args[i:i + 2]
    reporter = None
    if "--reporter" in args:
        i = args.index("--reporter")
        reporter = Path(args[i + 1])
        del args[i:i + 2]
    root = Path(args[0] if args else f"reports/{period.set}")
    errs, days = check(root, through, reporter, period)
    for e in errs:
        print(e, file=sys.stderr)
    if errs:
        return 1
    print(f"{root}: {len(days)} {period.noun}{'' if len(days) == 1 else 's'} ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
