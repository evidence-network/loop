#!/usr/bin/env python3
"""The ecosystem gate: no step goes from PASS to red on a pin bump (loop#149).

Usage:
  ecosystem_gate.py update --ecosystem NAME --findings DIR [--baseline FILE]
  ecosystem_gate.py verify [--baseline FILE] [--lock FILE]
  ecosystem_gate.py check  --old FILE --new FILE

baseline/live.json holds, for each ecosystem, the last accepted live result of each step, the
findings directory it was taken from, and the digest of the pin set it ran under. It moves only
through `update`, which refuses a findings directory steps/finding.py refuses (that includes a
finding whose pinned entries differ from versions.lock), a rehearsal finding, a repeated step
number, and a regression against the baseline already committed. `verify` re-reads the committed
findings with steps/finding.py (records, proof and pins against the current versions.lock), refuses
a hand-edited baseline or a non-canonical findings path, and requires the baseline's pin-set digest
to be the current versions.lock's.
`check` compares the base branch's baseline with the pull request's: a pin-set digest that moved
must come with a findings directory that moved too, and a step that was
PASS and is now FAIL, malformed or missing fails, and so does PASS -> NOT_CHECKED while the old
baseline's `not_checked_is_regression` is true (default true, until Steven rules; the old file's
setting decides, so a pull request cannot turn it off for itself). Progress passes.
Standard library only. Exit 0 = clean, 1 = refused, 2 = usage.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "steps"))
import finding  # noqa: E402

BASELINE = ROOT / "baseline" / "live.json"


def pin_set_sha256(lock=ROOT / "versions.lock"):
    """The digest of the pins alone, so a comment edit in versions.lock is not a pin bump."""
    lines = sorted(f"{n} {repo} {ref}" for n, (repo, ref) in finding.pins(lock).items())
    return hashlib.sha256(("\n".join(lines) + "\n").encode()).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, doc):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_findings(findings_dir):
    """{NN: (result, sha256 of finding.json)} for every step directory, or the errors."""
    steps, errs = {}, []
    for d in sorted(p for p in Path(findings_dir).iterdir() if p.is_dir()):
        m = finding.STEP_DIR.match(d.name)
        f = d / "finding.json"
        if not m or not f.is_file():
            errs.append(f"{d.name}: not a step directory with a finding.json")
            continue
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
            result = r["result"]
            population = r["population"]
        except (ValueError, KeyError, TypeError):
            errs.append(f"{d.name}: finding.json is not a finding")
            continue
        if population != "live":
            errs.append(f"{d.name}: population is {population}; the baseline holds live results")
        if m.group(1) in steps:
            errs.append(f"{d.name}: step {m.group(1)} has more than one directory")
        steps[m.group(1)] = (result, finding.sha256(f))
    return steps, errs


def compare(old, new, not_checked_is_regression):
    """The regressions from the old ecosystem entry's steps to the new one's."""
    errs = []
    for nn, o in sorted(old.items()):
        if o["result"] != "PASS":
            continue
        n = new.get(nn, {}).get("result", "missing")
        if n == "PASS":
            continue
        if n == "NOT_CHECKED" and not not_checked_is_regression:
            continue
        errs.append(f"step {nn}: PASS -> {n}")
    return errs


def cmd_check(old_path, new_path):
    old, new = load(old_path), load(new_path)
    flag = old.get("not_checked_is_regression", True)
    errs = []
    for eco, entry in sorted(old["ecosystems"].items()):
        if eco not in new["ecosystems"]:
            errs.append(f"{eco}: ecosystem dropped from the baseline")
            continue
        n = new["ecosystems"][eco]
        if n["pin_set_sha256"] != entry["pin_set_sha256"] and n["findings"] == entry["findings"]:
            errs.append(f"{eco}: the pin set changed but the baseline still comes from {entry['findings']}")
        errs += [f"{eco} {e}" for e in compare(entry["steps"], new["ecosystems"][eco]["steps"], flag)]
    return errs


def cmd_verify(baseline, lock):
    doc = load(baseline)
    errs = []
    if not isinstance(doc.get("not_checked_is_regression"), bool):
        errs.append("not_checked_is_regression must be true or false")
    for eco, entry in sorted(doc["ecosystems"].items()):
        canonical = str((ROOT / entry["findings"]).resolve().relative_to(ROOT.resolve()))
        if entry["findings"] != canonical:
            errs.append(f"{eco}: findings is {entry['findings']!r}, write it as {canonical!r}")
        steps, ferrs = read_findings(ROOT / entry["findings"])
        errs += [f"{eco} {e}" for e in ferrs + finding.check(ROOT / entry["findings"])]
        have = {nn: {"result": r, "finding_sha256": s} for nn, (r, s) in steps.items()}
        if have != entry["steps"]:
            errs.append(f"{eco}: steps differ from the findings in {entry['findings']}")
        if entry["pin_set_sha256"] != pin_set_sha256(lock):
            errs.append(f"{eco}: versions.lock changed, so the baseline must be taken from a live run under "
                        "the new pins (update --findings DIR)")
    return errs


def cmd_update(eco, findings_dir, baseline):
    errs = finding.check(findings_dir)
    steps, ferrs = read_findings(findings_dir)
    errs += ferrs
    if errs:
        return errs
    entry = {
        "findings": str(Path(findings_dir).resolve().relative_to(ROOT.resolve())),
        "pin_set_sha256": pin_set_sha256(),
        "steps": {nn: {"result": r, "finding_sha256": s} for nn, (r, s) in steps.items()},
    }
    doc = load(baseline) if Path(baseline).is_file() else {"ecosystems": {}, "not_checked_is_regression": True}
    if eco in doc["ecosystems"]:
        errs = compare(doc["ecosystems"][eco]["steps"], entry["steps"], doc["not_checked_is_regression"])
        if errs:
            return [f"{eco} {e}" for e in errs]
    doc["ecosystems"][eco] = entry
    write(baseline, doc)
    return []


def main(argv):
    ap = argparse.ArgumentParser(prog="ecosystem_gate.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("update")
    u.add_argument("--ecosystem", required=True)
    u.add_argument("--findings", required=True)
    v = sub.add_parser("verify")
    c = sub.add_parser("check")
    c.add_argument("--old", required=True)
    c.add_argument("--new", required=True)
    for p in (u, v):
        p.add_argument("--baseline", default=str(BASELINE))
    v.add_argument("--lock", default=str(ROOT / "versions.lock"))
    a = ap.parse_args(argv[1:])
    try:
        if a.cmd == "update":
            errs = cmd_update(a.ecosystem, a.findings, a.baseline)
        elif a.cmd == "verify":
            errs = cmd_verify(a.baseline, a.lock)
        else:
            errs = cmd_check(a.old, a.new)
    except (OSError, ValueError, KeyError, TypeError) as e:
        errs = [f"cannot read the baseline: {e!r}"]
    for e in errs:
        print(e, file=sys.stderr)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
