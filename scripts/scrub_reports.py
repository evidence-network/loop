#!/usr/bin/env python3
"""The scrubbed set: what a day of raw reports may show in public, and nothing more.

Usage:
  scrub_reports.py scrub REPORTS_DIR OUT_DIR [--left DIR] [--set daily|rehearsal|ondemand]
  scrub_reports.py check OUT_DIR [--set daily|rehearsal|ondemand]

The raw reports (reports/daily/<YYYY-MM-DD>/, scripts/report_layout.py) hold
the bytes each node answered. They stay in the private raw store. `scrub`
reads them and writes one file per day, OUT_DIR/<YYYY-MM-DD>.json, holding
only the fields below; `check` refuses a file holding anything else.

  date                  the day
  run                   "settled" (RUN.json) or "missed" (MISSED)
  missed_reason         the reporter's own reason, up to its first ':'
                        (never the error text after it); null when settled
  versions_lock_sha256  from RUN.json; null when missed
  reporter_key_id       the reporter's key from RUN.json; null when missed
  listed                the nodes the run asked (the commitment list that day,
                        less the reporter unless a second console asked it);
                        0 when missed
  nodes                 one per node asked, sorted by node_id; [] when missed:
    node_id, node_line (NODE), absent_days_running, left_on (from
    nodes/left/<node_id>.json, or null), and per question (checkpoints,
    history_card): state (the day as read with any later ask in it), every
    attempt's time and state, log_size_at_checkpoint (the latest checkpoint's
    mmr_size and timestamp in a verified answer, else null),
    records_at_checkpoint (how many records the node's log holds at that
    checkpoint: the leaf count of an MMR of mmr_size nodes; null exactly when
    log_size_at_checkpoint is null, or mmr_size is no MMR's size) and
    answer_sha256 (the SHA-256 of the answer's bytes; null when nothing came
    back).

No answer, request, peer-key or verification bytes go in. The same input
gives the same bytes: sorted keys, 2-space indent, a trailing newline, and
OUT_DIR holds exactly one file per input day.

With --set rehearsal, the rehearsal set (reports/rehearsal/, a test of the
reporter, one directory per 10-minute UTC slot) is scrubbed the same way into
one OUT_DIR/<YYYY-MM-DD>T<HHMM>Z.json per slot: `date` is the slot, and
absent_days_running counts slots. With --set ondemand, the on-demand set
(reports/ondemand/, runs made by hand between dailies, one directory per run)
is scrubbed the same way into one OUT_DIR/<YYYY-MM-DD>T<HHMMSS>Z.json per run:
`date` is the run's name, and absent_days_running counts runs, each back to
the latest earlier one. Standard library only.
Exit 0 = done / clean, 1 = refused.
"""
import argparse
import base64
import binascii
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import report_layout as layout  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?Z$")
# A run of base64/base64url characters this long in free text is a blob, not words.
BLOB = re.compile(r"[A-Za-z0-9+/_=-]{24,}")
MAX_STRING = 200
NODE_LINE = re.compile(r"^(joined, discloses nothing|joined, no checkpoint yet|discloses: (checkpoints|history_card/1)(, history_card/1)?)$")

DAY_KEYS = {"date", "run", "missed_reason", "versions_lock_sha256", "reporter_key_id", "listed", "nodes"}
NODE_KEYS = {"node_id", "node_line", "absent_days_running", "left_on", "questions"}
QUESTION_KEYS = {"state", "attempts", "log_size_at_checkpoint", "records_at_checkpoint", "answer_sha256"}


def dump(day):
    return json.dumps(day, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def day_file(period):
    """A scrubbed file's name in the set: <period>.json."""
    return re.compile(period.pattern.pattern[:-1] + r"\.json$")


def missed_reason(text, date):
    """The reporter's reason for the day, with any error text after its first ':' cut off."""
    text = " ".join(text.split())
    prefix = f"no report for {date}: "
    if text.startswith(prefix):
        text = text[len(prefix):]
    return text.split(":", 1)[0].strip()[:MAX_STRING] or None


def decode_artifact(answer):
    """The artifact JSON inside a reply's answer (flat envelope, or under `envelope`), or None."""
    if not isinstance(answer, dict):
        return None
    blob = answer.get("artifact")
    if blob is None and isinstance(answer.get("envelope"), dict):
        blob = answer["envelope"].get("artifact")
    if not isinstance(blob, str):
        return None
    try:
        raw = base64.urlsafe_b64decode(blob.replace("+", "-").replace("/", "_") + "=" * (-len(blob) % 4))
        return json.loads(raw)
    except (binascii.Error, ValueError):
        return None


def log_size(reply):
    """{"mmr_size", "timestamp"} of the largest checkpoint in a verified answer's artifact, or None."""
    artifact = decode_artifact(reply.get("answer")) if isinstance(reply, dict) else None
    checkpoints = artifact.get("checkpoints") if isinstance(artifact, dict) else None
    best = None
    for cp in checkpoints if isinstance(checkpoints, list) else []:
        if not isinstance(cp, dict):
            continue
        size, at = cp.get("mmr_size"), cp.get("timestamp")
        if isinstance(size, int) and not isinstance(size, bool) and size >= 0 and isinstance(at, str) and ISO.fullmatch(at):
            if best is None or size > best["mmr_size"]:
                best = {"mmr_size": size, "timestamp": at}
    return best


def leaves(mmr_size):
    """The records (leaves) in an MMR of mmr_size nodes, or None when no MMR has that size.

    An MMR of n leaves has 2n - popcount(n) nodes, which grows with n, so n lies
    within 64 of mmr_size / 2.
    """
    if not (isinstance(mmr_size, int) and not isinstance(mmr_size, bool) and mmr_size >= 0):
        return None
    for n in range(mmr_size // 2, mmr_size // 2 + 66):
        if 2 * n - bin(n).count("1") == mmr_size:
            return n
    return None


def question(node_dir, stem, answered):
    """One question's day: state, attempts, log size, the answer's digest."""
    a = answered[stem]
    attempts = [{"at": layout.attempt_time(d.name), "state": layout.state(line)} for d, line in a["tried"]]
    digest, size = None, None
    if a["attempt"] is not None:
        path = a["attempt"] / f"{stem}.response.json"
        if path.is_file():
            body = path.read_bytes()
            digest = hashlib.sha256(body).hexdigest()
            if a["state"] == "verified":
                try:
                    size = log_size(json.loads(body))
                except ValueError:
                    size = None
    return {"state": a["state"], "attempts": attempts, "log_size_at_checkpoint": size,
            "records_at_checkpoint": leaves(size["mmr_size"]) if size else None, "answer_sha256": digest}


def left_on(left_dir, node_id):
    path = Path(left_dir) / f"{node_id}.json"
    if not path.is_file():
        return None
    left = json.loads(path.read_text(encoding="utf-8")).get("left")
    return left if isinstance(left, str) and DATE.fullmatch(left) else None


def scrub_day(reports, day_dir, left_dir, period=layout.Daily):
    date = day_dir.name
    if (day_dir / "RUN.json").is_file():
        run = layout.run_record(day_dir)
        nodes = []
        for node_id in sorted(run["asked"]):
            node_dir = day_dir / node_id
            answered = layout.answers(node_dir)
            missing = [s for s in layout.STEMS if answered[s]["state"] is None]
            if missing:
                raise ValueError(f"{date}/{node_id}: never asked {', '.join(missing)}")
            nodes.append({
                "node_id": node_id,
                "node_line": (node_dir / "NODE").read_text(encoding="utf-8").strip(),
                "absent_days_running": layout.absent_days_running(reports, node_id, period.parse(date), period),
                "left_on": left_on(left_dir, node_id),
                "questions": {s: question(node_dir, s, answered) for s in layout.STEMS},
            })
        return {"date": date, "run": "settled", "missed_reason": None,
                "versions_lock_sha256": run["versions_lock_sha256"], "reporter_key_id": run["reporter"]["key_id"],
                "listed": len(run["asked"]), "nodes": nodes}
    if (day_dir / "MISSED").is_file():
        return {"date": date, "run": "missed",
                "missed_reason": missed_reason((day_dir / "MISSED").read_text(encoding="utf-8"), date),
                "versions_lock_sha256": None, "reporter_key_id": None, "listed": 0, "nodes": []}
    raise ValueError(f"{date}: neither RUN.json nor MISSED (a run still under way is not scrubbed)")


def scrub(reports, out, left_dir, period=layout.Daily):
    reports, out = Path(reports), Path(out)
    days = sorted(p for p in reports.iterdir() if p.is_dir() and period.pattern.fullmatch(p.name)) if reports.is_dir() else []
    written = {}
    for d in days:
        written[f"{d.name}.json"] = dump(scrub_day(reports, d, left_dir, period))
    errs = [e for name, text in written.items() for e in check_text(name, text, period)]
    if errs:
        raise ValueError("the scrubbed set does not check:\n  " + "\n  ".join(errs))
    out.mkdir(parents=True, exist_ok=True)
    for p in out.iterdir():
        if day_file(period).fullmatch(p.name) and p.name not in written:
            p.unlink()
    for name, text in written.items():
        path = out / name
        if not path.is_file() or path.read_bytes() != text.encode():
            path.write_bytes(text.encode())
    return sorted(written)


# --- check -----------------------------------------------------------------

def free_text(where, value, errs, nullable=True):
    if value is None and nullable:
        return
    if not isinstance(value, str):
        errs.append(f"{where}: not a string")
        return
    if len(value) > MAX_STRING:
        errs.append(f"{where}: longer than {MAX_STRING} characters")
    if BLOB.search(value):
        errs.append(f"{where}: holds a base64-looking run")


def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def keys(where, obj, want, errs):
    if not isinstance(obj, dict):
        errs.append(f"{where}: not an object")
        return False
    extra, missing = sorted(set(obj) - want), sorted(want - set(obj))
    if extra:
        errs.append(f"{where}: unknown key(s) {', '.join(extra)}")
    if missing:
        errs.append(f"{where}: missing key(s) {', '.join(missing)}")
    return not extra and not missing


def match(where, value, pattern, errs, nullable=False):
    if value is None and nullable:
        return
    if not (isinstance(value, str) and pattern.fullmatch(value)):
        errs.append(f"{where}: {value!r} is not the expected form")


def check_question(where, q, errs):
    if not keys(where, q, QUESTION_KEYS, errs):
        return
    if q["state"] not in layout.DAY_STATES:
        errs.append(f"{where}.state: {q['state']!r} is not a day state")
    if not isinstance(q["attempts"], list) or not q["attempts"]:
        errs.append(f"{where}.attempts: not a non-empty list")
    else:
        for i, a in enumerate(q["attempts"]):
            w = f"{where}.attempts[{i}]"
            if keys(w, a, {"at", "state"}, errs):
                match(f"{w}.at", a["at"], ISO, errs)
                if a["state"] not in layout.ATTEMPT_STATES:
                    errs.append(f"{w}.state: {a['state']!r} is not an attempt state")
    size = q["log_size_at_checkpoint"]
    if size is not None and keys(f"{where}.log_size_at_checkpoint", size, {"mmr_size", "timestamp"}, errs):
        if not is_int(size["mmr_size"]):
            errs.append(f"{where}.log_size_at_checkpoint.mmr_size: not a count")
        match(f"{where}.log_size_at_checkpoint.timestamp", size["timestamp"], ISO, errs)
    records = q["records_at_checkpoint"]
    if records is not None and not is_int(records):
        errs.append(f"{where}.records_at_checkpoint: not a count")
    elif records != (leaves(size.get("mmr_size")) if isinstance(size, dict) else None):
        errs.append(f"{where}.records_at_checkpoint: not the leaf count of log_size_at_checkpoint.mmr_size")
    match(f"{where}.answer_sha256", q["answer_sha256"], HEX64, errs, nullable=True)


def check_day(name, day, period=layout.Daily):
    errs = []
    if not keys(name, day, DAY_KEYS, errs):
        return errs
    match(f"{name}: date", day["date"], period.pattern, errs)
    if f"{day['date']}.json" != name:
        errs.append(f"{name}: date {day['date']!r} is not the file's")
    if day["run"] not in ("settled", "missed"):
        errs.append(f"{name}: run {day['run']!r} is neither settled nor missed")
    free_text(f"{name}: missed_reason", day["missed_reason"], errs)
    match(f"{name}: versions_lock_sha256", day["versions_lock_sha256"], HEX64, errs, nullable=True)
    match(f"{name}: reporter_key_id", day["reporter_key_id"], HEX64, errs, nullable=True)
    if not is_int(day["listed"]):
        errs.append(f"{name}: listed is not a count")
    nodes = day["nodes"]
    if not isinstance(nodes, list):
        return errs + [f"{name}: nodes is not a list"]
    if day["run"] == "missed" and (nodes or day["listed"] or day["missed_reason"] is None):
        errs.append(f"{name}: a missed day has a reason and no nodes")
    if day["run"] == "settled" and (day["missed_reason"] is not None or day["listed"] != len(nodes)):
        errs.append(f"{name}: a settled day has no missed_reason and lists every node it asked")
    ids = []
    for i, node in enumerate(nodes):
        where = f"{name}: nodes[{i}]"
        if not keys(where, node, NODE_KEYS, errs):
            continue
        match(f"{where}.node_id", node["node_id"], HEX64, errs)
        ids.append(node["node_id"])
        match(f"{where}.node_line", node["node_line"], NODE_LINE, errs)
        if not is_int(node["absent_days_running"]):
            errs.append(f"{where}.absent_days_running: not a count")
        match(f"{where}.left_on", node["left_on"], DATE, errs, nullable=True)
        if keys(f"{where}.questions", node["questions"], set(layout.STEMS), errs):
            for stem in layout.STEMS:
                check_question(f"{where}.questions.{stem}", node["questions"][stem], errs)
    if ids != sorted(set(ids)):
        errs.append(f"{name}: nodes are not sorted by node_id, or repeat one")
    return errs


def check_text(name, text, period=layout.Daily):
    if not day_file(period).fullmatch(name):
        return [{"daily": f"{name}: not <YYYY-MM-DD>.json",
                 "rehearsal": f"{name}: not a <YYYY-MM-DD>T<HHMM>Z.json 10-minute slot",
                 "ondemand": f"{name}: not a <YYYY-MM-DD>T<HHMMSS>Z.json on-demand run"}[period.set]]
    try:
        day = json.loads(text)
    except ValueError as e:
        return [f"{name}: not JSON ({e})"]
    errs = check_day(name, day, period)
    if not errs and dump(day) != text:
        errs.append(f"{name}: not in the canonical form (sorted keys, 2-space indent, trailing newline)")
    return errs


def check(out, period=layout.Daily):
    out = Path(out)
    if not out.is_dir():
        return [f"{out}: not a directory"]
    errs = []
    for p in sorted(out.iterdir()):
        if p.name == ".gitkeep":
            continue
        if not p.is_file():
            errs.append(f"{p.name}: not a file")
            continue
        try:
            text = p.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            errs.append(f"{p.name}: not UTF-8")
            continue
        errs += check_text(p.name, text, period)
    return errs


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scrub")
    s.add_argument("reports", help="the raw reports/daily directory")
    s.add_argument("out", help="the scrubbed set's directory (stats/data/daily)")
    s.add_argument("--left", default=str(ROOT / "nodes" / "left"), help="the leave marks (default: nodes/left)")
    c = sub.add_parser("check")
    c.add_argument("out")
    for p in (s, c):
        p.add_argument("--set", choices=sorted(layout.SETS), default="daily",
                       help="daily (default); rehearsal: one file per 10-minute slot; "
                            "ondemand: one file per run made by hand")
    args = parser.parse_args(argv[1:])
    period = layout.SETS[args.set]
    if args.cmd == "check":
        errs = check(args.out, period)
        for e in errs:
            print(e, file=sys.stderr)
        print(f"{args.out}: {'refused' if errs else 'clean'}")
        return 1 if errs else 0
    try:
        days = scrub(args.reports, args.out, args.left, period)
    except (OSError, ValueError, KeyError) as e:
        print(e, file=sys.stderr)
        return 1
    print(f"{args.out}: {len(days)} {period.noun}(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
