#!/usr/bin/env python3
"""A loop step's finding: what it asserts, what it found, what it pinned, its proof.

Usage: finding.py check DIR      check every <NN>-<name>/finding.json under DIR

A step writes exactly one finding.json into its own directory, beside the proof
files it names. A log line is not a finding: the result is PASS only when the
step's assertion was checked by a verifier and held, FAIL when it was checked
and did not hold, and NOT_CHECKED when it could not be checked (and why).
Standard library only. Exit 0 = clean, 1 = refused.
"""
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ("PASS", "FAIL", "NOT_CHECKED")
POPULATIONS = ("rehearsal", "live")
STEP_DIR = re.compile(r"^(\d{2})-([a-z][a-z0-9-]*)$")
FIELDS = ("step", "name", "result", "population", "asserts", "pinned", "proof", "detail",
          "not_checked", "started", "finished")
# In a NOT_CHECKED finding, asserts and detail may not claim a check: "checked",
# "witnessed" and "verified" are refused unless negated ("not checked", "never
# verified", "could not be verified", "has not been checked"). "unchecked",
# "unverified" and "NOT_CHECKED" are other words and are not matched.
NEGATED_CHECK = re.compile(r"\b(?:not|never|cannot)(?:\s+be(?:en)?)?\s+(?:checked|witnessed|verified)\b", re.I)
CLAIMED_CHECK = re.compile(r"\b(?:checked|witnessed|verified)\b", re.I)
# A PASS states each gap outside its assertion in detail as one sentence,
# "Outside this step's assertion: <what>: <owner/repo#N>.", which outside()
# writes and gaps() reads back (the status page shows them on the row).
OUTSIDE = "Outside this step's assertion: "
ISSUE_REF = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#\d+$")
GAP = re.compile(re.escape(OUTSIDE) + r"(.+?): ([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#\d+)\.(?=\s|$)")


def outside(what, issue):
    """One gap sentence for a PASS detail: what lies outside the assertion, and the issue that owns it."""
    if not ISSUE_REF.match(issue):
        raise ValueError(f"a gap's issue is owner/repo#N, not {issue!r}")
    return f"{OUTSIDE}{what}: {issue}."


def gaps(detail):
    """The [(what, issue)] gap sentences in a detail."""
    return GAP.findall(detail)


def pins(path=ROOT / "versions.lock"):
    """versions.lock as {name: (repository, ref)}."""
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            name, repo, ref = line.split()
            out[name] = (repo, ref)
    return out


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write(step_dir, *, result, population, asserts, pinned, proof, detail, not_checked=(), started):
    """Write <step_dir>/finding.json. `pinned` names versions.lock entries; `proof` names files in step_dir.

    `not_checked` lists {"what", "why", "issue"} for each part that could not be
    checked; a NOT_CHECKED result needs at least one.
    """
    step_dir = Path(step_dir)
    m = STEP_DIR.match(step_dir.name)
    if not m:
        raise ValueError(f"{step_dir.name}: a step directory is <NN>-<name>")
    lock = pins()
    record = {
        "step": m.group(1),
        "name": m.group(2),
        "result": result,
        "population": population,
        "asserts": asserts,
        "pinned": {n: {"repository": lock[n][0], "ref": lock[n][1]} for n in pinned},
        "proof": [{"path": p, "sha256": sha256(step_dir / p)} for p in proof],
        "detail": detail,
        "not_checked": list(not_checked),
        "started": started,
        "finished": now(),
    }
    errs = check_record(step_dir, record)
    if errs:
        raise ValueError("; ".join(errs))
    (step_dir / "finding.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return record


def check_record(step_dir, r):
    errs = []
    where = Path(step_dir).name
    if sorted(r) != sorted(FIELDS):
        return [f"{where}: fields must be exactly {', '.join(FIELDS)}"]
    m = STEP_DIR.match(where)
    if not m or (r["step"], r["name"]) != m.groups():
        errs.append(f"{where}: step and name must match the directory")
    if r["result"] not in RESULTS:
        errs.append(f"{where}: result must be one of {', '.join(RESULTS)}")
    if r["population"] not in POPULATIONS:
        errs.append(f"{where}: population must be one of {', '.join(POPULATIONS)}")
    if not (isinstance(r["asserts"], str) and r["asserts"].strip()):
        errs.append(f"{where}: asserts is the one fact the step checks")
    lock = pins()
    for n, pin in r["pinned"].items():
        if lock.get(n) != (pin.get("repository"), pin.get("ref")):
            errs.append(f"{where}: pinned {n} is not the versions.lock entry")
    for p in r["proof"]:
        f = Path(step_dir) / p["path"]
        if Path(p["path"]).is_absolute() or p["path"].startswith("/") or ".." in Path(p["path"]).parts:
            errs.append(f"{where}: proof {p['path']} must be a relative path inside the step directory")
        elif not f.is_file():
            errs.append(f"{where}: proof {p['path']} is not a file in the step directory")
        elif sha256(f) != p["sha256"]:
            errs.append(f"{where}: proof {p['path']} does not match its sha256")
    if r["result"] == "PASS" and not r["proof"]:
        errs.append(f"{where}: a PASS needs the proof bytes it was decided on")
    for item in r["not_checked"]:
        if not (isinstance(item, dict) and sorted(item) == ["issue", "what", "why"]):
            errs.append(f"{where}: each not_checked entry is {{what, why, issue}}")
    if r["result"] == "NOT_CHECKED" and not r["not_checked"]:
        errs.append(f"{where}: NOT_CHECKED needs a not_checked entry saying what and why")
    if r["result"] == "NOT_CHECKED":
        for field in ("asserts", "detail"):
            claim = CLAIMED_CHECK.search(NEGATED_CHECK.sub("", str(r[field])))
            if claim:
                errs.append(f"{where}: a NOT_CHECKED {field} may not claim {claim.group(0)!r}")
    if str(r["detail"]).count(OUTSIDE) != len(gaps(str(r["detail"]))):
        errs.append(f"{where}: each gap in detail is one sentence, \"{OUTSIDE}<what>: <owner/repo#N>.\"")
    if r["result"] == "PASS" and r["not_checked"]:
        errs.append(f"{where}: a PASS covers its assertion fully, so it has no not_checked entries; "
                    "say what lies outside the assertion in detail")
    return errs


def check(findings_dir):
    errs = []
    dirs = [d for d in sorted(Path(findings_dir).iterdir()) if d.is_dir()]
    if not dirs:
        return [f"{findings_dir}: no step directories"]
    for d in dirs:
        f = d / "finding.json"
        if not f.is_file():
            errs.append(f"{d.name}: no finding.json")
            continue
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except ValueError as e:
            errs.append(f"{d.name}: finding.json is not JSON ({e})")
            continue
        errs += check_record(d, r) if isinstance(r, dict) else [f"{d.name}: finding.json is not an object"]
    return errs


def main(argv):
    if len(argv) != 3 or argv[1] != "check":
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    errs = check(argv[2])
    for e in errs:
        print(e, file=sys.stderr)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
