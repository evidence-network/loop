#!/usr/bin/env python3
"""The daily reporter: ask every listed node the two stranger-safe questions.

Usage:
  daily_report.py run   [--date D] [--console URL] [--nodes DIR] [--out DIR]
                        [--reporter FILE] [--data-dir DIR] [--timeout S]
  daily_report.py prove --date D [--out DIR]

`run` asks each node in nodes/ `checkpoints` and `history_card/1` over the mesh
evidence request, through the reporter node's own capsules plugin
(POST <console>/api/plugins/capsules/tools/mesh_evidence_request), and writes
what came back under <out>/<date>/<node_id>/. It asks those two and nothing else.
The plugin signs each request with the reporter's announced key and checks the
answer under the key the reporter's host announces for the node (mesh-llm
plugin key announcement; the reporter node sets no CAPSULES_PEER_KEYS map, so
no key comes from static configuration); this script records that check, it
does not repeat it.

Per question Q (`checkpoints`, `history_card`):
  Q.request.json     the request map handed to the plugin
  Q.response.json    the plugin's reply, the exact bytes received: the node's
                     answer (artifact or signed refusal) beside its verification
  Q.absence.txt      instead of a response, when nothing came back: why
  Q.verification     one line: verified | no_checkpoint_yet | refusal |
                     no_announced_key | not_evidence | absence, then key=value
                     details. no_checkpoint_yet is a signed coverage_unsatisfiable:
                     the node has cut no checkpoint yet (its first comes 300 s
                     after activity). It is neither a refusal nor an absence.
  peer-key.json      the key the reporter's node judged the answers under
  NODE               "discloses: ...", "joined, no checkpoint yet" or
                     "joined, discloses nothing"

A day with no run is never silent: `run` first writes <out>/<day>/MISSED for
each day since the last report that has none, and a run that cannot reach its
own node writes MISSED for today and exits 2. No score, no ranking, no
aggregate: each node's files say what that node answered, and nothing is
totalled across nodes.

`prove` is the day-one acceptance check, printed, never written to the repo:
exit 0 when at least two nodes answered `checkpoints` verified with at least
one checkpoint, and `history_card/1` verified.

Standard library only. Exit 0 = done, 1 = refused (bad input), 2 = missed day.
"""
import argparse
import datetime as dt
import json
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_nodes  # noqa: E402
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
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


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


def verification_line(status, body, error, announced, listed):
    """One line for one question: the state the plugin found, then details."""
    if error is not None:
        return f"absence why={quote(error)}"
    if status != 200:
        return f"absence why={quote(f'http {status}: ' + body.decode('utf-8', 'replace'))}"
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


def ask_node(console, entry, node_dir, timeout):
    """Ask one node both questions; write its files. Returns its NODE line."""
    node_id, listed = entry["node_id"], entry["key_id"]
    node_dir.mkdir(parents=True)
    announced = None
    try:
        status, body = http("GET", peer_key_url(console, node_id), None, timeout)
        (node_dir / "peer-key.json").write_bytes(body)
        if status == 200:
            reply = json.loads(body)
            key = reply.get("announced_key_id") if isinstance(reply, dict) else None
            announced = key if isinstance(key, str) and HEX64.match(key) else None
    except (OSError, ValueError) as e:
        (node_dir / "peer-key.json").write_text(json.dumps({"error": str(e)}) + "\n")
    disclosed, waiting = [], False
    for stem, request in QUESTIONS.items():
        (node_dir / f"{stem}.request.json").write_text(json.dumps(request, indent=2, sort_keys=True) + "\n")
        status, body, error = None, b"", None
        try:
            status, body = http("POST", console + TOOL_ROUTE, {"peer_id": node_id, "request": request}, timeout)
        except OSError as e:
            error = f"no reply from the reporter's node within {timeout}s: {e}"
        if error is None and status == 200:
            (node_dir / f"{stem}.response.json").write_bytes(body)
        else:
            (node_dir / f"{stem}.absence.txt").write_text(
                (error or f"http {status}\n{body.decode('utf-8', 'replace')}").rstrip("\n") + "\n"
            )
        line = verification_line(status, body, error, announced, listed)
        (node_dir / f"{stem}.verification").write_text(line + "\n")
        if line.startswith("verified "):
            disclosed.append(QUESTION_NAMES[stem])
        waiting = waiting or line.startswith("no_checkpoint_yet ")
    if disclosed:
        node_line = f"discloses: {', '.join(disclosed)}"
    else:
        node_line = "joined, no checkpoint yet" if waiting else "joined, discloses nothing"
    (node_dir / "NODE").write_text(node_line + "\n")
    return node_line


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


def report_days(out):
    return sorted(p.name for p in out.iterdir() if p.is_dir() and DATE.match(p.name)) if out.is_dir() else []


def mark_missed(out, today, why):
    """Write MISSED for every earlier day with no finished report.

    That is each day since the last report that has no directory, and each
    earlier day whose run stopped before writing RUN.json, which would
    otherwise fail check_reports on every later run.
    """
    days = report_days(out)
    if not days:
        return []
    marked = []
    for name in days:
        d = out / name
        if name < today.isoformat() and not (d / "RUN.json").exists() and not (d / "MISSED").exists():
            shutil.rmtree(d)
            d.mkdir()
            (d / "MISSED").write_text(f"no report for {name}: its run stopped before it finished\n")
            marked.append(name)
    day = dt.date.fromisoformat(days[-1]) + dt.timedelta(days=1)
    while day < today:
        d = out / day.isoformat()
        d.mkdir(parents=True)
        (d / "MISSED").write_text(f"no report for {day.isoformat()}: {why}\n")
        marked.append(day.isoformat())
        day += dt.timedelta(days=1)
    return marked


def run(args):
    today = dt.date.fromisoformat(args.date) if args.date else dt.datetime.now(dt.timezone.utc).date()
    out = Path(args.out)
    reporter = load_reporter(args.reporter, args.data_dir)
    errs, _ = check_nodes.check_dir(Path(args.nodes))
    if errs:
        raise ValueError("the commitment list does not check:\n  " + "\n  ".join(errs))
    day_dir = out / today.isoformat()
    if (day_dir / "RUN.json").exists():
        raise ValueError(f"{day_dir} already holds a run")
    for missed in mark_missed(out, today, f"the reporter did not run; noticed by the run of {today.isoformat()}"):
        print(f"{missed}: MISSED")
    # No RUN.json here (checked above): what is left is a MISSED mark or a run
    # that stopped midway. This run replaces it.
    if day_dir.exists():
        shutil.rmtree(day_dir)
    console = args.console.rstrip("/")
    started = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        status, body = http("GET", console + LEDGER_ROUTE, None, args.timeout)
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

    entries = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(args.nodes).glob("*.json"))]
    entries = [e for e in entries if e["node_id"] != reporter["node_id"]]
    day_dir.mkdir(parents=True)
    lines = {}
    for entry in entries:
        lines[entry["node_id"]] = ask_node(console, entry, day_dir / entry["node_id"], args.timeout)
        print(f"{entry['node_id']}: {lines[entry['node_id']]}")
    finished = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = {
        "date": today.isoformat(),
        "reporter": {"node_id": reporter["node_id"], "key_id": reporter["key_id"]},
        "started": started,
        "finished": finished,
        "questions": [QUESTION_NAMES[s] for s in QUESTIONS],
        "asked": sorted(lines),
    }
    (day_dir / "RUN.json").write_text(json.dumps(record, indent=2) + "\n")
    readme = [f"# Daily report {today.isoformat()}", "",
              f"Reporter `{reporter['node_id']}` (key `{reporter['key_id']}`) asked each node in `nodes/`"
              " `checkpoints` and `history_card/1`. Nodes are listed by node id; the order means nothing.", ""]
    readme += [f"- `{node_id}`: {line}" for node_id, line in sorted(lines.items())] or ["- no node is listed"]
    (day_dir / "README.md").write_text("\n".join(readme) + "\n")
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


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--date", help="UTC date to report as (default: today, UTC)")
    r.add_argument("--console", default=DEFAULT_CONSOLE, help="the reporter node's mesh-llm console")
    r.add_argument("--nodes", default="nodes")
    r.add_argument("--out", default="reports/daily")
    r.add_argument("--reporter", default="reporter/reporter.json")
    r.add_argument("--data-dir", help="the reporter's capsules data dir, to check its key is the committed one")
    r.add_argument("--timeout", type=float, default=60.0)
    p = sub.add_parser("prove")
    p.add_argument("--date", required=True)
    p.add_argument("--out", default="reports/daily")
    args = parser.parse_args(argv[1:])
    try:
        return run(args) if args.cmd == "run" else prove(args)
    except ReporterDown as e:
        day = args.date or dt.datetime.now(dt.timezone.utc).date().isoformat()
        d = Path(args.out) / day
        d.mkdir(parents=True, exist_ok=True)
        (d / "MISSED").write_text(f"no report for {day}: the reporter could not reach its own node: {e}\n")
        print(f"{day}: MISSED ({e})", file=sys.stderr)
        return 2
    except (OSError, ValueError) as e:
        print(e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
