#!/usr/bin/env python3
"""Write tests/fixtures/stats/negative/daily/: a missed day and a node whose count fell, synthetic nodes only.

Run once: python3 -B tests/fixtures/stats/make_negative.py

  2026-11-03  settled, 2 listed: nodes 5 and 6 answer both.
  2026-11-04  settled: node 5's log grows; node 6 answers.
  2026-11-05  missed.
  2026-11-06  settled: node 5's log is smaller than on 11-04 (its count fell); node 6 grows.
The site draws 11-05 and 11-06 as hatched gaps in the records chart, and no zero for either.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from make_fixtures import KEY, LOCK, at, node, q, size  # noqa: E402

OUT = HERE / "negative" / "daily"


def both(n, mmr, date, d):
    return node(n, "discloses: checkpoints, history_card/1", [
        q("verified", (at(date, 6), "verified"), size=size(mmr, at(date, 5)), digest=f"{n}{d}" * 32),
        q("verified", (at(date, 6), "verified"), size=size(mmr, at(date, 5)), digest=f"{d}{n}" * 32)])


def day(date, nodes, missed=None):
    return {"date": date, "run": "missed" if missed else "settled", "missed_reason": missed,
            "versions_lock_sha256": None if missed else LOCK, "reporter_key_id": None if missed else KEY,
            "listed": 2, "nodes": nodes}


def days():
    yield day("2026-11-03", [both(5, 79, "2026-11-03", "a"), both(6, 11, "2026-11-03", "b")])
    yield day("2026-11-04", [both(5, 97, "2026-11-04", "c"), both(6, 15, "2026-11-04", "d")])
    yield day("2026-11-05", [], missed="the reporter did not run: its host was down")
    yield day("2026-11-06", [both(5, 41, "2026-11-06", "e"), both(6, 19, "2026-11-06", "a")])


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.json"):
        old.unlink()
    for d in days():
        (OUT / f"{d['date']}.json").write_text(json.dumps(d, sort_keys=True, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

