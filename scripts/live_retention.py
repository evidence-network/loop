#!/usr/bin/env python3
"""The retention rule for old live findings (evidence-network/loop#312). It reports; it deletes nothing.

  scripts/live_retention.py [FINDINGS_DIR] [--baseline FILE] [--json]

Why: a live run is ~27 MB raw and one runs daily, so main would pass GitHub Pages' 1 GB limit within a year (the
measurement is on loop#312). A harvested run (scripts/live_harvest.py, loop#309) already keeps only finding.json,
run.json, index.md and proof-archive.json on main, the proof bytes living in the release asset that file names by
sha256; `steps/finding.py check` accepts that digest-only layout. This module is the rule that says which runs on
main may be reduced to it.

The rule, per live run directory (<date>-live[-N]) under FINDINGS_DIR:
  keep   the newest live run: the status page and the site read its proof bytes.
  keep   the run baseline/live.json pins as the gate's reference, so a pin change is judged against whole proof.
  hold   a run with no valid proof-archive.json: its proof bytes exist nowhere else, so nothing of it is pruned.
  prune  any other run: every file but finding.json, run.json, index.md and proof-archive.json is a prune candidate.
Rehearsal, demo and any other directory under FINDINGS_DIR are outside the rule and never listed.

Exit: 0 the plan was printed; 2 usage or FINDINGS_DIR is not a directory.
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "steps"))
import finding  # noqa: E402

LIVE_RE = re.compile(r"(\d{4}-\d\d-\d\d)-live(?:-([1-9][0-9]*))?")
PROOF_ASSETS = ("run.json", "index.md", finding.ARCHIVE)


def baseline_run(baseline):
    """The findings directory name the baseline pins for the mesh ecosystem, or None."""
    try:
        return Path(json.loads(Path(baseline).read_text(encoding="utf-8"))["ecosystems"]["mesh"]["findings"]).name
    except (OSError, ValueError, KeyError, TypeError):
        return None


def prunable(run):
    """The files of run that main need not hold once its archive is pinned: all but each step's finding.json and the run's own record files."""
    out = []
    for p in sorted(run.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(run)
        if len(rel.parts) == 1 and rel.name in PROOF_ASSETS:
            continue
        if len(rel.parts) == 2 and rel.name == "finding.json":
            continue
        out.append(rel.as_posix())
    return out


def plan(findings, baseline=None):
    """[{run, action: keep|hold|prune, reason, files, bytes}] for each live run, newest first. Reads only."""
    root = Path(findings)
    runs = sorted((d for d in root.iterdir() if d.is_dir() and LIVE_RE.fullmatch(d.name)),
                  key=lambda d: (LIVE_RE.fullmatch(d.name).group(1), int(LIVE_RE.fullmatch(d.name).group(2) or 0)),
                  reverse=True)
    pinned = baseline_run(baseline) if baseline else None
    out = []
    for i, run in enumerate(runs):
        entry = {"run": run.name, "files": [], "bytes": 0}
        if i == 0:
            entry.update(action="keep", reason="newest live run: the page and the site read its proof bytes")
        elif run.name == pinned:
            entry.update(action="keep", reason="the run baseline/live.json pins as the gate's reference")
        elif not finding.check_archive(run)[1]:
            entry.update(action="hold", reason="no valid proof-archive.json: its proof bytes exist nowhere else")
        else:
            files = prunable(run)
            entry.update(action="prune", reason="proof bytes live in the release asset proof-archive.json pins by sha256",
                         files=files, bytes=sum((run / f).stat().st_size for f in files))
        out.append(entry)
    return out


def main(argv):
    ap = argparse.ArgumentParser(prog="live_retention.py")
    ap.add_argument("findings", nargs="?", default=str(ROOT / "findings"))
    ap.add_argument("--baseline", default=str(ROOT / "baseline" / "live.json"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if not Path(args.findings).is_dir():
        print(f"live_retention: {args.findings} is not a directory", file=sys.stderr)
        return 2
    entries = plan(args.findings, args.baseline)
    if args.json:
        print(json.dumps(entries, indent=2, sort_keys=True))
    else:
        for e in entries:
            extra = f", {len(e['files'])} files, {e['bytes']} bytes" if e["action"] == "prune" else ""
            print(f"{e['action']:5} {e['run']}: {e['reason']}{extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
