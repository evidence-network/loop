#!/usr/bin/env python3
"""Is the reporter rehearsal good enough to go live? One verdict over reports/rehearsal.

Usage: rehearsal_confidence.py [DIR] [--slots N] [--nodes DIR] [--lock FILE]
                               [--build-json FILE] [--json]
       (default DIR: reports/rehearsal of a loop-data checkout; N: 12)

A slot is clean when it holds a finished run (RUN.json, under the checks of
check_reports.py) and
  - it was taken under the current versions.lock (RUN.json versions_lock_sha256);
  - it asked exactly the nodes listed in nodes/;
  - every node it asked answered `checkpoints` and `history_card/1` verified,
    under the plugin key its nodes/ entry lists (listed_key=match);
  - the reporter's own node (RUN.json reporter.node_id) has a row, asked
    through the second console, and that row is verified too, not absent.
A MISSED slot, a slot with no directory between two others, and a run that
stopped before RUN.json are not clean. The newest slot, while it has neither
RUN.json nor MISSED, is still under way and is left out.

The verdict is CONFIDENT (exit 0) when
  1. the newest slots, back to the first one that is not clean, are a run of at
     least N clean slots (12 slots: two hours);
  2. a MISSED slot comes before that run, and the slot right after the latest
     such MISSED slot is clean: the planted stop of the timer, and its recovery
     with no hand step;
  3. the build is the pinned one. The reports do not record a node's build:
     RUN.json records only the versions.lock the reporter ran under, and each
     answer only the plugin key the node announced. So the build comes from
     --build-json, the build.json `lab/lab.sh check` writes on the lab host:
     its check found no failure, its capsules source is the versions.lock pin,
     and its mesh-llm source contains the mesh-llm pins. Without it, NOT YET.
Otherwise NOT YET: <reason> (exit 1). The first line printed is the verdict;
with --json, the details follow as JSON. Standard library only.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_reports  # noqa: E402
import report_layout as layout  # noqa: E402

Rehearsal = layout.Rehearsal
QUESTION_NAMES = {"checkpoints": "checkpoints", "history_card": "history_card/1"}
SHA = re.compile(r"^[0-9a-f]{40}$")
MESH_PINS = ("mesh-llm", "mesh-llm-pr-2620")


def short(node_id):
    return node_id[:12]


def lock_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pins(path):
    """{name: ref} from versions.lock."""
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) == 3 and not line.startswith("#"):
            out[parts[0]] = parts[2]
    return out


def listed(nodes_dir):
    """{node_id: entry} for each node in nodes/."""
    out = {}
    for p in sorted(Path(nodes_dir).glob("*.json")):
        e = json.loads(p.read_text(encoding="utf-8"))
        out[e["node_id"]] = e
    return out


def judge(slot_dir, nodes, lock):
    """None when the slot is clean; otherwise why it is not, in one line."""
    if (slot_dir / "MISSED").is_file():
        return "MISSED: " + " ".join((slot_dir / "MISSED").read_text(encoding="utf-8").split())
    if not (slot_dir / "RUN.json").is_file():
        return "no RUN.json: its run stopped before it finished"
    errs = check_reports.check_day(slot_dir)
    if errs:
        return "does not check: " + "; ".join(errs)
    run = layout.run_record(slot_dir)
    if run.get("versions_lock_sha256") != lock:
        return f"taken under versions.lock {str(run.get('versions_lock_sha256'))[:12]}, not the current {lock[:12]}"
    asked = sorted(run["asked"])
    if asked != sorted(nodes):
        extra = [short(n) for n in asked if n not in nodes]
        left = [short(n) for n in nodes if n not in asked]
        return "asked " + "; ".join(x for x in (f"{', '.join(extra)} not in nodes/" if extra else "",
                                                  f"not {', '.join(left)}, listed in nodes/" if left else "") if x)
    # The reporter's own row: asked through the second console, never left out.
    me = run["reporter"]["node_id"]
    if me not in asked or me not in run.get("asked_through_second_console", []):
        return f"the reporter's own node {short(me)} has no row asked through a second console"
    for node_id in asked:
        day = layout.answers(slot_dir / node_id)
        who = "the reporter's own node" if node_id == me else "node"
        for stem, name in QUESTION_NAMES.items():
            a = day[stem]
            if a["state"] != "verified":
                return f"{who} {short(node_id)} {name}: {a['state']}"
            if "listed_key=match" not in a["line"].split():
                return f"{who} {short(node_id)} {name}: verified under a key other than its nodes/ entry's"
    return None


def slots(reports):
    """[(name, slot_dir or None)] from the first slot to the newest settled one, gaps included."""
    names = sorted(p.name for p in Path(reports).iterdir() if p.is_dir() and Rehearsal.pattern.match(p.name)) \
        if Path(reports).is_dir() else []
    if names:
        newest = Path(reports) / names[-1]
        if not (newest / "RUN.json").exists() and not (newest / "MISSED").exists():
            names.pop()  # still under way
    if not names:
        return []
    out, at = [], Rehearsal.parse(names[0])
    while at <= Rehearsal.parse(names[-1]):
        name = Rehearsal.name(at)
        out.append((name, Path(reports) / name if name in names else None))
        at += Rehearsal.step
    return out


def judged(reports, nodes, lock):
    """[(slot, why or None)], oldest first."""
    return [(name, judge(d, nodes, lock) if d is not None else "no report and no MISSED mark")
            for name, d in slots(reports)]


def last_clean(reports, nodes, lock):
    """The newest clean slot, or None."""
    return next((name for name, why in reversed(judged(reports, nodes, lock)) if why is None), None)


def check_build(path, lock_pins):
    """[problems] with lab/lab.sh check's build.json against versions.lock."""
    try:
        build = json.loads(Path(path).read_text(encoding="utf-8"))
        mesh, cap = build["mesh_llm"], build["capsules"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        return [f"{path}: not a build.json from lab/lab.sh check ({e})"]
    problems = [f"{path}: lab/lab.sh check failed: {f}" for f in build.get("check_failures", ["it has not run"])]
    pin = lock_pins.get("capsules")
    want = pin if pin and SHA.match(pin) else cap.get("pin_commit") if cap.get("pin") == pin else None
    if not want or cap.get("source_head") != want:
        problems.append(f"{path}: capsules {cap.get('pin') or cap.get('source_head')} "
                        f"({str(cap.get('source_head'))[:12]}), versions.lock pins {pin}")
    for name in MESH_PINS:
        if lock_pins.get(name) and (mesh.get("contains") or {}).get(name) != lock_pins[name]:
            problems.append(f"{path}: mesh-llm {str(mesh.get('source_head'))[:12]} was not checked to contain "
                            f"the {name} pin {lock_pins[name]}")
    return problems


def declared(nodes, lock_pins):
    """What each nodes/ entry says it runs, and whether that names the pins. A declaration, never a measurement."""
    out = {}
    for node_id, e in sorted(nodes.items()):
        host, plugin = str(e.get("host_build", "")), str(e.get("plugin_version", ""))
        out[node_id] = {
            "host_build": host, "plugin_version": plugin,
            "names_capsules_pin": bool(lock_pins.get("capsules")) and lock_pins["capsules"] in plugin,
            "names_mesh_llm_pins": all(lock_pins[n] in host for n in MESH_PINS if lock_pins.get(n)),
        }
    return out


def confidence(reports, nodes_dir, lock_path, need=12, build_json=None):
    """(verdict line, details)."""
    nodes = listed(nodes_dir)
    lock = lock_sha256(lock_path)
    lock_pins = pins(lock_path)
    seen = judged(reports, nodes, lock)
    run = []
    first_unclean = None
    for name, why in reversed(seen):
        if why is not None:
            first_unclean = {"slot": name, "why": why}
            break
        run.insert(0, name)
    reasons = []
    if not seen:
        reasons.append(f"no settled slot in {reports}")
    elif len(run) < need:
        reasons.append(f"{len(run)} consecutive clean slot{'' if len(run) == 1 else 's'}, need {need}; "
                       f"{first_unclean['slot']} is not clean: {first_unclean['why']}")
    before = seen[:len(seen) - len(run)]
    missed = next((i for i in range(len(before) - 1, -1, -1)
                   if before[i][1] is not None and before[i][1].startswith("MISSED")), None)
    recovery = None
    if missed is None:
        if seen:
            reasons.append("no MISSED slot before the run: plant a stop of the timer across a slot boundary")
    else:
        after = seen[missed + 1]
        recovery = {"missed": before[missed][0], "reason": before[missed][1], "next": after[0],
                    "next_clean": after[1] is None, "next_why": after[1]}
        if after[1] is not None:
            reasons.append(f"the slot after the MISSED slot {before[missed][0]}, {after[0]}, is not clean: {after[1]}")
    if build_json is None:
        build = ["no --build-json: the reports do not record a node's build; run lab/lab.sh check on the lab host "
                 "and pass its build.json"]
    else:
        build = check_build(build_json, lock_pins)
    reasons += build
    details = {
        "reports": str(reports),
        "need": need,
        "versions_lock_sha256": lock,
        "slots": [{"slot": n, "clean": w is None, "why": w} for n, w in seen],
        "run": {"first": run[0] if run else None, "last": run[-1] if run else None, "length": len(run)},
        "first_unclean": first_unclean,
        "planted_stop": recovery,
        "build": {
            "in_reports": {"versions_lock_sha256": "RUN.json, per slot: the reporter's checkout, not a node's build",
                           "plugin_key": "each answer's verification line and peer-key.json: the key the node "
                                         "announced, which a rebuild or re-pin keeps",
                           "host_build": "not recorded", "plugin_build": "not recorded"},
            "build_json": str(build_json) if build_json else None,
            "build_json_problems": build if build_json else None,
            "declared_in_nodes": declared(nodes, lock_pins),
        },
        "reasons": reasons,
    }
    verdict = "CONFIDENT" if not reasons else "NOT YET: " + reasons[0] + \
        (f" (and {len(reasons) - 1} more)" if len(reasons) > 1 else "")
    if not reasons:
        verdict += f": {len(run)} clean slots {run[0]}..{run[-1]}, recovered at {recovery['next']}"
    details["verdict"] = verdict
    return verdict, details


def main(argv):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("reports", nargs="?", default="reports/rehearsal", help="a loop-data checkout's reports/rehearsal")
    p.add_argument("--slots", type=int, default=12, help="consecutive clean slots needed (default 12: two hours)")
    p.add_argument("--nodes", default=str(ROOT / "nodes"), help="the commitment list (default: this checkout's)")
    p.add_argument("--lock", default=str(ROOT / "versions.lock"), help="the current versions.lock")
    p.add_argument("--build-json", help="the build.json lab/lab.sh check wrote on the lab host")
    p.add_argument("--json", action="store_true", help="print the details too")
    args = p.parse_args(argv[1:])
    try:
        verdict, details = confidence(Path(args.reports), args.nodes, args.lock, args.slots, args.build_json)
    except (OSError, ValueError, KeyError, TypeError) as e:
        print(f"NOT YET: cannot read the rehearsal: {e}")
        return 1
    print(verdict)
    if args.json:
        print(json.dumps(details, indent=2))
    else:
        for r in details["reasons"][1:]:
            print(f"  also: {r}")
        for node_id, d in details["build"]["declared_in_nodes"].items():
            if not (d["names_capsules_pin"] and d["names_mesh_llm_pins"]):
                print(f"  note: nodes/{short(node_id)}... declares {d['plugin_version']!r}, not the versions.lock "
                      "pins (a declaration, not a measurement)")
    return 0 if verdict.startswith("CONFIDENT") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
