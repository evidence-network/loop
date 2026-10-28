"""Read the scrubbed daily set (stats/data/daily/) into plain records for the stats site.

One file per day, `<YYYY-MM-DD>.json`, written by scripts/scrub_reports.py with
sorted keys, a 2-space indent and a trailing newline. It carries, per node per
day, each question's settled state, its attempts, the log size at checkpoint
and the SHA-256 of the settled answer: no answer, request or key bytes. The
states are scripts/report_layout.py's (DAY_STATES, ATTEMPT_STATES).

This module is strict: a file that is not that format exactly (an unknown or
missing key, a state it does not know, an answer hash on an absence, a file
name that is not its date) is a StatsError, so the site build fails instead of
showing a guess. Standard library only.
"""
import datetime as dt
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import report_layout as layout  # noqa: E402

HEX64 = re.compile(r"^[0-9a-f]{64}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
DAY_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.json$")
SKIP = {".gitkeep", "README.md"}
# Key -> the question as the reporter names it.
QUESTIONS = {"checkpoints": "checkpoints", "history_card": "history_card/1"}
DAY_KEYS = {"date", "run", "missed_reason", "versions_lock_sha256", "reporter_key_id", "listed", "nodes"}
NODE_KEYS = {"node_id", "node_line", "absent_days_running", "left_on", "questions"}
QUESTION_KEYS = {"state", "attempts", "log_size_at_checkpoint", "answer_sha256"}
ATTEMPT_KEYS = {"at", "state"}
LOG_SIZE_KEYS = {"mmr_size", "timestamp"}


class StatsError(Exception):
    """A scrubbed file the site cannot read. Carries every problem found."""

    def __init__(self, problems):
        self.problems = problems
        super().__init__("\n".join(problems))


@dataclass
class Attempt:
    at: str
    state: str  # one of layout.ATTEMPT_STATES


@dataclass
class Answer:
    stem: str
    state: str  # one of layout.DAY_STATES
    attempts: list  # Attempt, earliest first
    log_size: dict | None  # {"mmr_size", "timestamp"}
    answer_sha256: str | None  # None exactly when the state is absence


@dataclass
class Node:
    node_id: str
    node_line: str
    absent_running: int
    left_on: str | None
    answers: dict  # stem -> Answer


@dataclass
class Day:
    date: str
    run: str  # settled | missed
    missed_reason: str | None
    versions_lock_sha256: str | None
    reporter_key_id: str | None
    listed: int
    nodes: list  # Node, by node id
    file: str  # the file's name under the data directory

    @property
    def missed(self):
        return self.run == "missed"


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool) and v >= 0


def _keys(obj, want, where, problems):
    if not isinstance(obj, dict):
        problems.append(f"{where}: not an object")
        return False
    if set(obj) != want:
        extra, missing = sorted(set(obj) - want), sorted(want - set(obj))
        problems.append(f"{where}: keys differ from the format"
                        + (f"; unexpected {', '.join(extra)}" if extra else "")
                        + (f"; missing {', '.join(missing)}" if missing else ""))
        return False
    return True


def _hex_or_null(v):
    return v is None or (isinstance(v, str) and HEX64.match(v))


def read_answer(stem, q, where, problems):
    n = len(problems)
    if not _keys(q, QUESTION_KEYS, where, problems):
        return None
    if q["state"] not in layout.DAY_STATES:
        problems.append(f"{where}: state {q['state']!r} is not one of {', '.join(layout.DAY_STATES)}")
    attempts = q["attempts"]
    if not isinstance(attempts, list) or not attempts:
        problems.append(f"{where}: attempts must be a non-empty list")
        attempts = []
    tried = []
    for i, a in enumerate(attempts):
        w = f"{where}.attempts[{i}]"
        if not _keys(a, ATTEMPT_KEYS, w, problems):
            continue
        if not (isinstance(a["at"], str) and STAMP.match(a["at"])):
            problems.append(f"{w}: at is not a UTC timestamp")
        elif a["state"] not in layout.ATTEMPT_STATES:
            problems.append(f"{w}: state {a['state']!r} is not one of {', '.join(layout.ATTEMPT_STATES)}")
        else:
            tried.append(Attempt(a["at"], a["state"]))
    if [a.at for a in tried] != sorted(a.at for a in tried):
        problems.append(f"{where}: attempts are not earliest first")
    # The settled state is the first attempt that got an answer, or absence when none did.
    first = next((a.state for a in tried if a.state in layout.ANSWERED), "absence")
    if len(tried) == len(attempts) and tried and q["state"] != first:
        problems.append(f"{where}: state {q['state']!r} but the attempts settle as {first!r}")
    size = q["log_size_at_checkpoint"]
    if size is not None and _keys(size, LOG_SIZE_KEYS, f"{where}.log_size_at_checkpoint", problems):
        if not (_int(size["mmr_size"]) and isinstance(size["timestamp"], str) and STAMP.match(size["timestamp"])):
            problems.append(f"{where}.log_size_at_checkpoint: needs mmr_size=<n> and a UTC timestamp")
    digest = q["answer_sha256"]
    if not _hex_or_null(digest):
        problems.append(f"{where}: answer_sha256 is not 64 hex or null")
    elif (digest is None) != (q["state"] == "absence"):
        problems.append(f"{where}: answer_sha256 must be null exactly when the state is absence")
    if len(problems) > n:
        return None
    return Answer(stem, q["state"], tried, size, digest)


def read_node(node, where, problems):
    n = len(problems)
    if not _keys(node, NODE_KEYS, where, problems):
        return None
    if not (isinstance(node["node_id"], str) and HEX64.match(node["node_id"])):
        problems.append(f"{where}: node_id is not 64 hex")
        return None
    where = f"{where} ({node['node_id'][:12]})"
    if not isinstance(node["node_line"], str) or not node["node_line"].strip():
        problems.append(f"{where}: node_line must be a non-empty string")
    left = node["left_on"]
    if left is not None and not (isinstance(left, str) and DATE.match(left)):
        problems.append(f"{where}: left_on is not a YYYY-MM-DD date or null")
    answers = {}
    if _keys(node["questions"], set(QUESTIONS), f"{where}.questions", problems):
        for stem in QUESTIONS:
            a = read_answer(stem, node["questions"][stem], f"{where}.{stem}", problems)
            if a:
                answers[stem] = a
    running = node["absent_days_running"]
    if not _int(running):
        problems.append(f"{where}: absent_days_running is not a whole number")
    elif len(answers) == len(QUESTIONS):
        absent = all(a.state == "absence" for a in answers.values())
        if absent != (running > 0):
            problems.append(f"{where}: absent_days_running is {running} but the node "
                            + ("answered nothing" if absent else "answered") + " this day")
    if len(problems) > n:
        return None
    return Node(node["node_id"], node["node_line"].strip(), running, left, answers)


def canonical(obj):
    """The bytes the format fixes: sorted keys, a 2-space indent, a trailing newline."""
    return [(json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=a) + "\n").encode("utf-8") for a in (True, False)]


def read_day(path, problems):
    where = path.name
    n = len(problems)
    m = DAY_FILE.match(path.name)
    if not m:
        problems.append(f"{where}: not a <YYYY-MM-DD>.json day file")
        return None
    raw = path.read_bytes()
    try:
        d = json.loads(raw)
    except ValueError as err:
        problems.append(f"{where}: not JSON ({err})")
        return None
    if not _keys(d, DAY_KEYS, where, problems):
        return None
    if raw not in canonical(d):
        problems.append(f"{where}: not in the fixed form (sorted keys, 2-space indent, trailing newline)")
    if d["date"] != m.group(1):
        problems.append(f"{where}: date is {d['date']!r}, the file name says {m.group(1)}")
    try:
        dt.date.fromisoformat(m.group(1))
    except ValueError:
        problems.append(f"{where}: {m.group(1)} is not a date")
    if d["run"] not in ("settled", "missed"):
        problems.append(f"{where}: run must be settled or missed")
    if (d["run"] == "missed") != isinstance(d["missed_reason"], str) or d["missed_reason"] == "":
        problems.append(f"{where}: missed_reason is a non-empty string exactly when the run is missed")
    for key in ("versions_lock_sha256", "reporter_key_id"):
        if not _hex_or_null(d[key]):
            problems.append(f"{where}: {key} is not 64 hex or null")
    if not _int(d["listed"]):
        problems.append(f"{where}: listed is not a whole number")
    nodes = []
    if not isinstance(d["nodes"], list):
        problems.append(f"{where}: nodes is not a list")
    else:
        if d["run"] == "missed" and d["nodes"]:
            problems.append(f"{where}: a missed day has no nodes")
        for i, node in enumerate(d["nodes"]):
            got = read_node(node, f"{where}: nodes[{i}]", problems)
            if got:
                nodes.append(got)
        ids = [x.node_id for x in nodes]
        if ids != sorted(set(ids)):
            problems.append(f"{where}: nodes are not sorted by node_id, or one is given twice")
        if _int(d["listed"]) and len(d["nodes"]) > d["listed"]:
            problems.append(f"{where}: {len(d['nodes'])} nodes but only {d['listed']} listed")
    if len(problems) > n:
        return None
    return Day(d["date"], d["run"], d["missed_reason"], d["versions_lock_sha256"], d["reporter_key_id"],
               d["listed"], nodes, path.name)


def load(root):
    """Every day under root, newest first. Raises StatsError on anything unreadable."""
    root = Path(root)
    if not root.is_dir():
        raise StatsError([f"{root}: no such directory"])
    problems, days = [], []
    for path in sorted(root.iterdir(), reverse=True):
        if path.name in SKIP:
            continue
        if not path.is_file() or path.is_symlink():
            problems.append(f"{path.name}: not a day file")
            continue
        day = read_day(path, problems)
        if day:
            days.append(day)
    if problems:
        raise StatsError(problems)
    return days


def calendar(days):
    """Every date from the first day to the last, earliest first: (date, Day or None)."""
    if not days:
        return []
    by_date = {d.date: d for d in days}
    first, last = (dt.date.fromisoformat(x) for x in (min(by_date), max(by_date)))
    out = []
    while first <= last:
        out.append((first.isoformat(), by_date.get(first.isoformat())))
        first += dt.timedelta(days=1)
    return out


def left_on(days):
    """node id -> the date its newest record says it left, for each node that left."""
    out = {}
    for day in sorted(days, key=lambda d: d.date):
        for node in day.nodes:
            if node.left_on:
                out[node.node_id] = node.left_on
    return out
