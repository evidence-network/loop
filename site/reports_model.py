"""Read reports/daily/ into plain records for the report page.

This is the only module that knows the report layout (reports/README.md, as
scripts/daily_report.py writes it). The page renders what this returns and
nothing else. It is strict: anything it does not recognise is a ReportError,
so the page build fails instead of showing a guess.

Layout read here, per <YYYY-MM-DD>/:
  MISSED                       no run that day, and why
  RUN.json, README.md          a run; one <node_id>/ per node it asked
  <node_id>/NODE               "discloses: ...", "joined, no checkpoint yet",
                               "joined, discloses nothing"
  <node_id>/peer-key.json      the key the answers were judged under
  <node_id>/<q>.request.json   what was asked
  <node_id>/<q>.response.json  the reply bytes, or
  <node_id>/<q>.absence.txt    why nothing came back
  <node_id>/<q>.verification   one line: <outcome> key=value ...

The current layout records one attempt per question, inside the run's
started..finished window. Attempt is its own record so that a layout with one
file per attempt only changes how attempts are read, here.

Standard library only.
"""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
# File stem -> the question as the reporter names it.
QUESTIONS = {"checkpoints": "checkpoints", "history_card": "history_card/1"}
OUTCOMES = ("verified", "no_checkpoint_yet", "refusal", "no_announced_key", "not_evidence", "absence")
NODE_LINE = re.compile(
    r"^(joined, discloses nothing|joined, no checkpoint yet"
    r"|discloses: (checkpoints|history_card/1)(, history_card/1)?)$"
)
TOKEN = re.compile(r'\s+(\w+)=("(?:[^"\\]|\\.)*"|\S+)')
DAY_FILES = {"RUN.json", "README.md"}
SKIP = {".gitkeep", "README.md"}


class ReportError(Exception):
    """A report the page cannot read. Carries every problem found."""

    def __init__(self, problems):
        self.problems = problems
        super().__init__("\n".join(problems))


@dataclass
class Attempt:
    """One ask of one question. `at` is when, or None when the layout only
    gives the run's window (`window`)."""

    at: str | None
    window: tuple[str, str] | None


@dataclass
class Answer:
    stem: str
    question: str
    outcome: str
    line: str  # the .verification line, exactly
    details: dict  # its key=value pairs, quoted values unquoted
    attempts: list
    files: dict  # role -> path relative to the reports root: request, response|absence, verification
    checkpoints: int | None = None
    anchor: str | None = None
    epoch: str | None = None  # the answer format (evidence-interop/0) has no epoch field; kept for the page's column


@dataclass
class Node:
    node_id: str
    node_line: str
    announced_key: str | None
    answers: dict  # stem -> Answer
    files: dict  # NODE, peer-key.json -> relative path


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


def parse_line(line):
    """(outcome, details) of a .verification line, or raise ValueError."""
    outcome, _, rest = line.partition(" ")
    if outcome not in OUTCOMES:
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


def read_answer(root, day_dir, node_dir, stem, window, problems):
    where = f"{day_dir.name}/{node_dir.name}/{stem}"
    rel = lambda name: (node_dir / name).relative_to(root).as_posix()  # noqa: E731
    files = {}
    if not (node_dir / f"{stem}.request.json").is_file():
        problems.append(f"{where}: missing {stem}.request.json")
    else:
        files["request"] = rel(f"{stem}.request.json")
        try:
            json.loads((node_dir / f"{stem}.request.json").read_bytes())
        except ValueError as e:
            problems.append(f"{where}: {stem}.request.json is not JSON ({e})")
    answered = (node_dir / f"{stem}.response.json").is_file()
    absent = (node_dir / f"{stem}.absence.txt").is_file()
    if answered == absent:
        problems.append(f"{where}: needs exactly one of {stem}.response.json or {stem}.absence.txt")
    if answered:
        files["response"] = rel(f"{stem}.response.json")
    if absent:
        files["absence"] = rel(f"{stem}.absence.txt")
    path = node_dir / f"{stem}.verification"
    if not path.is_file():
        problems.append(f"{where}: missing {stem}.verification")
        return None
    files["verification"] = rel(f"{stem}.verification")
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) != 1:
        problems.append(f"{where}: {stem}.verification is not one line")
        return None
    try:
        outcome, details = parse_line(lines[0])
    except ValueError as e:
        problems.append(f"{where}: {stem}.verification: {e}")
        return None
    if (outcome == "absence") != absent:
        problems.append(f"{where}: the line says {outcome} but the answer is {'absent' if absent else 'present'}")
    answer = Answer(stem, QUESTIONS[stem], outcome, lines[0], details, [Attempt(None, window)], files)
    if outcome == "verified":
        count, anchor = details.get("checkpoints", ""), details.get("anchor", "")
        if not (count.isdigit() and HEX64.match(anchor)):
            problems.append(f"{where}: a verified line needs checkpoints=<n> and anchor=<64 hex>")
        else:
            answer.checkpoints, answer.anchor = int(count), anchor
        if answered:
            try:
                state = json.loads((node_dir / f"{stem}.response.json").read_bytes())["verification"]["state"]
            except (ValueError, KeyError, TypeError):
                state = None
            if state != "artifact":
                problems.append(f"{where}: the line says verified but the response's verification state is {state!r}")
    return answer


def read_node(root, day_dir, node_dir, window, problems):
    where = f"{day_dir.name}/{node_dir.name}"
    allowed = {"NODE", "peer-key.json"}
    for stem in QUESTIONS:
        allowed |= {f"{stem}.{s}" for s in ("request.json", "response.json", "absence.txt", "verification")}
    extra = sorted(p.name for p in node_dir.iterdir() if p.name not in allowed)
    if extra:
        problems.append(f"{where}: unexpected file(s) {', '.join(extra)}")
    node_path = node_dir / "NODE"
    node_line = node_path.read_text(encoding="utf-8").strip() if node_path.is_file() else ""
    if not NODE_LINE.match(node_line):
        problems.append(f"{where}: NODE is missing or not one of the known lines")
    announced = None
    key_path = node_dir / "peer-key.json"
    if not key_path.is_file():
        problems.append(f"{where}: missing peer-key.json")
    else:
        try:
            body = json.loads(key_path.read_bytes())
        except ValueError:
            body = None  # the reporter saves whatever the host sent; not knowing the key is a fact, not an error
        key = body.get("announced_key_id") if isinstance(body, dict) else None
        announced = key if isinstance(key, str) and HEX64.match(key) else None
    answers = {}
    for stem in QUESTIONS:
        a = read_answer(root, day_dir, node_dir, stem, window, problems)
        if a:
            answers[stem] = a
    rel = lambda name: (node_dir / name).relative_to(root).as_posix()  # noqa: E731
    return Node(node_dir.name, node_line, announced, answers, {"NODE": rel("NODE"), "peer-key": rel("peer-key.json")})


def read_day(root, day_dir, problems):
    day = Day(day_dir.name)
    names = {p.name for p in day_dir.iterdir()}
    if "MISSED" in names:
        text = (day_dir / "MISSED").read_text(encoding="utf-8").strip()
        if names != {"MISSED"} or not text:
            problems.append(f"{day.date}: a MISSED day holds only MISSED, saying why")
        day.missed = text
        day.files["MISSED"] = (day_dir / "MISSED").relative_to(root).as_posix()
        return day
    try:
        run = json.loads((day_dir / "RUN.json").read_bytes())
        asked = run["asked"]
        window = (run["started"], run["finished"])
        if run.get("date") != day.date:
            raise ValueError(f"date is {run.get('date')!r}")
        if not (isinstance(asked, list) and all(isinstance(n, str) and HEX64.match(n) for n in asked)):
            raise ValueError("asked is not a list of node ids")
        if not all(isinstance(t, str) and STAMP.match(t) for t in window):
            raise ValueError("started/finished are not UTC timestamps")
    except (OSError, ValueError, KeyError, TypeError) as e:
        problems.append(f"{day.date}: RUN.json unreadable ({e})")
        return day
    day.run = run
    day.files["RUN.json"] = (day_dir / "RUN.json").relative_to(root).as_posix()
    entries = names - DAY_FILES
    for name in sorted(entries):
        if not (HEX64.match(name) and (day_dir / name).is_dir()):
            problems.append(f"{day.date}: unexpected entry {name}")
    if sorted(n for n in entries if HEX64.match(n)) != sorted(asked):
        problems.append(f"{day.date}: node directories do not match RUN.json asked")
    for name in sorted(asked):
        if (day_dir / name).is_dir():
            day.nodes.append(read_node(root, day_dir, day_dir / name, window, problems))
    return day


def load(root):
    """Every day under root, newest first. Raises ReportError on anything unreadable."""
    root = Path(root)
    if not root.is_dir():
        raise ReportError([f"{root}: no such directory"])
    problems, days = [], []
    for p in sorted(root.iterdir(), reverse=True):
        if p.name in SKIP:
            continue
        if not (p.is_dir() and DATE.match(p.name)):
            problems.append(f"{p.name}: not a <YYYY-MM-DD> directory")
            continue
        days.append(read_day(root, p, problems))
    if problems:
        raise ReportError(problems)
    return days
