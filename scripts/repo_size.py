#!/usr/bin/env python3
"""Measure what the daily reports cost this repository, and project a year.

Usage:
  repo_size.py measure [--bodies DIR] [--nodes N] [--days D] [--start C]
                       [--per-day K] [--attempts A] [--work DIR] [--json]

`measure` runs the real reporter (`daily_report.py run`) once a day for D days
against a fake reporter-node console, commits each day's reports to a scratch
git repository, and measures:
  tree        bytes of every file under reports/ (what a checkout holds);
  pack        `git repack -adf` size: what the history costs, deltas on;
  pack_nodelta  the same with deltas off (`--window=0`): each file zlib'd alone,
              the bound when one day's bytes share nothing with the day before.
It prints those after day 1 and after the last day, and a year at 10 and 100
nodes: the measured total plus (365 - D) more days of the last day's growth,
scaled per node (nodes share no bytes: each node's answers are its own).

Each node answers with C + K*(day-1) checkpoints cut on day `day`. Answer bodies:
  --bodies DIR  real answers, `<n>.checkpoints.json` / `<n>.history_card.json`
                (a capsules node's wire answer with n checkpoints, or the signed
                refusal it sends instead); for a count with no file the largest
                n below it is used. Each node gets its own bytes by a fixed
                permutation of the hex digits, which keeps their size, their
                compressibility and the append-only growth of the list.
  (none)        synthetic: SYNTHETIC_STATEMENT pseudo-random bytes per
                checkpoint, refused past MAX_ANSWER_BYTES. For tests: its bytes
                compress worse than real checkpoints.
Each answer is wrapped as the plugin's reply (`{answer, held, request_digest,
verification}`). With --attempts A the reporter file sets A-1 retries
(`retry_after_s`) and every node answers only on attempt A: attempts 1..A-1 get
"could not reach peer" and keep only their absence line. That is the most a day
costs short of an absence: the reporter never asks an answered question again,
so a day holds at most one answer per question, in its attempt directory and
byte-copied into the settled day (git stores those two copies as one blob).

Standard library and git. Exit 0 = measured.
"""
import argparse
import base64
import contextlib
import datetime as dt
import functools
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_nodes  # noqa: E402
import daily_report  # noqa: E402

YEAR = 365
STEMS = ("checkpoints", "history_card")
REPORTER = {"node_id": "e" * 64, "key_id": "f" * 64}
RETRY_EVERY_S = 600
# capsules main (424f2c7) evidence_answer: an answer over 1 MiB is refused.
MAX_ANSWER_BYTES = 1024 * 1024
SYNTHETIC_STATEMENT = 1024
HEX_RUN = re.compile(r"[0-9a-f]{16,}")


def sha(*parts, n=64):
    """n pseudo-random hex digits, fixed by parts."""
    seed = "/".join(map(str, parts))
    return "".join(hashlib.sha512(f"{seed}/{j}".encode()).hexdigest() for j in range(-(-n // 128)))[:n]


def node_id(i):
    return sha("node", i)


def remap(i):
    """Node i's fixed permutation of the hex digits (node 0 keeps its bytes)."""
    digits = "0123456789abcdef"
    if i == 0:
        return str.maketrans(digits, digits)
    order = sorted(digits, key=lambda c: sha("remap", i, c))
    return str.maketrans(digits, "".join(order))


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class Bodies:
    """The plugin's reply for node i, question stem, n checkpoints, one attempt."""

    def __init__(self, bodies_dir=None):
        self.dir = Path(bodies_dir) if bodies_dir else None
        self.counts = {}
        if self.dir:
            for stem in STEMS:
                found = sorted(int(p.name.split(".")[0]) for p in self.dir.glob(f"*.{stem}.json"))
                if not found:
                    raise ValueError(f"{self.dir}: no <n>.{stem}.json")
                self.counts[stem] = found
        # One node's two answers at a time, asked once per attempt.
        self.wire = functools.lru_cache(maxsize=4)(self.real if self.dir else self.synthetic)

    def real(self, i, stem, n):
        have = [c for c in self.counts[stem] if c <= n] or self.counts[stem][:1]
        wire = json.loads((self.dir / f"{have[-1]}.{stem}.json").read_bytes())
        table = remap(i)
        sub = lambda s: HEX_RUN.sub(lambda m: m.group(0).translate(table), s)  # noqa: E731
        if "artifact" in wire:
            pad = "=" * (-len(wire["artifact"]) % 4)
            artifact = base64.urlsafe_b64decode(wire["artifact"] + pad).decode()
            wire["artifact"] = b64(sub(artifact).encode())
        return json.loads(sub(json.dumps(wire))), have[-1]

    def synthetic(self, i, stem, n):
        statements = [sha("statement", i, stem, k, n=2 * SYNTHETIC_STATEMENT) for k in range(n)]
        artifact = json.dumps({"anchor": sha("anchor", i, n), "checkpoints": statements}, separators=(",", ":"))
        wire = {"anchor": {"digest": sha("anchor", i, n), "freshness": n}, "artifact": b64(artifact.encode()),
                "artifact_digest": sha("ad", i, n), "profile": daily_report.PROFILE, "responder": sha("key", i)}
        if len(json.dumps(wire)) > MAX_ANSWER_BYTES:
            wire = {"key_id": sha("key", i), "reason": "policy_declined"}
        return wire, n

    def reply(self, i, stem, n, day, attempt):
        wire, listed = self.wire(i, stem, n)
        wire = dict(wire)
        digest = sha("request", i, stem, day, attempt)
        wire.update(issued_at=f"{day}T06:{attempt:02d}:00Z", request_digest=digest, sig=sha("sig", digest, n=128))
        if "reason" in wire:
            verification = {"reason": wire["reason"], "state": "refusal"}
        else:
            verification = {"anchor": wire["anchor"]["digest"], "checkpoints": listed, "records": [],
                            "state": "artifact"}
        reply = {"answer": wire, "held": [], "request_digest": digest, "verification": verification}
        return json.dumps(reply, sort_keys=True, separators=(",", ":")).encode()


class FakeConsole:
    """The reporter node's console: every node's key announced, `bodies` answers.

    A question is answered on its `answer_on`-th ask of the day; the asks before
    it get the plugin's "could not reach peer"."""

    def __init__(self, bodies, nodes, answer_on=1):
        self.bodies, self.index = bodies, {node_id(i): i for i in range(nodes)}
        self.day, self.count, self.answer_on, self.asked = None, 0, answer_on, {}
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def reply(self, status, body):
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                peer = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)["peer"][0]
                key = sha("key", fake.index[peer]) if peer in fake.index else None
                self.reply(200, json.dumps({"announced_key_id": key}).encode())

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stem = "history_card" if body["request"].get("derivation") else "checkpoints"
                i = fake.index[body["peer_id"]]
                attempt = fake.asked[i, stem, fake.day] = fake.asked.get((i, stem, fake.day), 0) + 1
                if attempt < fake.answer_on:
                    return self.reply(502, b"plugin tool failed: could not reach peer")
                self.reply(200, fake.bodies.reply(i, stem, fake.count, fake.day, attempt))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def tree_bytes(root):
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


def pack_bytes(repo, delta=True):
    git(repo, "repack", "-adfq", *([] if delta else ["--window=0"]))
    return sum(p.stat().st_size for p in (repo / ".git" / "objects" / "pack").glob("*.pack"))


def sizes(repo):
    return {"tree": tree_bytes(repo / "reports"), "pack": pack_bytes(repo), "pack_nodelta": pack_bytes(repo, False)}


class Clock:
    """The reporter's clock: each day's run starts at 06:00 UTC, and sleeping moves it on."""

    def __init__(self):
        self.at = None

    def now(self):
        return self.at

    def sleep(self, seconds):
        self.at += dt.timedelta(seconds=seconds)


def setup(repo, nodes, attempts=1):
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "reporter@example.invalid")
    git(repo, "config", "user.name", "reporter")
    git(repo, "config", "gc.auto", "0")
    (repo / "nodes").mkdir()
    for i in range(nodes):
        entry = {"node_id": node_id(i), "key_id": sha("key", i), "operator": "example-operator",
                 "consent": check_nodes.CONSENT, "mesh": "lab", "ask_route": "mesh",
                 "disclosure": {"policy": "counterparties", "stranger_safe": ["checkpoints", "history_card/1"]},
                 "host_build": "mesh-llm lab build", "plugin_version": "capsules 0.1.5"}
        (repo / "nodes" / f"{entry['node_id']}.json").write_text(json.dumps(entry, indent=2) + "\n")
    retries = [RETRY_EVERY_S * k for k in range(1, attempts)]
    (repo / "reporter.json").write_text(json.dumps({**REPORTER, "retry_after_s": retries}) + "\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "list")


def measure(bodies, repo, nodes=10, days=1, start=12, per_day=12, attempts=1, first="2027-01-01"):
    """Measured sizes after day 1, after day D-1 and after day D (all nodes)."""
    setup(repo, nodes, attempts)
    console, clock = FakeConsole(bodies, nodes, answer_on=attempts), Clock()
    out = {}
    try:
        for d in range(days):
            day = dt.date.fromisoformat(first) + dt.timedelta(days=d)
            console.day, console.count = day.isoformat(), start + per_day * d
            clock.at = dt.datetime.combine(day, dt.time(6), dt.timezone.utc)
            argv = ["daily_report.py", "run", "--date", day.isoformat(), "--console", console.url,
                    "--nodes", str(repo / "nodes"), "--out", str(repo / "reports" / "daily"),
                    "--reporter", str(repo / "reporter.json"), "--timeout", "30"]
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                code = daily_report.main(argv, now=clock.now, sleep=clock.sleep)
            if code != 0:
                raise RuntimeError(f"the reporter exited {code} on {day}: {err.getvalue().strip()}")
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", day.isoformat())
            if d in (0, days - 2, days - 1):
                out[d + 1] = sizes(repo)
    finally:
        console.close()
    return out


def project(measured, days, nodes, target, year=YEAR):
    """A year at `target` nodes: the measured total, plus the last day's growth
    for the rest of the year, per node. A repack can come out a little smaller
    than the one before it; that counts as no growth."""
    last = measured[days]
    step = {k: max(0, last[k] - measured[days - 1][k]) for k in last} if days > 1 else last
    return {k: (last[k] + (year - days) * step[k]) * target / nodes for k in last}


def human(n):
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("measure")
    m.add_argument("--bodies", help="directory of real answers, <n>.<stem>.json")
    m.add_argument("--nodes", type=int, default=10)
    m.add_argument("--days", type=int, default=1)
    m.add_argument("--start", type=int, default=12, help="checkpoints each node has cut on day 1")
    m.add_argument("--per-day", type=int, default=12, help="checkpoints each node cuts a day")
    m.add_argument("--attempts", type=int, default=1, help="the attempt on which every node answers")
    m.add_argument("--work", help="keep the scratch repository here (default: a temporary one)")
    m.add_argument("--json", action="store_true")
    args = parser.parse_args(argv[1:])
    try:
        bodies = Bodies(args.bodies)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(args.work or tmp)
        if args.work:
            shutil.rmtree(repo, ignore_errors=True)
        repo.mkdir(parents=True, exist_ok=True)
        measured = measure(bodies, repo, args.nodes, args.days, args.start, args.per_day, args.attempts)
    year = {n: project(measured, args.days, args.nodes, n) for n in (10, 100)}
    if args.json:
        print(json.dumps({"measured": measured, "year": year}, indent=2, sort_keys=True))
        return 0
    print(f"{args.nodes} nodes, answered on attempt {args.attempts}, {args.start} + {args.per_day}/day checkpoints, "
          f"{'real' if args.bodies else 'synthetic'} bodies")
    rows = [(f"after day {d}", measured[d]) for d in sorted(measured)]
    rows += [(f"year, {n} nodes", year[n]) for n in (10, 100)]
    print(f"{'':18} {'tree':>12} {'pack':>12} {'pack_nodelta':>12}")
    for label, s in rows:
        print(f"{label:18} {human(s['tree']):>12} {human(s['pack']):>12} {human(s['pack_nodelta']):>12}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
