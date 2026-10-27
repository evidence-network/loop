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

A `verified` line also carries the log size at the answer's checkpoint,
`mmr_size=<n> timestamp=<time>`, when the answer names one: `last_checkpoint()`
reads it from the answer's artifact (see there).
Standard library only.
"""
import base64
import datetime as dt
import hashlib
import json
import re
import struct
from pathlib import Path

STEMS = ("checkpoints", "history_card")
ANSWERED = ("verified", "no_checkpoint_yet", "refusal", "no_announced_key", "not_evidence")
NO_ANSWER = ("unreachable", "timeout", "absence")
ATTEMPT_STATES = ANSWERED + NO_ANSWER
DAY_STATES = ANSWERED + ("absence",)
ATTEMPTS = "attempts"
ATTEMPT_NAME = re.compile(r"^\d{8}T\d{6}Z$")
CHECKPOINT_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")
ATTEMPT_LINE = re.compile(r"^at=(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z) by=(run pass=[1-9]\d*|ask)$")


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
    return sorted(p for p in root.iterdir() if p.is_dir() and ATTEMPT_NAME.match(p.name)) if root.is_dir() else []


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


def absent_days_running(out, node_id, day):
    """How many days, ending with `day`, the node was asked and answered nothing.

    Counts back day by day and stops at a day the node answered anything, was
    not asked, or had no run. A re-ask that got an answer ends the run of days.
    """
    n = 0
    while True:
        node_dir = Path(out) / day.isoformat() / node_id
        if not (node_dir.is_dir() and attempts(node_dir) and absent(node_dir)):
            return n
        n += 1
        day -= dt.timedelta(days=1)


def absence_fact(n):
    """The count as a fact about the node, never a score."""
    if n == 0:
        return ""
    return "absent today" if n == 1 else f"absent {n} days running"


def read_attempt(attempt_dir):
    """(at, by) from an attempt's ATTEMPT line, or None when it does not parse."""
    m = ATTEMPT_LINE.match((Path(attempt_dir) / "ATTEMPT").read_text(encoding="utf-8").strip())
    return (m.group(1), m.group(2)) if m else None


def _cbor(b, i=0):
    """(value, next index) of the CBOR item at b[i]: definite lengths only, tags dropped."""
    major, info, i = b[i] >> 5, b[i] & 31, i + 1
    if info < 24:
        n = info
    elif info <= 27:
        size = 1 << (info - 24)
        n, i = int.from_bytes(b[i:i + size], "big"), i + size
        if major == 7 and info > 24:
            return struct.unpack({2: ">e", 4: ">f", 8: ">d"}[size], b[i - size:i])[0], i
    else:
        raise ValueError("indefinite or reserved CBOR length")
    if major == 0:
        return n, i
    if major == 1:
        return -1 - n, i
    if major in (2, 3):
        if i + n > len(b):
            raise ValueError("CBOR string runs past the end")
        return (b[i:i + n] if major == 2 else b[i:i + n].decode("utf-8")), i + n
    if major == 4:
        items = []
        for _ in range(n):
            v, i = _cbor(b, i)
            items.append(v)
        return items, i
    if major == 5:
        m = {}
        for _ in range(n):
            k, i = _cbor(b, i)
            m[k], i = _cbor(b, i)
        return m, i
    if major == 6:
        return _cbor(b, i)
    return {20: False, 21: True, 22: None}.get(n), i


def last_checkpoint(response):
    """(mmr_size, timestamp) of the checkpoint a verified answer is anchored at, or None.

    `response` is a .response.json's bytes. The answer's artifact (base64url) is
    a JSON object whose `checkpoints` list (or `history` entries' `checkpoint`,
    for history_card/1) holds signed checkpoint statements in hex, oldest first,
    ending with the one the artifact's `anchor` names. A statement is a COSE_Sign1
    whose payload carries `log_size` (the MMR size) and `issued_at`. Only the
    last statement is read, and only when its SHA-256 is the artifact's `anchor`.
    The reporter does not verify: it reads this after the plugin said `artifact`.
    """
    try:
        answer = json.loads(response)["answer"]
        text = (answer.get("artifact") or answer["envelope"]["artifact"])
        artifact = json.loads(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)))
        chain = artifact.get("checkpoints") or [h["checkpoint"] for h in artifact.get("history") or []]
        statement = bytes.fromhex(chain[-1])
        if hashlib.sha256(statement).hexdigest() != artifact["anchor"]:
            return None
        sign1, _ = _cbor(statement)
        claims, _ = _cbor(sign1[2])
        size, at = claims["log_size"], claims["issued_at"]
    except (ValueError, KeyError, IndexError, TypeError, AttributeError, struct.error):
        return None
    if isinstance(size, int) and not isinstance(size, bool) and size >= 0 and isinstance(at, str) \
            and CHECKPOINT_TIME.match(at):
        return size, at
    return None



def run_record(day_dir):
    return json.loads((Path(day_dir) / "RUN.json").read_text(encoding="utf-8"))
