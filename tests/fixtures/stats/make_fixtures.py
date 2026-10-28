#!/usr/bin/env python3
"""Write tests/fixtures/stats/daily/: a small scrubbed set in the fixed format, synthetic nodes only.

Run once: python3 -B tests/fixtures/stats/make_fixtures.py

Four nodes (ids are repeated hex digits, not real nodes), five day files over
six dates:
  2026-10-28  settled, 3 listed: node 1 answers both; node 2 refuses both;
              node 3 answers checkpoints at the retry an hour in, and nothing
              comes back for history_card at either attempt.
  2026-10-29  missed.
  2026-10-30  settled, 4 listed: node 2 absent; node 3 replies not_evidence /
              no_announced_key; node 4 joins with no checkpoint yet.
  2026-10-31  no file.
  2026-11-01  settled, 3 listed: node 3 left on this day; node 2 absent.
  2026-11-02  settled, 3 listed: node 2 absent two days running.
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "daily"
KEY = "e" * 64  # the reporter's key id
LOCK = "f" * 64  # sha256 of versions.lock


def nid(n):
    return str(n) * 64


def q(state, *attempts, size=None, digest=None):
    return {"state": state, "attempts": [{"at": at, "state": s} for at, s in attempts],
            "log_size_at_checkpoint": size, "answer_sha256": digest}


def node(n, line, questions, absent=0, left=None):
    return {"node_id": nid(n), "node_line": line, "absent_days_running": absent, "left_on": left,
            "questions": dict(zip(("checkpoints", "history_card"), questions))}


def size(mmr, at):
    return {"mmr_size": mmr, "timestamp": at}


def day(date, nodes, listed, missed=None):
    return {"date": date, "run": "missed" if missed else "settled", "missed_reason": missed,
            "versions_lock_sha256": None if missed else LOCK, "reporter_key_id": None if missed else KEY,
            "listed": listed, "nodes": nodes}


def at(date, hour):
    return f"{date}T{hour:02d}:00:00Z"


def days():
    d1, d3, d5, d6 = "2026-10-28", "2026-10-30", "2026-11-01", "2026-11-02"
    both = "discloses: checkpoints, history_card/1"
    left3 = "2026-11-01"
    yield day(d1, [
        node(1, both, [q("verified", (at(d1, 6), "verified"), size=size(41, at(d1, 5)), digest="1a" * 32),
                       q("verified", (at(d1, 6), "verified"), size=size(41, at(d1, 5)), digest="1b" * 32)]),
        node(2, "joined, discloses nothing", [q("refusal", (at(d1, 6), "refusal"), digest="2a" * 32),
                                              q("refusal", (at(d1, 6), "refusal"), digest="2b" * 32)]),
        node(3, "discloses: checkpoints",
             [q("verified", (at(d1, 6), "unreachable"), (at(d1, 7), "verified"), size=size(7, at(d1, 4)),
                digest="3a" * 32),
              q("absence", (at(d1, 6), "timeout"), (at(d1, 7), "timeout"))], left=left3),
    ], 3)
    yield day("2026-10-29", [], 3, missed="the reporter did not run: its host was down")
    yield day(d3, [
        node(1, both, [q("verified", (at(d3, 6), "verified"), size=size(57, at(d3, 5)), digest="1c" * 32),
                       q("verified", (at(d3, 6), "verified"), size=size(57, at(d3, 5)), digest="1d" * 32)]),
        node(2, "joined, discloses nothing", [q("absence", (at(d3, 6), "unreachable"), (at(d3, 7), "unreachable")),
                                              q("absence", (at(d3, 6), "unreachable"), (at(d3, 7), "unreachable"))],
             absent=1),
        node(3, "discloses: checkpoints", [q("not_evidence", (at(d3, 6), "not_evidence"), digest="3c" * 32),
                                           q("no_announced_key", (at(d3, 6), "no_announced_key"), digest="3d" * 32)],
             left=left3),
        node(4, "joined, no checkpoint yet", [q("no_checkpoint_yet", (at(d3, 6), "no_checkpoint_yet"),
                                                digest="4a" * 32),
                                              q("no_checkpoint_yet", (at(d3, 6), "no_checkpoint_yet"),
                                                digest="4b" * 32)]),
    ], 4)
    for date, absent in ((d5, 1), (d6, 2)):
        yield day(date, [
            node(1, both, [q("verified", (at(date, 6), "verified"), size=size(80, at(date, 5)), digest="1e" * 32),
                           q("verified", (at(date, 6), "verified"), size=size(80, at(date, 5)), digest="1f" * 32)]),
            node(2, "joined, discloses nothing", [q("absence", (at(date, 6), "absence")),
                                                  q("absence", (at(date, 6), "absence"))], absent=absent),
            node(4, both, [q("verified", (at(date, 6), "verified"), size=size(3, at(date, 5)), digest="4c" * 32),
                           q("refusal", (at(date, 6), "refusal"), digest="4d" * 32)]),
        ], 3)


def main():
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("*.json"):
        old.unlink()
    for d in days():
        (OUT / f"{d['date']}.json").write_text(json.dumps(d, sort_keys=True, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
