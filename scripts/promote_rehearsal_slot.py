#!/usr/bin/env python3
"""Go live: copy one clean rehearsal slot into the daily set as day one.

Usage: promote_rehearsal_slot.py (--slot <YYYY-MM-DD>T<HHMM>Z | --last-clean)
                                 [--data DIR] [--nodes DIR] [--lock FILE]
                                 [--reporter FILE] [--commit]
       (default DIR: $LOOP_DATA_DIR, the loop-data checkout)

Copies DIR/reports/rehearsal/<slot>/ to DIR/reports/daily/<day>/, <day> being
the slot's UTC day, as a live day in the daily layout. Every file the nodes'
plugins returned, and every file of each node's day, is copied byte for byte.
Only the two files the reporter wrote about the run change:
  RUN.json   `date` becomes <day>; `label: live` and
             `promoted_from: rehearsal/<slot>` are added;
  README.md  its title becomes `# Daily report <day>` (no TEST), and a line
             says the day was promoted from that slot.
--last-clean takes the newest clean slot, as scripts/rehearsal_confidence.py
judges slots (the current nodes/ and versions.lock).

Refuses, writing nothing, when the slot is not clean; when reports/daily/<day>
exists already (a report or a MISSED mark); when the daily set with the day in
it fails check_reports.py --through <day> --reporter <reporter.json> (the daily
watchdog); or when a change would fall outside reports/daily/<day>/.

--commit is for the reporter node, from ~/loop beside ~/loop-data, as
run_daily.sh runs: the checkout must be on main with nothing outgoing, it is
fast-forwarded to origin/main first, and the commit holds only
reports/daily/<day>/ (anything else staged, or any outgoing commit touching
another path, and the promotion is undone). It does not push: the reporter's
deploy key pushes it with `git -C <DIR> push origin HEAD:main`.
Standard library only. Exit 0 = promoted, 1 = refused.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_reports  # noqa: E402
import report_layout as layout  # noqa: E402
import rehearsal_confidence as confidence  # noqa: E402

REWRITTEN = ("RUN.json", "README.md")


class Refused(Exception):
    pass


def git(data, *args):
    done = subprocess.run(["git", "-C", str(data), *args], capture_output=True, text=True)
    if done.returncode != 0:
        raise Refused(f"git {' '.join(args)}: {done.stderr.strip()}")
    return done.stdout


def live_run(run, day, slot):
    run = dict(run)
    run["date"] = day
    run["label"] = "live"
    run["promoted_from"] = f"rehearsal/{slot}"
    return json.dumps(run, indent=2) + "\n"


def live_readme(text, day, slot):
    lines = text.split("\n")
    title = f"# Rehearsal report {slot} (TEST)"
    if lines[0] != title:
        raise Refused(f"rehearsal/{slot}/README.md does not start with {title!r}")
    lines[0] = f"# Daily report {day}"
    lines[1:1] = ["", f"Day one of the daily report, promoted from the rehearsal slot `rehearsal/{slot}`, its last "
                      "clean slot. Every other file is that slot's, byte for byte."]
    out = "\n".join(lines)
    if "TEST" in out:
        raise Refused(f"rehearsal/{slot}/README.md still says TEST after the rewrite")
    return out


def build_day(src, dest, day, slot):
    """Copy the slot to dest, rewriting only RUN.json and README.md."""
    shutil.copytree(src, dest, symlinks=True)
    (dest / "RUN.json").write_text(live_run(layout.run_record(src), day, slot), encoding="utf-8")
    (dest / "README.md").write_text(live_readme((src / "README.md").read_text(encoding="utf-8"), day, slot),
                                    encoding="utf-8")


def watchdog(daily, day, reporter):
    """The daily watchdog over a daily set: check_reports --through <day> --reporter <reporter.json>."""
    errs, _ = check_reports.check(daily, layout.Daily.parse(day), Path(reporter), layout.Daily)
    return errs


def outside(paths, day):
    keep = f"reports/daily/{day}/"
    return sorted(p for p in paths if p and not p.startswith(keep))


def promote(data, slot, nodes_dir, lock, reporter, commit=False):
    """Promote the slot (None: the newest clean one). Returns (slot, day)."""
    data = Path(data).resolve()
    if commit:
        if git(data, "symbolic-ref", "--short", "HEAD").strip() != "main":
            raise Refused(f"{data} is not on main")
        git(data, "fetch", "--quiet", "origin", "main")
        if git(data, "log", "--format=%h", "origin/main..HEAD").strip():
            raise Refused(f"{data} has commits not on origin/main: push or drop them first")
        if git(data, "rev-parse", "HEAD") != git(data, "rev-parse", "origin/main"):
            git(data, "merge", "--ff-only", "--quiet", "origin/main")
    rehearsal, daily = data / "reports" / "rehearsal", data / "reports" / "daily"
    for p in (data / "reports", rehearsal, daily):
        if p.is_symlink():
            raise Refused(f"{p} is a symlink")
    nodes = confidence.listed(nodes_dir)
    lock_sha = confidence.lock_sha256(lock)
    if slot is None:
        slot = confidence.last_clean(rehearsal, nodes, lock_sha)
        if slot is None:
            raise Refused(f"no clean slot in {rehearsal}")
    src = rehearsal / slot_name(slot)
    if not src.is_dir():
        raise Refused(f"{src}: no such slot")
    why = confidence.judge(src, nodes, lock_sha)
    if why is not None:
        raise Refused(f"rehearsal/{slot} is not clean: {why}")
    day = slot[:10]
    dest = daily / day
    if os.path.lexists(dest):
        raise Refused(f"reports/daily/{day} exists already: day one is never written over a day")
    # Build and check the day beside a copy of the daily set before anything lands in the checkout.
    with tempfile.TemporaryDirectory() as tmp:
        trial = Path(tmp) / "daily"
        if daily.is_dir():
            shutil.copytree(daily, trial, symlinks=True)
        else:
            trial.mkdir()
        build_day(src, trial / day, day, slot)
        errs = watchdog(trial, day, reporter)
        if errs:
            raise Refused("the daily set with day one fails check_reports:\n  " + "\n  ".join(errs))
        before = set(git(data, "status", "--porcelain", "--untracked-files=all").splitlines()) if commit else set()
        shutil.copytree(trial / day, dest, symlinks=True)
    try:
        errs = watchdog(daily, day, reporter)
        if errs:
            raise Refused("the daily set with day one fails check_reports:\n  " + "\n  ".join(errs))
        if commit:
            changed = [l[3:] for l in git(data, "status", "--porcelain", "--untracked-files=all").splitlines()
                       if l not in before]
            stray = outside(changed, day)
            if stray:
                raise Refused(f"the promotion changed paths outside reports/daily/{day}/: " + ", ".join(stray))
            git(data, "add", "--", f"reports/daily/{day}")
            stray = outside(git(data, "diff", "--cached", "--name-only", "--no-renames").splitlines(), day)
            if stray:
                git(data, "reset", "-q", "--", f"reports/daily/{day}")
                raise Refused(f"the commit would also hold paths outside reports/daily/{day}/: " + ", ".join(stray))
            git(data, "commit", "--quiet", "-s", "-m", f"report: daily {day}, day one (promoted from rehearsal/{slot})")
            # Every outgoing commit, as run_daily.sh checks before it pushes.
            stray = outside(git(data, "log", "-m", "--format=", "--name-only", "origin/main..HEAD").splitlines(), day)
            if stray:
                git(data, "reset", "-q", "--soft", "HEAD~1")
                git(data, "reset", "-q", "--", f"reports/daily/{day}")
                raise Refused(f"outgoing commits touch paths outside reports/daily/{day}/: " + ", ".join(stray))
    except Refused:
        shutil.rmtree(dest, ignore_errors=True)
        raise
    return slot, day


def slot_name(slot):
    """The slot's directory name, refusing anything that is not a 10-minute slot."""
    return layout.Rehearsal.name(layout.Rehearsal.parse(slot))


def main(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    which = p.add_mutually_exclusive_group(required=True)
    which.add_argument("--slot", help="the rehearsal slot, <YYYY-MM-DD>T<HHMM>Z")
    which.add_argument("--last-clean", action="store_true", help="the newest clean rehearsal slot")
    p.add_argument("--data", default=os.environ.get("LOOP_DATA_DIR") or None, help="the loop-data checkout")
    p.add_argument("--nodes", default=str(ROOT / "nodes"))
    p.add_argument("--lock", default=str(ROOT / "versions.lock"))
    p.add_argument("--reporter", default=str(ROOT / "reporter" / "reporter.json"))
    p.add_argument("--commit", action="store_true", help="commit reports/daily/<day> in the loop-data checkout")
    args = p.parse_args(argv[1:])
    if not args.data:
        print("--data or LOOP_DATA_DIR: name the loop-data checkout", file=sys.stderr)
        return 1
    try:
        slot, day = promote(args.data, args.slot, args.nodes, args.lock, args.reporter, args.commit)
    except (Refused, OSError, ValueError, KeyError) as e:
        print(f"refused: {e}", file=sys.stderr)
        return 1
    print(f"promoted rehearsal/{slot} to reports/daily/{day} as day one (live)"
          + (": committed, not pushed" if args.commit else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
