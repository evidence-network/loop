"""Read reports/daily/ into plain records for the report page.

The report layout is scripts/report_layout.py's (reports/README.md, as
scripts/daily_report.py writes it): this module reads a node's day through it
(`attempts()`, `answers()`, `absent_days_running()`) and parses no layout of
its own. Before reading, it runs scripts/check_reports.py over the whole tree,
so the page refuses exactly what the reports check refuses. On top of that it
is strict about what the page shows: a result line it cannot read, a
`verified` line without its checkpoint count and anchor, or over a response
that is not an artifact. Anything it does not recognise is a ReportError, so
the page build fails instead of showing a guess.

Per question, the page shows the day so far, from the attempts: the answer of
the first attempt that got one (a later ask-again included), every attempt
that asked it, or, when none got an answer, the day's absence over all of
them. Leave marks (nodes/left/<node_id>.json, scripts/check_nodes.py) give
"left on <date>".

Standard library only.
"""
import datetime as dt
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_nodes  # noqa: E402
import check_reports  # noqa: E402
import report_layout as layout  # noqa: E402

HEX64 = re.compile(r"^[0-9a-f]{64}$")
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
# File stem -> the question as the reporter names it.
QUESTIONS = {"checkpoints": "checkpoints", "history_card": "history_card/1"}
OUTCOMES = layout.DAY_STATES
TOKEN = re.compile(r'\s+(\w+)=("(?:[^"\\]|\\.)*"|\S+)')
SKIP = {".gitkeep", "README.md"}


class ReportError(Exception):
    """A report the page cannot read. Carries every problem found."""

    def __init__(self, problems):
        self.problems = problems
        super().__init__("\n".join(problems))


@dataclass
class Attempt:
    """One ask of one question: when, by the run (`run pass=<n>`) or an ask-again (`ask`), and its result."""

    at: str
    by: str
    outcome: str  # one of layout.ATTEMPT_STATES
    line: str
    details: dict
    files: dict  # role -> path relative to the reports root: request, response|absence, verification


@dataclass
class Answer:
    stem: str
    question: str
    outcome: str  # one of OUTCOMES
    line: str  # the answering attempt's .verification line, or the day's absence line over every attempt
    details: dict  # its key=value pairs, quoted values unquoted
    attempts: list  # every Attempt that asked this question, earliest first
    files: dict  # the answering attempt's files; {} for an absence (each attempt has its own)
    asked_again: bool = False  # the answer came from an ask-again after the run
    checkpoints: int | None = None
    anchor: str | None = None
    epoch: str | None = None  # the answer format (evidence-interop/0) has no epoch field; kept for the page's column


@dataclass
class Node:
    node_id: str
    node_line: str
    announced_key: str | None
    answers: dict  # stem -> Answer
    files: dict  # NODE, peer-key (the latest attempt's) -> relative path
    absent_running: int = 0  # layout.absent_days_running: days, ending this one, it answered nothing


@dataclass
class Day:
    date: str
    missed: str | None = None  # the MISSED text, when there was no run
    run: dict | None = None
    nodes: list = field(default_factory=list)  # by node id
    files: dict = field(default_factory=dict)

    @property
    def listed(self):
        """The nodes the run asked: every node in nodes/ but the reporter's own."""
        return len(self.nodes)


@dataclass
class Left:
    node_id: str
    date: str
    file: str  # the leave mark, relative to the nodes/ directory


def parse_line(line, states=OUTCOMES):
    """(outcome, details) of a .verification line, or raise ValueError."""
    outcome, _, rest = line.partition(" ")
    if outcome not in states:
        raise ValueError(f"unknown outcome {outcome!r}")
    details, pos, rest = {}, 0, (" " + rest) if rest else ""
    while pos < len(rest):
        m = TOKEN.match(rest, pos)
        if not m:
            raise ValueError(f"cannot read {rest[pos:].strip()!r}")
        key, value = m.groups()
        if key in details:
            raise ValueError(f"{key} given twice")
        try:
            details[key] = json.loads(value) if value.startswith('"') else value
        except ValueError:
            raise ValueError(f"cannot read {value!r}") from None
        pos = m.end()
    return outcome, details


def read_attempt(root, attempt_dir, stem, line, problems):
    where = f"{attempt_dir.relative_to(root).as_posix()}/{stem}"
    rel = lambda name: (attempt_dir / name).relative_to(root).as_posix()  # noqa: E731
    at, by = layout.read_attempt(attempt_dir)
    try:
        outcome, details = parse_line(line, layout.ATTEMPT_STATES)
    except ValueError as e:
        problems.append(f"{where}.verification: {e}")
        return None
    files = {"request": rel(f"{stem}.request.json")}
    for role, name in (("response", f"{stem}.response.json"), ("absence", f"{stem}.absence.txt")):
        if (attempt_dir / name).is_file():
            files[role] = rel(name)
    files["verification"] = rel(f"{stem}.verification")
    return Attempt(at, by, outcome, line, details, files)


def read_answer(root, node_dir, stem, day_so_far, problems):
    where = f"{node_dir.relative_to(root).as_posix()}/{stem}"
    tried = []
    for a, line in day_so_far["tried"]:
        attempt = read_attempt(root, a, stem, line, problems)
        if attempt is None:
            return None
        tried.append(attempt)
    if day_so_far["attempt"] is None:
        line = layout.absence_line(day_so_far["tried"])
        outcome, details = parse_line(line)
        return Answer(stem, QUESTIONS[stem], outcome, line, details, tried, {})
    a = day_so_far["attempt"]
    got = tried[[t for t, _ in day_so_far["tried"]].index(a)]
    answer = Answer(stem, QUESTIONS[stem], got.outcome, got.line, got.details, tried, got.files,
                    asked_again=got.by == "ask")
    try:
        json.loads((a / f"{stem}.request.json").read_bytes())
    except ValueError as e:
        problems.append(f"{where}: {stem}.request.json is not JSON ({e})")
    if answer.outcome == "verified":
        count, anchor = answer.details.get("checkpoints", ""), answer.details.get("anchor", "")
        if not (count.isdigit() and HEX64.match(anchor)):
            problems.append(f"{where}: a verified line needs checkpoints=<n> and anchor=<64 hex>")
        else:
            answer.checkpoints, answer.anchor = int(count), anchor
        try:
            state = json.loads((a / f"{stem}.response.json").read_bytes())["verification"]["state"]
        except (ValueError, KeyError, TypeError):
            state = None
        if state != "artifact":
            problems.append(f"{where}: the line says verified but the response's verification state is {state!r}")
    return answer


def read_node(root, day_dir, node_dir, problems):
    rel = lambda p: p.relative_to(root).as_posix()  # noqa: E731
    latest = layout.attempts(node_dir)[-1]
    announced = None
    try:
        body = json.loads((latest / "peer-key.json").read_bytes())
    except ValueError:
        body = None  # the reporter saves whatever the host sent; not knowing the key is a fact, not an error
    key = body.get("announced_key_id") if isinstance(body, dict) else None
    announced = key if isinstance(key, str) and HEX64.match(key) else None
    answers = {}
    for stem, day_so_far in layout.answers(node_dir).items():
        a = read_answer(root, node_dir, stem, day_so_far, problems)
        if a:
            answers[stem] = a
    node_line = (node_dir / "NODE").read_text(encoding="utf-8").strip()
    running = layout.absent_days_running(root, node_dir.name, dt.date.fromisoformat(day_dir.name))
    return Node(node_dir.name, node_line, announced, answers,
                {"NODE": rel(node_dir / "NODE"), "peer-key": rel(latest / "peer-key.json")}, running)


def read_day(root, day_dir, problems):
    day = Day(day_dir.name)
    if (day_dir / "MISSED").is_file():
        day.missed = (day_dir / "MISSED").read_text(encoding="utf-8").strip()
        day.files["MISSED"] = (day_dir / "MISSED").relative_to(root).as_posix()
        return day
    try:
        run = layout.run_record(day_dir)
        asked = run["asked"]
        window = (run["started"], run["finished"])
        if not (isinstance(asked, list) and all(isinstance(n, str) and HEX64.match(n) for n in asked)):
            raise ValueError("asked is not a list of node ids")
        if not all(isinstance(t, str) and STAMP.match(t) for t in window):
            raise ValueError("started/finished are not UTC timestamps")
    except (OSError, ValueError, KeyError, TypeError) as e:
        problems.append(f"{day.date}: RUN.json unreadable ({e})")
        return day
    day.run = run
    day.files["RUN.json"] = (day_dir / "RUN.json").relative_to(root).as_posix()
    for name in sorted(asked):
        day.nodes.append(read_node(root, day_dir, day_dir / name, problems))
    return day


def load(root):
    """Every day under root, newest first. Raises ReportError on anything unreadable."""
    root = Path(root)
    if not root.is_dir():
        raise ReportError([f"{root}: no such directory"])
    problems, _ = check_reports.check(root)
    if problems:
        raise ReportError(problems)
    days = [read_day(root, p, problems) for p in sorted(root.iterdir(), reverse=True) if p.name not in SKIP]
    if problems:
        raise ReportError(problems)
    return days


def load_left(left):
    """The leave marks under `left` (nodes/left/), by date then node id. A missing directory is none."""
    left = Path(left)
    problems = check_nodes.check_left(left, set())
    if problems:
        raise ReportError(problems)
    marks = []
    for path in sorted(left.glob("*.json")) if left.is_dir() else []:
        mark = json.loads(path.read_text(encoding="utf-8"))
        marks.append(Left(mark["node_id"], mark["left"], f"left/{path.name}"))
    return sorted(marks, key=lambda m: (m.date, m.node_id))
