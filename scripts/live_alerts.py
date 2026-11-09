#!/usr/bin/env python3
"""Alerts for the daily live run (evidence-network/loop#310).

  scripts/live_alerts.py findings DIR...          one issue per FAIL or MALFORMED step in each live findings directory
  scripts/live_alerts.py changed BEFORE AFTER     the same, for each findings/*-live-* directory that range adds or changes
  scripts/live_alerts.py stale [--today YYYY-MM-DD]   one loop-live-stale issue when the newest live findings date is
                                                  more than 1 day old, and one loop-live-goose-stale issue when the newest
                                                  findings/goose/ live date is (loop#363: the Goose pass is checked on its
                                                  own, so a Mesh day does not hide a missing Goose day); it closes nothing.
                                                  It also opens one loop-reporter-stale issue when the newest daily report
                                                  in stats/data/daily/ (a settled day, not a MISSED one) is more than 1 day old
                                                  (loop#427: the lab reporter failed 10 days unnoticed)

A step is FAIL when its finding.json says so and MALFORMED when it is missing, not JSON, or refused by steps/finding.py
(check_record). NOT_CHECKED, PASS and a budget refusal open nothing: they are not a regression. An issue is labelled
loop-step-NN, the label loop.yml's nightly rehearsal job uses too, and its title and body follow that job's, so a step
that fails in both runs shares one open issue. An open issue gets a comment instead of a second issue. Issues are only
opened or commented on, never closed. GH names the gh binary (a stand-in in tests); it needs GH_TOKEN and GH_REPO.
.github/workflows/live-alerts.yml runs it.
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "steps"))
import finding  # noqa: E402

LIVE_DIR = re.compile(r"(\d{4}-\d{2}-\d{2})-live(-\d+)?")
STALE_LABEL = "loop-live-stale"
GOOSE_STALE_LABEL = "loop-live-goose-stale"
REPORTER_STALE_LABEL = "loop-reporter-stale"
STALE_DAYS = 1
# The repository that owns each live step's component, as in loop.yml's table (loop.yml has no other live steps either).
OWNER = {
    "01": "action-state-group/agent-action-capsule",
    "02": "action-state-group/evidencebook",
    "03": "Mesh-LLM/capsules-plugin",
    "12": "Mesh-LLM/capsules-plugin",
}


def gh(*args):
    p = subprocess.run([os.environ.get("GH", "gh"), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if p.returncode != 0:
        raise SystemExit(f"live_alerts: gh {' '.join(args[:2])} failed: {p.stderr.strip()}")
    return p.stdout.strip()


def judge(step_dir, digest_only):
    """(result, why): result is the finding's own, or MALFORMED with the reason."""
    f = step_dir / "finding.json"
    if not f.is_file():
        return "MALFORMED", "no finding.json"
    try:
        r = json.loads(f.read_text(encoding="utf-8"))
    except ValueError as e:
        return "MALFORMED", f"finding.json is not JSON ({e})"
    if not isinstance(r, dict):
        return "MALFORMED", "finding.json is not an object"
    try:
        errs = finding.check_record(step_dir, r, digest_only)
    except (AttributeError, KeyError, TypeError) as e:
        return "MALFORMED", f"finding.json has a field of the wrong type ({type(e).__name__}: {e})"
    if errs:
        return "MALFORMED", "; ".join(errs)[:600]
    return r["result"], str(r["detail"])[:600]


def failing(run):
    """[(step number, name, result, why)] for each FAIL or MALFORMED step directory of one findings directory."""
    run = Path(run)
    digest_only = finding.digest_only(run)
    out = []
    for d in sorted(p for p in run.iterdir() if p.is_dir()):
        m = finding.STEP_DIR.fullmatch(d.name)
        if not m:
            continue
        result, why = judge(d, digest_only)
        if result in ("FAIL", "MALFORMED"):
            out.append((m.group(1), m.group(2), result, why))
    return out


def raise_issue(label, color, description, title, body):
    gh("label", "create", label, "--color", color, "--description", description, "--force")
    n = gh("issue", "list", "--label", label, "--state", "open", "--json", "number", "--jq", ".[0].number")
    if n:
        gh("issue", "comment", n, "--body", body)
    else:
        gh("issue", "create", "--title", title, "--label", label, "--body", body)


def alert_run(run, run_url):
    run = Path(run)
    alerts = failing(run)
    where = run.relative_to(ROOT) if run.is_absolute() and run.is_relative_to(ROOT) else run
    for nn, name, result, why in alerts:
        # OWNER names Mesh's components; Goose steps run on evidencebook's G1 node (loop#315).
        owner = "evidence-network/loop#315 (the Goose pass)" if "goose" in run.parts else OWNER.get(nn, "unknown")
        raise_issue(f"loop-step-{nn}", "B60205", f"loop step {nn} failed",
                    f"loop step {nn} ({name}) fails",
                    f"Step {nn} ({name}) is {result} on the live {'Goose ' if 'goose' in run.parts else ''}run {run.name}: {run_url}\n"
                    f"The finding is {where.as_posix()}/{nn}-{name}/finding.json. {why}\n"
                    f"Owning component repository: {owner}.")
    print(f"live_alerts: {run.name}: {len(alerts)} FAIL or MALFORMED step(s)")
    return len(alerts)


def newest_live(findings=ROOT / "findings"):
    dates = [m.group(1) for d in findings.iterdir() if d.is_dir() and (m := LIVE_DIR.fullmatch(d.name))]
    return max(dates) if dates else None


def scrubbed_days(daily=ROOT / "stats" / "data" / "daily"):
    """{date: day file} for each readable scrubbed day file in stats/data/daily/.

    evidence-network/loop-data is private, so loop's token cannot read its `report: daily` commits; its publish-scrubbed
    workflow pushes one scrubbed file per day into stats/data/daily/ every night, and run is "settled" only for a day
    the reporter finished and "missed" for a MISSED one (scripts/scrub_reports.py)."""
    days = {}
    for f in daily.glob("*.json") if daily.is_dir() else []:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("date") == f.stem and re.fullmatch(r"\d{4}-\d{2}-\d{2}", f.stem):
            days[f.stem] = d
    return days


def newest_report(daily=ROOT / "stats" / "data" / "daily"):
    """Date of the newest settled daily report, or None. A MISSED day is not a report."""
    dates = [k for k, d in scrubbed_days(daily).items() if d.get("run") == "settled"]
    return max(dates) if dates else None


def alert_reporter_stale(today, run_url, daily=ROOT / "stats" / "data" / "daily"):
    newest = newest_report(daily)
    age = (today - datetime.date.fromisoformat(newest)).days if newest else None
    if age is not None and age <= STALE_DAYS:
        print(f"live_alerts: newest daily report {newest} is {age} day(s) old")
        return False
    seen = f"the newest daily report is dated {newest}, {age} days ago" if newest else "there is no daily report"
    days = scrubbed_days(daily)
    # Only a MISSED day newer than the newest report says why the reporter is silent now; an older one is history.
    missed = max((k for k, d in days.items() if d.get("run") == "missed" and (newest is None or k > newest)), default=None)
    why = f"The newest day the reporter marked MISSED is {missed}: {days[missed].get('missed_reason')}.\n" if missed else ""
    raise_issue(REPORTER_STALE_LABEL, "FBCA04", "the daily reporter has published no report",
                "the daily reporter has published no report for over a day",
                f"As of {today}, {seen} (more than {STALE_DAYS} day old; a MISSED day is not a report): {run_url}\n{why}"
                "Read from stats/data/daily/, which evidence-network/loop-data's publish-scrubbed workflow fills daily. "
                "Either the reporter on the lab VM has stopped (check its timer and its git pull in loop-data), or it ran and "
                "wrote MISSED (the reason above, if any; loop-data's reports workflow stays green then), or publish-scrubbed has "
                "stopped (its raw store in loop-data is then ahead of this one). This issue is closed by hand once a new "
                "report is published.")
    return True


def alert_stale(today, run_url, findings=ROOT / "findings", goose=False):
    """goose: judge findings/goose/ (the Goose pass of the daily run) with its own label and title."""
    where = findings / "goose" if goose else findings
    newest = newest_live(where) if where.is_dir() else None
    age = (today - datetime.date.fromisoformat(newest)).days if newest else None
    kind = "Goose live" if goose else "live"
    if age is not None and age <= STALE_DAYS:
        print(f"live_alerts: newest {kind} findings {newest} is {age} day(s) old")
        return False
    seen = f"the newest {kind} findings are dated {newest}, {age} days ago" if newest else f"there are no {kind} findings"
    raise_issue(GOOSE_STALE_LABEL if goose else STALE_LABEL, "FBCA04",
                f"the daily {kind} run has not landed",
                f"the daily {kind} run is stale",
                f"As of {today}, {seen} (more than {STALE_DAYS} day old): {run_url}\n"
                "The lab host's run may have failed, or the harvest pull request may not be merged; check "
                "scripts/live_harvest.py's pull request"
                + (" and the Goose pass in the run record (goose.status)" if goose else "") + ". "
                "This issue is closed by hand once a new live run is on main.")
    return True


def changed_dirs(before, after):
    # A new branch (all zeros) or a before the clone does not hold (a force push): the last commit's own changes.
    if set(before) == {"0"} or subprocess.run(["git", "cat-file", "-e", f"{before}^{{commit}}"], cwd=ROOT,
                                               capture_output=True).returncode != 0:
        before = f"{after}^"
    out = subprocess.run(["git", "diff", "--name-only", before, after, "--", "findings"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout.split()
    names = set()
    for p in out:
        parts = Path(p).parts
        if len(parts) > 3 and parts[1] == "goose":  # findings/goose/<name>/...
            names.add(Path("goose") / parts[2])
        elif len(parts) > 2:
            names.add(Path(parts[1]))
    return [ROOT / "findings" / n for n in sorted(names) if LIVE_DIR.fullmatch(n.name) and (ROOT / "findings" / n).is_dir()]


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("findings").add_argument("dirs", nargs="+")
    c = sub.add_parser("changed")
    c.add_argument("before")
    c.add_argument("after")
    sub.add_parser("stale").add_argument("--today", default=datetime.datetime.now(datetime.timezone.utc).date().isoformat())
    a = ap.parse_args(argv[1:])
    run_url = os.environ.get("RUN_URL", "")
    if a.cmd == "stale":
        today = datetime.date.fromisoformat(a.today)
        alert_stale(today, run_url)
        alert_stale(today, run_url, goose=True)
        alert_reporter_stale(today, run_url)
        return 0
    dirs = a.dirs if a.cmd == "findings" else changed_dirs(a.before, a.after)
    for d in dirs:
        alert_run(d, run_url)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
