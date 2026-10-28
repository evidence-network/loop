#!/usr/bin/env python3
"""Write tests/fixtures/stats/findings/: three rehearsal runs, synthetic, through steps/finding.py's write().

Run once: python3 -B tests/fixtures/stats/make_findings.py

  2026-10-30-rehearsal  01 PASS, 02 NOT_CHECKED, 03 FAIL; no 12.
  2026-11-01-rehearsal  01 PASS, 02 PASS, 03 NOT_CHECKED, 12 MALFORMED (its proof no longer matches).
  2026-11-02-rehearsal  01 PASS with a named gap, 02 PASS, 03 PASS, 12 PASS.
No live run: the page shows "no run yet" for live. Nothing is pinned, so the
fixture does not change when versions.lock does. Times are fixed.
"""
import json
import shutil
import sys
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "steps"))
import finding  # noqa: E402

OUT = HERE / "findings"
NAMES = {1: "seal", 2: "book", 3: "advertise", 12: "join"}
NC = [{"what": "the fact this step asserts", "why": "the rehearsal fixture leaves it open on purpose",
       "issue": "https://github.com/evidence-network/loop/issues/24"}]
GAP = "rehearsal fixture: PASS. Outside this step's assertion: a second reader of the record: evidence-network/loop#24."


def step(run, n, result, minute, detail=""):
    d = run / f"{n:02d}-{NAMES[n]}"
    d.mkdir(parents=True)
    (d / "proof.bin").write_bytes(b"rehearsal fixture proof bytes, step %d\n" % n)
    with mock.patch.object(finding, "now", return_value=f"{run.name[:10]}T02:{minute:02d}:00Z"):
        finding.write(d, result=result, population="rehearsal", asserts=f"step {n:02d} holds on the rehearsal nodes",
                      pinned=[], proof=["proof.bin"], detail=detail,
                      not_checked=NC if result == "NOT_CHECKED" else (), started=f"{run.name[:10]}T02:00:00Z")
    return d


def run(date, steps):
    r = OUT / f"{date}-rehearsal"
    r.mkdir(parents=True)
    (r / "run.json").write_text(json.dumps({"population": "rehearsal", "stage": "1",
                                            "started": f"{date}T02:00:00Z"}, indent=2, sort_keys=True) + "\n")
    for n, result, minute, *detail in steps:
        step(r, n, result, minute, *detail)
    return r


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    run("2026-10-30", [(1, "PASS", 2), (2, "NOT_CHECKED", 3), (3, "FAIL", 4)])
    r = run("2026-11-01", [(1, "PASS", 1), (2, "PASS", 3), (3, "NOT_CHECKED", 4), (12, "PASS", 6)])
    (r / "12-join" / "proof.bin").write_bytes(b"altered after the finding was written\n")  # MALFORMED
    run("2026-11-02", [(1, "PASS", 1, GAP), (2, "PASS", 2), (3, "PASS", 3), (12, "PASS", 5)])


if __name__ == "__main__":
    main()
