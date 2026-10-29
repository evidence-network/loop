#!/usr/bin/env python3
"""The daily reporter: ask every listed node the two stranger-safe questions.

Usage:
  daily_report.py run   [--set daily|rehearsal|ondemand] [--date D] [--console URL]
                        [--nodes DIR] [--out DIR] [--reporter FILE]
                        [--data-dir DIR] [--timeout S] [--self-node NAME]
                        [--second-console URL] [--population FILE]
  daily_report.py ask   (--node ID | --absent) [--date D] [--console URL]
                        [--nodes DIR] [--out DIR] [--reporter FILE]
                        [--data-dir DIR] [--timeout S] [--self-node NAME]
                        [--second-console URL] [--population FILE]
  daily_report.py prove --date D [--out DIR]

`run` asks each node in nodes/ `checkpoints` and `history_card/1` over the mesh
evidence request, through the reporter node's own capsules plugin
(POST <console>/api/plugins/capsules/tools/mesh_evidence_request), and writes
what came back under <out>/<date>/<node_id>/. It asks those two and nothing else.
A question that got no answer (the node unreachable, or no answer in time) is
asked again at the times in the reporter file's `retry_after_s` (seconds after
the run starts; none, one attempt). An answer, a signed refusal included, is
never asked again. Every attempt is its own directory, written once
(scripts/report_layout.py has the layout and how a day is read from it).
The plugin signs each request with the reporter's announced key and checks the
answer under the key the reporter's host announces for the node (mesh-llm
plugin key announcement; the reporter node sets no CAPSULES_PEER_KEYS map, so
no key comes from static configuration); this script records that check, it
does not repeat it.

The day, as the run settles it after its last attempt, per question Q
(`checkpoints`, `history_card`):
  Q.request.json     the request map handed to the plugin
  Q.response.json    the plugin's reply, the exact bytes received: the node's
                     answer (artifact or signed refusal) beside its verification
  Q.absence.txt      instead of a response, when nothing came back: each
                     attempt's time and why
  Q.verification     one line: verified | no_checkpoint_yet | refusal |
                     no_announced_key | not_evidence | absence, then key=value
                     details. no_checkpoint_yet is a signed coverage_unsatisfiable:
                     the node has cut no checkpoint yet (its first comes 300 s
                     after activity). It is neither a refusal nor an absence.
                     An absence lists every attempt time (attempts=...).
  peer-key.json      the key the reporter's node judged the answers under (as
                     of the last attempt)
  NODE               "discloses: ...", "joined, no checkpoint yet" or
                     "joined, discloses nothing"
Answered questions are byte copies of the attempt that answered them.

`--set rehearsal` (or LOOP_REPORT_SET=rehearsal) is a test of the reporter:
the same run, one report per 10-minute UTC slot (D is <YYYY-MM-DD>T<HHMM>Z)
under <out> = reports/rehearsal, MISSED for a slot with no run exactly as for
a day, and its retries bounded so the run settles inside its slot: it asks
again at REHEARSAL_RETRY_AFTER_S instead of `retry_after_s`, gives no request
more than REHEARSAL_TIMEOUT_S, and starts nothing after REHEARSAL_BUDGET_S
into the slot. The default set is `daily`, unchanged.

`--set ondemand` is a run made by hand between dailies (scripts/refresh_now.sh):
one report per run under <out> = reports/ondemand, named by the second it
started (D is <YYYY-MM-DD>T<HHMMSS>Z, UTC; default: now), and RUN.json says
"set": "ondemand". Its runs are not continuous: nothing is ever marked MISSED
for the time between two runs, and a run that cannot reach its own node writes
nothing at all (exit 2). An earlier run that stopped before it finished, never
committed, is removed. Its retries are bounded so the run settles in about two
minutes: it asks again at ONDEMAND_RETRY_AFTER_S, gives no request more than
ONDEMAND_TIMEOUT_S, and starts nothing after ONDEMAND_BUDGET_S into the run.

A node can't be asked through its own console, so the reporter's own node
(reporter.json `node_id`) is left out unless a second console is given:
`--second-console` (or LOOP_SECOND_CONSOLE), another lab node's console. Then
the reporter's node is asked like any listed node, through that console, and
RUN.json names it under `asked_through_second_console`. `--self-node` (or
LOOP_SELF_NODE) is the reporter's label in the population file
(population/live/population.json); when given, it must name the node
reporter.json names, so a stale label is refused rather than asking some
other node through the second console.

`ask` is the "ask again" path, after the day's run has settled: it asks one
node (--node) or every node with a question unanswered today (--absent) the
questions it has not answered today, and adds one attempt directory per node.
It writes nothing else; the files the run settled stay as they are, and
report_layout.answers() reads the day with the later attempt in it.

A day with no run is never silent: `run` first writes <out>/<day>/MISSED for
each day since the last report that has none, and a run that cannot reach its
own node writes MISSED for today and exits 2. No score, no ranking, no
aggregate: each node's files say what that node answered, and nothing is
totalled across nodes. A node that answered nothing is shown with how many
days running that is ("absent 3 days running"), a fact about that node only.

`prove` is the day-one acceptance check, printed, never written to the repo:
exit 0 when at least two nodes answered `checkpoints` verified with at least
one checkpoint, and `history_card/1` verified.

Standard library only. Exit 0 = done, 1 = refused (bad input), 2 = missed day.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_nodes  # noqa: E402
import report_layout as layout  # noqa: E402
import reporter_key  # noqa: E402

PROFILE = "evidence-interop/0"
# The two stranger-safe questions, as the capsules Evidence page asks them
# (web-ui evidenceRequestClient.ts). Keyed by the file-name stem.
QUESTIONS = {
    "checkpoints": {
        "profile": PROFILE,
        "coverage": {"min_freshness": 1},
        "subject": {"checkpoints": None},
    },
    "history_card": {
        "profile": PROFILE,
        "coverage": {"min_freshness": 1},
        "subject": {"full_history": None},
        "derivation": "history_card/1",
    },
}
QUESTION_NAMES = {"checkpoints": "checkpoints", "history_card": "history_card/1"}
TOOL_ROUTE = "/api/plugins/capsules/tools/mesh_evidence_request"
PEER_KEY_ROUTE = "/api/plugins/capsules/http/peer-key"
# The plugin's own ledger route; its node_pub_key_pem is the key the running
# plugin signs with, so the console the reporter talks to is bound to reporter.json.
LEDGER_ROUTE = "/api/plugins/capsules/http/ledger"
DEFAULT_CONSOLE = "http://127.0.0.1:3131"
PLACEHOLDER = "0" * 64
HEX64 = re.compile(r"^[0-9a-f]{64}$")
# The rehearsal set settles inside its 10-minute slot: retries in seconds after
# the run starts, the longest any one request waits, and how far into the slot
# the run may still start a request (the rest is left for the pull and push).
REHEARSAL_RETRY_AFTER_S = [120, 240]
REHEARSAL_TIMEOUT_S = 20.0
REHEARSAL_BUDGET_S = 420
# The on-demand set settles in about two minutes, the same way: the budget is
# counted from the run's start, and a request started near its end waits at most 1 s.
ONDEMAND_RETRY_AFTER_S = [30, 60]
ONDEMAND_TIMEOUT_S = 20.0
ONDEMAND_BUDGET_S = 90
# Per set: (retry_after_s, timeout_s, budget_s), in place of reporter.json's schedule.
BOUNDED = {"rehearsal": (REHEARSAL_RETRY_AFTER_S, REHEARSAL_TIMEOUT_S, REHEARSAL_BUDGET_S),
           "ondemand": (ONDEMAND_RETRY_AFTER_S, ONDEMAND_TIMEOUT_S, ONDEMAND_BUDGET_S)}
DEFAULT_POPULATION = "population/live/population.json"


class ReporterDown(Exception):
    """The reporter could not reach its own node: the day is missed, not the nodes."""


def http(method, url, body, timeout):
    """(status, bytes) for a reply, or raise OSError when nothing came back."""
    data = None if body is None else json.dumps(body, sort_keys=True).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def peer_key_url(console, peer):
    return f"{console}{PEER_KEY_ROUTE}?{urllib.parse.urlencode({'peer': peer})}"


def quote(text):
    return json.dumps(" ".join(str(text).split())[:300])


# What the host's tool route sends back when the plugin got no answer: mesh-llm
# handle_call answers a plugin tool error with a 502 carrying its text, and the
# capsules plugin (mesh_evidence_bridge.rs, SendError) says which failure it was.
NO_ANSWER_TEXT = (
    ("could not reach peer", "unreachable"),  # the mesh stream could not be opened
    ("peer does not answer evidence requests", "timeout"),  # opened; nothing came back in time
)
MALFORMED_TEXT = "peer returned malformed response"  # an answer, but not JSON


def verification_line(status, body, error, announced, listed, timed_out=False):
    """One line for one question: the state the plugin found, then details."""
    if error is not None:
        return f"{'timeout' if timed_out else 'absence'} why={quote(error)}"
    if status != 200:
        text = body.decode("utf-8", "replace")
        why = quote(f"http {status}: {text}")
        if status == 502:
            for marker, cls in NO_ANSWER_TEXT:
                if marker in text:
                    return f"{cls} why={why}"
            if MALFORMED_TEXT in text:
                return f"not_evidence why={why}"
        return f"absence why={why}"
    try:
        reply = json.loads(body)
        verification = reply["verification"]
        state = verification["state"]
    except (ValueError, KeyError, TypeError):
        return f"not_evidence why={quote('the plugin reply carries no verification')}"
    if announced is None:
        key = "listed_key=unknown"
    else:
        key = f"key={announced} listed_key={'match' if announced == listed else 'differs'}"
    if state == "artifact":
        # A history_card/1 artifact carries halves, not checkpoints.
        count = "halves" if "halves" in verification else "checkpoints"
        detail = f"{count}={verification.get(count)} anchor={verification.get('anchor')}"
        if "witnessed" in verification:
            detail += f" witnessed={verification['witnessed']}"
        return f"verified {detail} {key}"
    if state == "refusal" and verification.get("reason") == "coverage_unsatisfiable":
        return f"no_checkpoint_yet reason=coverage_unsatisfiable {key}"
    if state == "refusal":
        return f"refusal reason={verification.get('reason')} {key}"
    if state == "no_announced_key":
        return "no_announced_key listed_key=unknown"
    if state == "not_evidence":
        return f"not_evidence why={quote(verification.get('why'))} {key}"
    return f"not_evidence why={quote('unknown verification state ' + str(state))} {key}"


def write_new(path, data):
    """Write a file that must not exist yet: a report is never rewritten."""
    with open(path, "xb") as f:
        f.write(data if isinstance(data, bytes) else data.encode())


def ask_attempt(console, entry, node_dir, stems, at, by, timeout, left=None):
    """Ask one node the given questions once; write the attempt. Returns {stem: line}.

    `left`, when given, says how many seconds the run has left: no request
    waits longer than that, nor less than 1 s unless `timeout` is shorter.
    """
    if left is not None:
        cap = timeout
        timeout = lambda: min(cap, max(1.0, left()))  # noqa: E731
    else:
        fixed = timeout
        timeout = lambda: fixed  # noqa: E731
    node_id, listed = entry["node_id"], entry["key_id"]
    d = node_dir / layout.ATTEMPTS / layout.attempt_name(at)
    if d.exists():
        raise ValueError(f"{d} already exists: one attempt per node per second")
    d.mkdir(parents=True)
    write_new(d / "ATTEMPT", f"at={layout.iso(at)} {by}\n")
    announced = None
    try:
        status, body = http("GET", peer_key_url(console, node_id), None, timeout())
        write_new(d / "peer-key.json", body)
        if status == 200:
            reply = json.loads(body)
            key = reply.get("announced_key_id") if isinstance(reply, dict) else None
            announced = key if isinstance(key, str) and HEX64.match(key) else None
    except (OSError, ValueError) as e:
        if not (d / "peer-key.json").exists():
            write_new(d / "peer-key.json", json.dumps({"error": str(e)}) + "\n")
    lines = {}
    for stem in stems:
        request = QUESTIONS[stem]
        write_new(d / f"{stem}.request.json", json.dumps(request, indent=2, sort_keys=True) + "\n")
        status, body, error, timed_out = None, b"", None, False
        wait = timeout()
        try:
            status, body = http("POST", console + TOOL_ROUTE, {"peer_id": node_id, "request": request}, wait)
        except OSError as e:
            timed_out = isinstance(e, TimeoutError) or isinstance(getattr(e, "reason", None), TimeoutError)
            if timed_out:
                error = f"no reply from the reporter's node within {wait:g}s: {e}"
            else:
                error = f"the reporter's node did not take the request: {e}"
        if error is None and status == 200:
            write_new(d / f"{stem}.response.json", body)
        else:
            write_new(d / f"{stem}.absence.txt",
                      (error or f"http {status}\n{body.decode('utf-8', 'replace')}").rstrip("\n") + "\n")
        lines[stem] = verification_line(status, body, error, announced, listed, timed_out)
        write_new(d / f"{stem}.verification", lines[stem] + "\n")
    return lines


def settle(node_dir):
    """Write the node's day from its attempts, once, after the run's last attempt. Returns NODE."""
    day = layout.answers(node_dir)
    disclosed, waiting = [], False
    for stem in QUESTIONS:
        a = day[stem]
        first = a["tried"][0][0]
        write_new(node_dir / f"{stem}.request.json", (first / f"{stem}.request.json").read_bytes())
        if a["attempt"] is not None:
            for name in (f"{stem}.response.json", f"{stem}.absence.txt", f"{stem}.verification"):
                if (a["attempt"] / name).is_file():
                    write_new(node_dir / name, (a["attempt"] / name).read_bytes())
            line = a["line"]
        else:
            write_new(node_dir / f"{stem}.absence.txt", layout.absence_text(a["tried"], stem))
            line = layout.absence_line(a["tried"])
            write_new(node_dir / f"{stem}.verification", line + "\n")
        if line.startswith("verified "):
            disclosed.append(QUESTION_NAMES[stem])
        waiting = waiting or line.startswith("no_checkpoint_yet ")
    write_new(node_dir / "peer-key.json", (layout.attempts(node_dir)[-1] / "peer-key.json").read_bytes())
    if disclosed:
        node_line = f"discloses: {', '.join(disclosed)}"
    else:
        node_line = "joined, no checkpoint yet" if waiting else "joined, discloses nothing"
    write_new(node_dir / "NODE", node_line + "\n")
    return node_line


def retry_schedule(reporter, path):
    """`retry_after_s`: when to ask again, in seconds after the run starts, rising."""
    after = reporter.get("retry_after_s", [])
    ok = isinstance(after, list) and all(isinstance(s, (int, float)) and not isinstance(s, bool) and s > 0 for s in after)
    if not ok or after != sorted(set(after)):
        raise ValueError(f"{path}: retry_after_s must be a rising list of seconds after the run starts")
    return after


def load_reporter(path, data_dir):
    """The reporter's committed identity; with data_dir, check it is this node's."""
    reporter = json.loads(Path(path).read_text(encoding="utf-8"))
    for field in ("node_id", "key_id"):
        value = reporter.get(field)
        if not (isinstance(value, str) and HEX64.match(value)) or value == PLACEHOLDER:
            raise ValueError(f"{path}: {field} is not set (run scripts/reporter_key.py on the reporter node)")
    if data_dir is not None:
        reporter_key.check_private_key(Path(data_dir))
        local = reporter_key.read_identity(Path(data_dir))
        if local != {"node_id": reporter["node_id"], "key_id": reporter["key_id"]}:
            raise ValueError(f"{path} does not match the plugin key in {data_dir}: {local}")
    return reporter


def versions_lock_sha256(path=Path(__file__).resolve().parent.parent / "versions.lock"):
    """The digest of the versions.lock this run ran under, recorded in RUN.json."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def self_node(args, reporter, console):
    """The second console the reporter's own node is asked through, or None.

    Refuses a --self-node label the population does not have, or one that is
    not the node reporter.json names, and a second console that is the
    reporter's own.
    """
    second = (args.second_console or "").rstrip("/") or None
    if args.self_node:
        try:
            pop = json.loads(Path(args.population).read_text(encoding="utf-8"))
            named = {n["name"]: n.get("node_id") for n in pop["nodes"]}
        except (OSError, ValueError, KeyError, TypeError) as e:
            raise ValueError(f"--self-node {args.self_node!r}: cannot read the population {args.population} ({e})")
        if args.self_node not in named:
            raise ValueError(f"--self-node {args.self_node!r} is not a node in {args.population} "
                             f"({', '.join(named)})")
        if named[args.self_node] != reporter["node_id"]:
            raise ValueError(f"--self-node {args.self_node!r} is node {named[args.self_node]}, not the "
                             f"reporter's node {reporter['node_id']}")
    if second is not None and second == console:
        raise ValueError(f"--second-console {second} is the reporter's own console: a node can't be asked "
                         "through its own console")
    return second


def report_days(out, period=layout.Daily):
    return sorted(p.name for p in out.iterdir() if p.is_dir() and period.pattern.match(p.name)) if out.is_dir() else []


def mark_missed(out, today, why, period=layout.Daily):
    """Write MISSED for every day but today that has no finished report.

    That is each day between the last earlier report and today that has no
    directory, and each
    other day (earlier, or later after a clock that ran ahead) whose run
    stopped before writing RUN.json, which would otherwise fail
    check_reports on every later run. Today's is replaced by today's run.
    In the rehearsal set, the same for every 10-minute slot. The on-demand
    set is not continuous: nothing is marked, and an earlier run that stopped
    before it finished (never committed: only a finished run is) is removed.
    """
    days = report_days(out, period)
    now = period.name(today)
    if not days:
        return []
    if not period.continuous:
        for name in days:
            d = out / name
            if name != now and not (d / "RUN.json").exists() and not (d / "MISSED").exists():
                shutil.rmtree(d)
                print(f"{name}: removed, its run stopped before it finished")
        return []
    marked = []
    for name in days:
        d = out / name
        if name != now and not (d / "RUN.json").exists() and not (d / "MISSED").exists():
            shutil.rmtree(d)
            d.mkdir()
            (d / "MISSED").write_text(f"no report for {name}: its run stopped before it finished\n")
            marked.append(name)
    earlier = [name for name in days if name < now]
    if not earlier:
        return marked
    day = period.parse(earlier[-1]) + period.step
    while day < today:
        d = out / period.name(day)
        d.mkdir(parents=True)
        (d / "MISSED").write_text(f"no report for {period.name(day)}: {why}\n")
        marked.append(period.name(day))
        day += period.step
    return marked


def utc_now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def reporter_up(console, reporter, timeout):
    """The console answers, and the plugin behind it signs with the reporter key."""
    try:
        status, body = http("GET", console + LEDGER_ROUTE, None, timeout)
        if status != 200:
            raise ReporterDown(f"http {status} from {LEDGER_ROUTE}: {body.decode('utf-8', 'replace')[:300]}")
    except OSError as e:
        raise ReporterDown(str(e))
    try:
        signing = reporter_key.key_from_pem(json.loads(body)["node_pub_key_pem"])
    except (ValueError, KeyError, TypeError, AttributeError) as e:
        raise ValueError(f"{console}: the plugin names no signing key ({e!r})")
    if signing != reporter["key_id"]:
        raise ValueError(f"{console}: the plugin signs with {signing}, not the reporter key {reporter['key_id']}")


def run(args, now, sleep):
    period = layout.SETS[args.set]
    rehearsal = period is layout.Rehearsal
    ondemand = period is layout.OnDemand
    start = now()
    today = period.parse(args.date) if args.date else period.of(start)
    name = period.name(today)
    out = Path(args.out)
    reporter = load_reporter(args.reporter, args.data_dir)
    schedule = retry_schedule(reporter, args.reporter)
    timeout, time_left = args.timeout, None
    if args.set in BOUNDED:
        # Bounded so the run settles inside its slot (or, on demand, in about
        # two minutes from its start), whatever reporter.json says.
        schedule, cap, budget = BOUNDED[args.set]
        timeout = min(args.timeout, cap)
        deadline = today + dt.timedelta(seconds=budget)
        time_left = lambda: (deadline - now()).total_seconds()  # noqa: E731
    errs, _ = check_nodes.check_dir(Path(args.nodes))
    if errs:
        raise ValueError("the commitment list does not check:\n  " + "\n  ".join(errs))
    day_dir = out / name
    if (day_dir / "RUN.json").exists():
        raise ValueError(f"{day_dir} already holds a run")
    if ondemand and day_dir.exists():
        raise ValueError(f"{day_dir} already exists: one on-demand run per second")
    console = args.console.rstrip("/")
    second = self_node(args, reporter, console)
    for missed in mark_missed(out, today, f"the reporter did not run; noticed by the run of {name}", period):
        print(f"{missed}: MISSED")
    reporter_up(console, reporter, timeout)

    entries = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(args.nodes).glob("*.json"))]
    # The reporter's own node is asked only through a second console, never its own.
    entries = [e for e in entries if e["node_id"] != reporter["node_id"] or second]
    via = {reporter["node_id"]: second} if second else {}
    if day_dir.exists():
        (day_dir / "MISSED").unlink(missing_ok=True)
    day_dir.mkdir(parents=True, exist_ok=True)
    # A run that was cut short today left its attempts: they stand, and this
    # run asks only what they left unanswered. Anything else it left (files it
    # settled before stopping, a node no longer listed) is redone from them.
    listed = {e["node_id"] for e in entries}
    for left in sorted(day_dir.iterdir()):
        if left.is_dir() and left.name in listed:
            for f in left.iterdir():
                if f.name != layout.ATTEMPTS:
                    shutil.rmtree(f) if f.is_dir() else f.unlink()
        else:
            shutil.rmtree(left) if left.is_dir() else left.unlink()
    for entry in entries:
        (day_dir / entry["node_id"]).mkdir(exist_ok=True)
    for n, after in enumerate([0, *schedule], 1):
        owed = {e["node_id"]: layout.unanswered(day_dir / e["node_id"]) for e in entries}
        if not any(owed.values()):
            break
        if n > 1:
            wait = (start + dt.timedelta(seconds=after) - now()).total_seconds()
            if time_left is not None and time_left() - max(wait, 0) <= 0:
                print(f"no time left in the {period.noun} to ask {sum(1 for v in owed.values() if v)} node(s) again")
                break
            print(f"asking {sum(1 for v in owed.values() if v)} node(s) again in {max(wait, 0):.0f}s (attempt {n})")
            if wait > 0:
                sleep(wait)
        for entry in entries:
            if owed[entry["node_id"]]:
                ask_attempt(via.get(entry["node_id"], console), entry, day_dir / entry["node_id"],
                            owed[entry["node_id"]], now(), f"by=run pass={n}", timeout, time_left)
    lines = {}
    for entry in entries:
        lines[entry["node_id"]] = settle(day_dir / entry["node_id"])
        print(f"{entry['node_id']}: {lines[entry['node_id']]}")
    record = {
        "date": name,
        "reporter": {"node_id": reporter["node_id"], "key_id": reporter["key_id"]},
        "started": layout.iso(start),
        "finished": layout.iso(now()),
        "questions": [QUESTION_NAMES[s] for s in QUESTIONS],
        "asked": sorted(lines),
        "versions_lock_sha256": versions_lock_sha256(),
    }
    if ondemand:
        record["set"] = period.set
    asked_via = sorted(n for n in via if n in lines)
    if asked_via:
        record["asked_through_second_console"] = asked_via
    again = ", ".join(f"+{s:g}s" for s in schedule) or "never"
    title = (f"# Rehearsal report {name} (TEST)" if rehearsal else f"# On-demand report {name}" if ondemand
             else f"# Daily report {name}")
    readme = [title, "",
              f"Reporter `{reporter['node_id']}` (key `{reporter['key_id']}`) asked each node in `nodes/`"
              " `checkpoints` and `history_card/1`. Nodes are listed by node id; the order means nothing.",
              f"A question with no answer was asked again at {again} after the run started;"
              " each node's `attempts/` holds every time it was asked.", ""]
    if ondemand:
        readme += ["A run made by hand between the daily runs; it is never counted as a day.", ""]
    for node_id, line in sorted(lines.items()):
        n = layout.absent_days_running(out, node_id, today, period)
        fact = layout.absence_fact(n, period.noun) if ondemand else layout.absence_fact(n)
        if node_id in via:
            fact = "; ".join(x for x in ("the reporter's own node, asked through a second node's console", fact) if x)
        readme.append(f"- `{node_id}`: {line}" + (f"; {fact}" if fact else ""))
    if not lines:
        readme.append("- no node is listed")
    write_new(day_dir / "README.md", "\n".join(readme) + "\n")
    # RUN.json marks the day finished, so it goes last and appears whole: a run
    # stopped before this rename leaves no RUN.json, and the next run resumes it.
    tmp = day_dir / "RUN.json.partial"
    tmp.write_text(json.dumps(record, indent=2) + "\n")
    tmp.replace(day_dir / "RUN.json")
    return 0


def ask(args, now):
    """Ask again, after the day's run: one node, or every node still owed an answer today."""
    today = dt.date.fromisoformat(args.date) if args.date else now().date()
    day_dir = Path(args.out) / today.isoformat()
    if not (day_dir / "RUN.json").is_file():
        raise ValueError(f"{day_dir}: no settled run to ask again after (the run asks again on its own schedule)")
    asked = layout.run_record(day_dir)["asked"]
    if args.node is not None and args.node not in asked:
        raise ValueError(f"{args.node} was not asked on {today.isoformat()}")
    targets = [args.node] if args.node is not None else [n for n in asked if layout.unanswered(day_dir / n)]
    listed = {}
    for p in sorted(Path(args.nodes).glob("*.json")):
        e = json.loads(p.read_text(encoding="utf-8"))
        listed[e["node_id"]] = e
    missing = [n for n in targets if n not in listed]
    if missing:
        raise ValueError(f"not on the commitment list any more: {', '.join(missing)}")
    reporter = load_reporter(args.reporter, args.data_dir)
    console = args.console.rstrip("/")
    second = self_node(args, reporter, console)
    via = {reporter["node_id"]: second} if second else {}
    reporter_up(console, reporter, args.timeout)
    if not targets:
        print(f"{today.isoformat()}: every node has answered every question; nothing to ask")
    for node_id in targets:
        stems = layout.unanswered(day_dir / node_id)
        if not stems:
            print(f"{node_id}: answered every question today; nothing to ask")
            continue
        lines = ask_attempt(via.get(node_id, console), listed[node_id], day_dir / node_id, stems, now(), "by=ask",
                            args.timeout)
        print(f"{node_id}: " + "; ".join(f"{QUESTION_NAMES[s]}: {l}" for s, l in lines.items()))
    return 0


def prove(args):
    day_dir = Path(args.out) / args.date
    passing = []
    for node_dir in sorted(p for p in day_dir.iterdir() if p.is_dir()) if day_dir.is_dir() else []:
        cp = (node_dir / "checkpoints.verification").read_text().split() or ["missing"]
        hc = (node_dir / "history_card.verification").read_text().split() or ["missing"]
        count = next((int(t.split("=", 1)[1]) for t in cp if re.match(r"^checkpoints=\d+$", t)), 0)
        ok = cp[0] == "verified" and count >= 1 and hc[0] == "verified"
        print(f"{node_dir.name}: {'yes' if ok else 'no'} (checkpoints: {' '.join(cp[:2])}; history_card/1: {hc[0]})")
        if ok:
            passing.append(node_dir.name)
    print(f"day-one proof {'holds' if len(passing) >= 2 else 'does not hold'} for {args.date}")
    return 0 if len(passing) >= 2 else 1


def self_options(p):
    p.add_argument("--self-node", default=os.environ.get("LOOP_SELF_NODE") or None,
                   help="the reporter's label in --population; it must be reporter.json's node")
    p.add_argument("--second-console", default=os.environ.get("LOOP_SECOND_CONSOLE") or None,
                   help="another node's console, to ask the reporter's own node through")
    p.add_argument("--population", default=DEFAULT_POPULATION, help="where --self-node is looked up")


def main(argv, now=utc_now, sleep=time.sleep):
    """`now` and `sleep` are the clock: a test passes its own, so no run waits for real."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--set", choices=sorted(layout.SETS), default=os.environ.get("LOOP_REPORT_SET") or "daily",
                   help="daily (default); rehearsal: one report per 10-minute slot, a test; "
                        "ondemand: one report per run made by hand")
    r.add_argument("--date", help="UTC date to report as (default: today, UTC); in rehearsal, the slot; "
                                  "on demand, the run's start <YYYY-MM-DD>T<HHMMSS>Z")
    r.add_argument("--console", default=DEFAULT_CONSOLE, help="the reporter node's mesh-llm console")
    r.add_argument("--nodes", default="nodes")
    r.add_argument("--out", help="default: reports/<set>")
    r.add_argument("--reporter", default="reporter/reporter.json")
    r.add_argument("--data-dir", help="the reporter's capsules data dir, to check its key is the committed one")
    r.add_argument("--timeout", type=float, default=60.0)
    self_options(r)
    a = sub.add_parser("ask", help="ask again after the day's run")
    which = a.add_mutually_exclusive_group(required=True)
    which.add_argument("--node", help="the node id to ask again")
    which.add_argument("--absent", action="store_true", help="every node with a question unanswered today")
    a.add_argument("--date", help="UTC date of the run to add to (default: today, UTC)")
    a.add_argument("--console", default=DEFAULT_CONSOLE, help="the reporter node's mesh-llm console")
    a.add_argument("--nodes", default="nodes")
    a.add_argument("--out", default="reports/daily")
    a.add_argument("--reporter", default="reporter/reporter.json")
    a.add_argument("--data-dir", help="the reporter's capsules data dir, to check its key is the committed one")
    a.add_argument("--timeout", type=float, default=60.0)
    self_options(a)
    p = sub.add_parser("prove")
    p.add_argument("--date", required=True)
    p.add_argument("--out", default="reports/daily")
    args = parser.parse_args(argv[1:])
    args.set = getattr(args, "set", "daily")
    period = layout.SETS[args.set]
    if args.out is None:
        args.out = f"reports/{period.set}"
    try:
        if args.cmd == "run":
            return run(args, now, sleep)
        return ask(args, now) if args.cmd == "ask" else prove(args)
    except ReporterDown as e:
        day = args.date or period.name(period.of(now()))
        d = Path(args.out) / day
        if args.cmd == "ask":
            print(f"{day}: not asked again: the reporter could not reach its own node: {e}", file=sys.stderr)
            return 2
        if not period.continuous:
            # No continuity, so nothing to mark: the run is simply not there.
            print(f"{day}: no on-demand run: the reporter could not reach its own node: {e}", file=sys.stderr)
            return 2
        if d.exists() and any(p.name != "MISSED" for p in d.iterdir()):
            # Attempts from a run cut short today stand; the day is not marked over them.
            print(f"{day}: the reporter could not reach its own node: {e}; earlier attempts kept", file=sys.stderr)
            return 2
        d.mkdir(parents=True, exist_ok=True)
        (d / "MISSED").write_text(f"no report for {day}: the reporter could not reach its own node: {e}\n")
        print(f"{day}: MISSED ({e})", file=sys.stderr)
        return 2
    except (OSError, ValueError) as e:
        print(e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
