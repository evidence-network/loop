#!/usr/bin/env python3
"""Alerts for the daily live run (evidence-network/loop#310).

  scripts/live_alerts.py findings DIR...          one issue per FAIL or MALFORMED step in each live findings directory
  scripts/live_alerts.py changed BEFORE AFTER     the same, for each findings/*-live-* directory that range adds or changes
  scripts/live_alerts.py stale [--today YYYY-MM-DD]   one loop-live-stale issue when the newest live findings date is
                                                  more than 1 day old; it closes nothing

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
    errs = finding.check_record(step_dir, r, digest_only)
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
    for nn, name, result, why in alerts:
        owner = OWNER.get(nn, "unknown")
        raise_issue(f"loop-step-{nn}", "B60205", f"loop step {nn} failed",
                    f"loop step {nn} ({name}) fails",
                    f"Step {nn} ({name}) is {result} on the live run {run.name}: {run_url}\n"
                    f"The finding is {run.as_posix()}/{nn}-{name}/finding.json. {why}\n"
                    f"Owning component repository: {owner}.")
    print(f"live_alerts: {run.name}: {len(alerts)} FAIL or MALFORMED step(s)")
    return len(alerts)


def newest_live(findings=ROOT / "findings"):
    dates = [m.group(1) for d in findings.iterdir() if d.is_dir() and (m := LIVE_DIR.fullmatch(d.name))]
    return max(dates) if dates else None


def alert_stale(today, run_url, findings=ROOT / "findings"):
    newest = newest_live(findings)
    age = (today - datetime.date.fromisoformat(newest)).days if newest else None
    if age is not None and age <= STALE_DAYS:
        print(f"live_alerts: newest live findings {newest} is {age} day(s) old")
        return False
    seen = f"the newest live findings are dated {newest}, {age} days ago" if newest else "there are no live findings"
    raise_issue(STALE_LABEL, "FBCA04", "the daily live run has not landed",
                "the daily live run is stale",
                f"As of {today}, {seen} (more than {STALE_DAYS} day old): {run_url}\n"
                "The lab host's run may have failed, or the operator's Mac slept; check scripts/live_harvest.py's pull request. "
                "This issue is closed by hand once a new live run is on main.")
    return True


def changed_dirs(before, after):
    if set(before) == {"0"}:
        before = f"{after}^"
    out = subprocess.run(["git", "diff", "--name-only", before, after, "--", "findings"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout.split()
    names = {Path(p).parts[1] for p in out if len(Path(p).parts) > 2}
    return [ROOT / "findings" / n for n in sorted(names) if LIVE_DIR.fullmatch(n) and (ROOT / "findings" / n).is_dir()]


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
        alert_stale(datetime.date.fromisoformat(a.today), run_url)
        return 0
    dirs = a.dirs if a.cmd == "findings" else changed_dirs(a.before, a.after)
    for d in dirs:
        alert_run(d, run_url)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
