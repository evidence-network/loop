"""The layout of a node's day in reports/daily/, and how that day is read.

One module, so the reporter that writes reports, the check that guards them and
anything that reads them (a report page) agree on what the files mean.

A node's day is its attempts: <node_id>/attempts/<YYYYMMDDTHHMMSSZ>/, one
directory per time the reporter asked, each written once and never touched
again. An attempt holds ATTEMPT (`at=<time> by=run pass=<n>` or `at=<time>
by=ask`), peer-key.json, and for each question it asked: <q>.request.json,
<q>.verification, and <q>.response.json or <q>.absence.txt.

An attempt's verification line starts with one of
  verified | no_checkpoint_yet | refusal | no_announced_key | not_evidence
    an answer came back: the question is settled for the day and is never
    asked again, a signed refusal included;
  unreachable | timeout | absence
    no answer: `unreachable`, the reporter's node could not open a stream to
    the node (the plugin's "could not reach peer"); `timeout`, the stream
    opened or the request went out and nothing came back in time (the
    plugin's "peer does not answer evidence requests", or no reply from the
    reporter's own node within its timeout); `absence`, no reply for any
    other reason. The question is asked again.

A question with no answer after its last attempt is the day's `absence`.

A report set is a directory under reports/ holding one directory per period:
`daily`, one per UTC day (<YYYY-MM-DD>), `rehearsal`, a test of the reporter,
one per 10-minute UTC slot (<YYYY-MM-DD>T<HHMM>Z, minutes a multiple of 10),
and `ondemand`, one per run made by hand between dailies, named by the second
the run started (<YYYY-MM-DD>T<HHMMSS>Z, UTC). A period's directory has the
same layout in all three; SETS has each set's period. `daily` and `rehearsal`
are continuous: a period with no run is MISSED. `ondemand` is not: its runs
are made by hand, so the time between two runs is no period at all, never
MISSED, and nothing expects the next one. Standard library only.
"""
import datetime as dt
import json
import re
from pathlib import Path

STEMS = ("checkpoints", "history_card")
ANSWERED = ("verified", "no_checkpoint_yet", "refusal", "no_announced_key", "not_evidence")
NO_ANSWER = ("unreachable", "timeout", "absence")
ATTEMPT_STATES = ANSWERED + NO_ANSWER
DAY_STATES = ANSWERED + ("absence",)
ATTEMPTS = "attempts"
ATTEMPT_NAME = re.compile(r"^\d{8}T\d{6}Z$")
ATTEMPT_LINE = re.compile(r"^at=(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) by=(run pass=[1-9]\d*|ask)$")


class Daily:
    """The daily set: one directory per UTC day, <YYYY-MM-DD>."""
    set = "daily"
    noun = "day"
    pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    step = dt.timedelta(days=1)
    continuous = True

    @staticmethod
    def name(period):
        return period.isoformat()

    @staticmethod
    def parse(name):
        return dt.date.fromisoformat(name)

    @staticmethod
    def of(at):
        """The day a UTC time falls in."""
        return at.date()


class Rehearsal:
    """The rehearsal set: one directory per 10-minute UTC slot, <YYYY-MM-DD>T<HHMM>Z."""
    set = "rehearsal"
    noun = "slot"
    pattern = re.compile(r"^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3])[0-5]0Z$")
    step = dt.timedelta(minutes=10)
    continuous = True

    @staticmethod
    def name(period):
        return period.strftime("%Y-%m-%dT%H%MZ")

    @staticmethod
    def parse(name):
        if not Rehearsal.pattern.fullmatch(name):
            raise ValueError(f"{name!r} is not a 10-minute slot <YYYY-MM-DD>T<HHMM>Z")
        return dt.datetime.strptime(name, "%Y-%m-%dT%H%MZ").replace(tzinfo=dt.timezone.utc)

    @staticmethod
    def of(at):
        """The slot a UTC time falls in."""
        return at.replace(minute=at.minute - at.minute % 10, second=0, microsecond=0)


class OnDemand:
    """The on-demand set: one directory per run made by hand, <YYYY-MM-DD>T<HHMMSS>Z, its start (UTC).

    Not continuous: there is no step from one run to the next, so no run is
    ever MISSED for want of another, and the run before a run is simply the
    latest earlier one there is.
    """
    set = "ondemand"
    noun = "run"
    pattern = re.compile(r"^\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3])[0-5]\d[0-5]\dZ$")
    step = None
    continuous = False

    @staticmethod
    def name(period):
        return period.strftime("%Y-%m-%dT%H%M%SZ")

    @staticmethod
    def parse(name):
        if not OnDemand.pattern.fullmatch(name):
            raise ValueError(f"{name!r} is not an on-demand run <YYYY-MM-DD>T<HHMMSS>Z")
        return dt.datetime.strptime(name, "%Y-%m-%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)

    @staticmethod
    def of(at):
        """The run that starts at a UTC time."""
        return at.replace(microsecond=0)


SETS = {"daily": Daily, "rehearsal": Rehearsal, "ondemand": OnDemand}


def previous(out, name, period=Daily):
    """The name of the period before `name` in the set at `out`, or None.

    In a continuous set, the period one step earlier, whether or not it has a
    directory; in the on-demand set, the latest earlier run directory there is.
    """
    if period.continuous:
        return period.name(period.parse(name) - period.step)
    out = Path(out)
    earlier = sorted(p.name for p in out.iterdir() if p.is_dir() and period.pattern.fullmatch(p.name)
                     and p.name < name) if out.is_dir() else []
    return earlier[-1] if earlier else None


def iso(at):
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")


def attempt_name(at):
    return at.strftime("%Y%m%dT%H%M%SZ")


def attempt_time(name):
    """The ISO time an attempt directory's name stands for."""
    return iso(dt.datetime.strptime(name, "%Y%m%dT%H%M%SZ"))


def state(line):
    return line.split(" ", 1)[0]


def attempts(node_dir):
    """A node's attempt directories, earliest first."""
    root = Path(node_dir) / ATTEMPTS
    return sorted(p for p in root.iterdir() if p.is_dir() and ATTEMPT_NAME.fullmatch(p.name)) if root.is_dir() else []


def answers(node_dir):
    """Per question, the day so far: {"state", "line", "attempt", "tried"}.

    `attempt` is the attempt that answered (the first that did), or None;
    `tried` lists every attempt that asked the question, earliest first.
    `state` is that attempt's state, `absence` when every attempt went
    unanswered, or None when the question was never asked.
    """
    day = {}
    for stem in STEMS:
        tried, answer = [], None
        for a in attempts(node_dir):
            path = a / f"{stem}.verification"
            if not path.is_file():
                continue
            line = path.read_text(encoding="utf-8").strip()
            tried.append((a, line))
            if answer is None and state(line) in ANSWERED:
                answer = (a, line)
        if answer:
            day[stem] = {"state": state(answer[1]), "line": answer[1], "attempt": answer[0], "tried": tried}
        else:
            day[stem] = {"state": "absence" if tried else None, "line": None, "attempt": None, "tried": tried}
    return day


def unanswered(node_dir):
    """The questions still owed an answer today: never asked, or never answered."""
    return [stem for stem, a in answers(node_dir).items() if a["attempt"] is None]


def absent(node_dir):
    """True when the node was asked and answered nothing all day."""
    return all(a["state"] == "absence" for a in answers(node_dir).values())


def absence_line(tried):
    """The day's verification line for a question no attempt answered."""
    last = tried[-1][1]
    times = ",".join(attempt_time(a.name) for a, _ in tried)
    return f"absence {last.split(' ', 1)[1] if ' ' in last else 'why=unknown'} attempts={times}"


def absence_text(tried, stem):
    """The day's absence.txt for a question no attempt answered: each attempt, when and why."""
    lines = [f"no answer after {len(tried)} attempt{'' if len(tried) == 1 else 's'}:"]
    for a, line in tried:
        why = (a / f"{stem}.absence.txt").read_text(encoding="utf-8", errors="replace")
        lines.append(f"{attempt_time(a.name)} {state(line)}: {' '.join(why.split())}")
    return "\n".join(lines) + "\n"


def absent_days_running(out, node_id, day, period=Daily):
    """How many days, ending with `day`, the node was asked and answered nothing.

    Counts back day by day and stops at a day the node answered anything, was
    not asked, or had no run. A re-ask that got an answer ends the run of days.
    In the rehearsal set it counts slots the same way; in the on-demand set,
    runs, each one back to the latest earlier run.
    """
    n = 0
    name = period.name(day)
    while name is not None:
        node_dir = Path(out) / name / node_id
        if not (node_dir.is_dir() and attempts(node_dir) and absent(node_dir)):
            return n
        n += 1
        name = previous(out, name, period)
    return n


def absence_fact(n, noun="day"):
    """The count as a fact about the node, never a score. `noun` is the set's (Daily.noun, ...)."""
    if n == 0:
        return ""
    if n == 1:
        return "absent today" if noun == "day" else f"absent this {noun}"
    return f"absent {n} {noun}s running"


def read_attempt(attempt_dir):
    """(at, by) from an attempt's ATTEMPT line, or None when it does not parse."""
    m = ATTEMPT_LINE.fullmatch((Path(attempt_dir) / "ATTEMPT").read_text(encoding="utf-8").strip())
    return (m.group(1), m.group(2)) if m else None


def run_record(day_dir):
    return json.loads((Path(day_dir) / "RUN.json").read_text(encoding="utf-8"))
