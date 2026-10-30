#!/usr/bin/env python3
"""Make step 10's rehearsal stand-in run: population/rehearsal/step10/run.

Usage: make_step10_standin.py

The stand-in is a real rehearsal run, not hand-written findings: steps 05, 06, 07 and 09 run on the rehearsal
population, their findings are dated into a closed month (2026-09-14), and the real step 08 reproduces them. Its
manifest is therefore the one 08 gives, which tests/test_step10_judge.py checks by running 08 again. Needs the
tools of steps 05 to 09 (their setup.sh). Run again only when a step's proof changes, then commit the result.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POP = ROOT / "population" / "rehearsal"
DEST = POP / "step10" / "run"
STEPS = ("05-deal", "06-settle", "07-ask", "09-dispute")
DAY = "2026-09-14T10:{m:02d}:{s:02d}Z"
RUN_STARTED = "2026-09-14T09:59:00Z"
VERIFIED = ("2026-10-02T09:00:00Z", "2026-10-02T09:01:00Z")  # 08's own dates: the as-of instant is its `finished`


def dump(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")


def main():
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    with tempfile.TemporaryDirectory() as tmp:
        run = Path(tmp) / "run"
        run.mkdir()
        for i, base in enumerate(STEPS):
            r = subprocess.run([str(ROOT / "steps" / base), "--population", str(POP), "--out", str(run / base)],
                               env=env, cwd=ROOT, capture_output=True, text=True, timeout=900)
            if r.returncode != 0:
                sys.exit(f"{base} exited {r.returncode}: {r.stderr[-1500:]}")
            (run / base / "loop-step.log").unlink(missing_ok=True)
            f = json.loads((run / base / "finding.json").read_text())
            if f["result"] != "PASS":
                sys.exit(f"{base} is {f['result']}: {f['detail'][:400]}")
            f["started"], f["finished"] = DAY.format(m=i, s=0), DAY.format(m=i, s=30)
            dump(run / base / "finding.json", f)
        dump(run / "run.json", {"population": "rehearsal", "stage": "1", "started": RUN_STARTED,
                                "steps": [s.split("-")[0] for s in STEPS] + ["08"]})  # stage 1 is loop.sh's; 08 reads it
        r = subprocess.run([str(ROOT / "steps" / "08-verify"), "--population", str(POP), "--out", str(run / "08-verify")],
                           env=env, cwd=ROOT, capture_output=True, text=True, timeout=900)
        if r.returncode != 0:
            sys.exit(f"08-verify exited {r.returncode}: {r.stderr[-1500:]}")
        f = json.loads((run / "08-verify" / "finding.json").read_text())
        # The stand-in holds four of stage 1's steps, so 08 honestly says the others were not run: NOT_CHECKED, with
        # those entries and nothing else; every step it holds is reproduced.
        other = [x for x in f["not_checked"] if not x["what"].startswith("step ") or "did not run" not in x["why"]]
        if f["result"] != "NOT_CHECKED" or other:
            sys.exit(f"08-verify is {f['result']}: {f['detail'][:1500]} {other}")
        f["started"], f["finished"] = VERIFIED
        dump(run / "08-verify" / "finding.json", f)
        shutil.rmtree(DEST, ignore_errors=True)
        shutil.copytree(run, DEST)
    print(f"wrote {DEST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
