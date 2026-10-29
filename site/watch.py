#!/usr/bin/env python3
"""Build the watch page: the network as a graph, day by day, from committed files only.

Usage: watch.py [--daily DIR] [--rehearsal DIR] [--ondemand DIR] [--findings DIR] [--nodes DIR]
                [--population DIR] [--commitments DIR] [--bindings DIR] [--lock FILE] [--out DIR]
       (defaults: stats/data/daily, stats/data/rehearsal, no on-demand set, findings, nodes, population,
        lab/runs, no bindings, no lock, _site)

Writes DIR/watch/index.html, data.json, watch.js, watch-layout.js and the
vendored d3 (vendor/d3-7.9.0/), served at <pages root>/watch/. Run it after
site/build.py, which empties DIR.

data.json is the export the page draws (its keys are in site/README.md). It
holds two views, `live` (the daily set, with each on-demand run as its own
point between the days) and `rehearsal` (the reporter rehearsal, a TEST),
never one graph with both and never added together. Each view is one entry
per day (or 10-minute slot) from the first to the last: the nodes in that
day's file with the reporter's two asks on each, the nodes that left, and
every pair of the shown nodes, dotted unless a committed finding of step 05
(deal), 06 (settle) or 07 (ask) shows the two exchanged that day. What is
drawn is a dot per node, except that the subjects of a controller binding
verified by a tool pinned in the lock are one "bound" dot; each dot has a ring
per community its history cards cover. A step with
no finding for the day says "not measured yet", naming the step. Under the
graph, one count per chart over the view's days, from the same entries.

The fields come from the scrubbed sets (site/stats_model.py reads them,
strictly), nodes/<node_id>.json (operator, host_build, plugin_version), each
node's committed commitment (its stapled history cards: derivation and
issuing book) and findings that pass steps/finding.py's check. No answer
bytes, no amount, no band. The page carries the same JSON inline, so a
file:// load draws it; served, it re-reads its own data.json every minute.
The output depends only on the inputs. Standard library only.
"""
import base64
import datetime as dt
import hashlib
import html
import json
import math
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "steps"))
import stats_model  # noqa: E402
import finding  # noqa: E402

layout = stats_model.layout
PAGE = "watch"
FORMAT = "watch/2"
D3 = "vendor/d3-7.9.0"
# The graph's own size, in SVG units: the layout and the seeds are inside it.
SIZE = (760, 520)
# The reporter backend's own asks in a step 07 proof come from the node it runs on.
REPORTER = "reporter"
# A question's settled state as an outcome: a reply, a signed refusal, or nothing back.
OUTCOMES = ("answered", "refused", "absent")
# What makes a pair solid: something came back from the other node, a reply or a signed refusal.
EXCHANGED = ("answered", "refused")
# The edge kinds a loop step measures: kind -> (step, step directory name, what the edge is).
STEPS = {"deal": ("05", "05-deal", "a pushed half of a deal, sender to receiver"),
         "settle": ("06", "06-settle", "a settlement, payer to payee"),
         "ask07": ("07", "07-ask", "a later ask, asker to asked")}
VIEWS = (("live", "daily set", layout.Daily), ("rehearsal", "reporter rehearsal, a TEST", layout.Rehearsal))
NODE_FIELDS = ("operator", "host_build", "plugin_version")
RUN_DIR = re.compile(r"^(\d{4}-\d{2}-\d{2})-(rehearsal|live)$")
# An on-demand run's scrubbed file: the run's start, to the second.
ONDEMAND_FILE = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2})(\d{2})(\d{2})Z\.json$")
# The charts under the graph: key -> what it counts.
METRICS = (("nodes", "nodes listed"), ("records", "records sealed, at each node's checkpoint, all nodes"),
           ("answered", "asks answered"), ("refused", "asks refused"), ("absent", "asks absent"),
           ("deals", "deals (step 05)"), ("settlements", "settlements (step 06)"))

e = html.escape


def outcome(state):
    if state == "refusal":
        return "refused"
    return "answered" if state in layout.ANSWERED else "absent"


def attempt_outcome(state):
    """An attempt or verification line's first word, as an edge outcome."""
    return outcome(state if state in layout.ANSWERED else "absence")


def read_json(path):
    try:
        v = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return v


def listing(nodes_dir):
    """node id -> nodes/<node_id>.json, for each listed node; key id -> node id."""
    by_id, by_key = {}, {}
    for p in sorted(Path(nodes_dir).glob("*.json")):
        d = read_json(p)
        if isinstance(d, dict) and d.get("node_id") == p.stem:
            by_id[p.stem] = d
            if isinstance(d.get("key_id"), str):
                by_key[d["key_id"]] = p.stem
    return by_id, by_key


def names(population_dir, population):
    """A population's node name -> node id, where its population.json gives one."""
    pop = read_json(Path(population_dir) / population / "population.json") or {}
    return {n["name"]: n["node_id"] for n in pop.get("nodes", [])
            if isinstance(n, dict) and isinstance(n.get("name"), str) and stats_model.HEX64.match(str(n.get("node_id")))}


def seed(node_id):
    """A node's first place in the graph, from its id alone: the same on every load and every build."""
    h = hashlib.sha256(node_id.encode()).digest()
    a = int.from_bytes(h[:4], "big") / 2 ** 32 * 2 * math.pi
    r = 110 + int.from_bytes(h[4:6], "big") / 2 ** 16 * 90
    return [round(SIZE[0] / 2 + r * math.cos(a), 1), round(SIZE[1] / 2 + r * math.sin(a), 1)]


# --- History cards, from each node's committed commitment ---------------------

def commitments(commitments_dir, nodes_dir, by_id, by_key, rel):
    """node id -> (the commitment's path, the commitment), newest first wins.

    Where a commitment is committed: nodes/commitments/<node_id>.json, the
    carrier step 03 reads, when there is one; else the join's own copy,
    <commitments dir>/<date>/join/<name>/commitment.json, matched to the node
    by its key id (a commitment's node_id is the node's key id). Both
    evidence-commitment/0 and /1 are read."""
    out = {}
    for p in sorted(Path(commitments_dir).glob("*/join/*/commitment.json"), reverse=True):
        d = read_json(p)
        if isinstance(d, dict) and str(d.get("profile", "")).startswith("evidence-commitment/"):
            nid = by_key.get(d.get("key_id"))
            if nid and nid not in out:
                out[nid] = (rel(p), d)
    for nid in by_id:
        p = Path(nodes_dir) / "commitments" / f"{nid}.json"
        d = read_json(p)
        if isinstance(d, dict) and str(d.get("profile", "")).startswith("evidence-commitment/"):
            out[nid] = (rel(p), d)
    return out


def decoded(text):
    """A card's base64 (standard or url-safe, padded or not) as JSON, or None."""
    try:
        raw = base64.b64decode(str(text).replace("-", "+").replace("_", "/") + "=" * (-len(str(text)) % 4))
        v = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return v if isinstance(v, dict) else None


def witnesses(result):
    """The witnesses whose receipts a card carries, on the checkpoints of its history, in first-seen order."""
    found = []
    for h in result.get("history") or []:
        for r in (h.get("receipts") or []) if isinstance(h, dict) else []:
            w = r.get("witness") if isinstance(r, dict) else None
            if isinstance(w, str) and w and w not in found:
                found.append(w)
    return found


# A card's community, as the page names it. A community's own product name is not shown on this public page.
COMMUNITIES = {"mesh-llm": "mesh-llm", "muse": "personal-agents"}


def history_cards(commitment):
    """The history cards stapled to a commitment: each its stream, community, witnesses and whether it has history.

    evidence-commitment/1: `history_cards`, a list, one entry per stream, each
    naming its `community`. evidence-commitment/0: its single `history_card`,
    read as a one-entry list in the mesh-llm community, whose stream is the
    card's own log id (its `evidence_stream`). None stapled: an empty list.
    A card with no checkpoint in its history has no history, whether its
    stream is new or a cleared one started again: `since` is the day the
    stream started (the card's `stream_started`, else the day it was issued).
    Nothing of a card but these."""
    got = commitment.get("history_cards", commitment.get("history_card"))
    one = not isinstance(got, list)
    entries = got if isinstance(got, list) else [got] if isinstance(got, dict) else []
    out = []
    for c in entries:
        if not isinstance(c, dict):
            continue
        card = decoded(c.get("bytes")) or {}
        result = decoded(card.get("artifact")) or {}
        stream = c.get("stream")
        if not isinstance(stream, str):
            stream = result.get("evidence_stream")
        history = any(isinstance(h, dict) and h.get("checkpoint") for h in result.get("history") or [])
        since = result.get("stream_started") if isinstance(result.get("stream_started"), str) else card.get("issued_at")
        out.append({"stream": stream if isinstance(stream, str) else None,
                    "derivation": c.get("derivation") if isinstance(c.get("derivation"), str) else None,
                    "community": "mesh-llm" if one else COMMUNITIES.get(c.get("community"), "other"),
                    "witnesses": witnesses(result), "history": history,
                    "since": None if history or not isinstance(since, str) else since[:10]})
    return out


# --- Controller bindings --------------------------------------------------------

BINDING = "binding-verdict/0"


def pins(lock):
    """name@ref for each line of the lock the build reads."""
    out = set()
    try:
        text = Path(lock).read_text(encoding="utf-8") if lock else ""
    except OSError:
        return out
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 3 and not line.startswith("#"):
            out.add(f"{parts[0]}@{parts[2]}")
    return out


def bindings(bindings_dir, lock, by_id, by_key, rel):
    """(the bound groups, node id -> the verdict on it where it does not group).

    bindings/<controller_key_id>.json, a binding-verdict/0. Its subjects are
    one bound group only when its status is verified and its verifier names a
    tool pinned in the lock; revoked, not_checked, or verified by a tool not
    pinned: each subject stays its own dot, and its card says the binding is
    not verified. A missing directory: no groups. A node is in one group at most."""
    groups, notes, taken = [], {}, set()
    pinned = pins(lock)
    root = Path(bindings_dir) if bindings_dir else None
    for p in sorted(root.glob("*.json")) if root and root.is_dir() else []:
        d = read_json(p)
        if not (isinstance(d, dict) and d.get("profile") == BINDING and d.get("controller_key_id") == p.stem):
            continue
        subjects = sorted({by_key.get(x, x) for x in d.get("subjects") or [] if isinstance(x, str)} & set(by_id))
        verifier = d.get("verifier") if isinstance(d.get("verifier"), str) else None
        status = d.get("status")
        if status == "verified" and verifier in pinned and len(subjects) > 1 and not taken & set(subjects):
            taken |= set(subjects)
            # The controller's key id names the operator's key: it stays out of the page, its id and its path.
            groups.append({"id": "bound:" + hashlib.sha256(p.stem.encode()).hexdigest()[:16], "subjects": subjects,
                           "verifier": verifier, "checked_at": d.get("checked_at")})
            continue
        why = ("its verifier is not pinned in the lock" if status == "verified" else f"its status is {status}")
        for nid in subjects:
            notes.setdefault(nid, {"status": status if isinstance(status, str) else None, "says": "binding not verified",
                                   "why": why, "cites": d.get("cites") if isinstance(d.get("cites"), str) else None})
    return groups, notes


def rings(cards):
    """One ring per community the cards cover: with history if any of its cards has some, else since when none."""
    out = []
    for com in sorted({c["community"] for c in cards}):
        mine = [c for c in cards if c["community"] == com]
        history = any(c["history"] for c in mine)
        out.append({"community": com, "history": history, "streams": sorted(c["stream"] or "" for c in mine),
                    "since": None if history else min((c["since"] for c in mine if c["since"]), default=None)})
    return out


def drawn(entry, groups, nodes):
    """What a day draws: a dot per node, a verified group's shown subjects as one bound dot; and the links between."""
    out, of = [], {}
    shown = {n["node_id"] for n in entry["nodes"] if not n["left"]}
    for g in groups:
        here = [x for x in g["subjects"] if x in shown]
        if here:
            out.append({"id": g["id"], "members": here, "bound": True, "left": False,
                        "rings": rings([c for x in here for c in nodes[x]["history_cards"]])})
            of.update({x: g["id"] for x in here})
    for n in entry["nodes"]:
        if n["node_id"] not in of:
            out.append({"id": n["node_id"], "members": [n["node_id"]], "bound": False, "left": n["left"],
                        "rings": rings(nodes[n["node_id"]]["history_cards"])})
            of[n["node_id"]] = n["node_id"]
    links = {}
    for p in entry["pairs"]:
        a, b = sorted((of[p["a"]], of[p["b"]]))
        if a == b:
            continue
        got = links.setdefault((a, b), {"a": a, "b": b, "line": "dotted", "kinds": []})
        got["kinds"] = sorted(set(got["kinds"]) | set(p["kinds"]))
        got["line"] = "solid" if got["kinds"] else "dotted"
    entry["drawn"] = sorted(out, key=lambda x: x["id"])
    entry["links"] = [links[k] for k in sorted(links)]


# --- Edges from loop findings -------------------------------------------------

def lines(path):
    try:
        return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, ValueError):
        return []


def deal_edges(d, by_key, ok):
    """Step 05: one edge per side that should hold the other side's half: held, refused, or nothing held."""
    out = []
    sides = [p.name for p in sorted(d.iterdir()) if p.is_dir()]
    if ok(d / "deal.json"):  # the rehearsal driver's proof: <side>/push.json, refusal.json, held/
        for rx in sides:
            for tx in (s for s in sides if s != rx):
                if ok(d / rx / "refusal.json"):
                    got = "refused"
                elif ok(d / rx / "held" / "received-provenance.jsonl") and lines(d / rx / "held" / "received-provenance.jsonl"):
                    got = "answered"
                else:
                    got = "absent"
                out.append((tx, rx, got))
        return out
    for rx in sides:  # the live step: <node>/checks.json, one entry per half the node holds
        checks = (read_json(d / rx / "checks.json") if ok(d / rx / "checks.json") else None) or {}
        for held in checks.get("held", []):
            m = re.match(r"received from (\S+)", str((held.get("provenance") or {}).get("detail", "")))
            out.append((m.group(1) if m else None, rx, "answered"))
    return out


def settle_edges(d, by_key, ok):
    """Step 06: the exchange's payer to its payee, settled when the finding's checks on it held."""
    ex = read_json(d / "exchange.json") if ok(d / "exchange.json") else None
    if not isinstance(ex, dict):
        return []
    checks = ((read_json(d / "checks.json") if ok(d / "checks.json") else None) or {}).get("checks", {})
    final = all((checks.get(k) or {}).get("ok") is True for k in ("reference", "finality", "exchange_match"))
    return [(by_key.get(ex.get("payer"), None), by_key.get(ex.get("payee"), None), "answered" if final else "absent")]


def ask07_edges(d, by_key, ok):
    """Step 07: <asker>/asks/<asked>/<kind>/outcome.json, or the reporter backend's attempt per node and question."""
    out = []
    for o in sorted(x for x in d.glob("*/asks/*/*/outcome.json") if ok(x)):
        got = (read_json(o) or {}).get("outcome")
        out.append((o.parts[-5], o.parts[-3],
                    {"artifact": "answered", "refusal": "refused"}.get(got, "absent")))
    for v in sorted(x for x in d.glob("*/attempts/*/*.verification") if ok(x)):
        word = v.read_text(encoding="utf-8", errors="replace").split(" ", 1)[0].strip()
        out.append((REPORTER, v.parts[-4], attempt_outcome(word)))
    return out


EDGES = {"deal": deal_edges, "settle": settle_edges, "ask07": ask07_edges}


def findings_by_day(findings_dir):
    """(date, population) -> the run directory under findings/."""
    out = {}
    root = Path(findings_dir)
    if root.is_dir():
        for p in sorted(root.iterdir()):
            m = RUN_DIR.match(p.name)
            if m and p.is_dir():
                out[m.groups()] = p
    return out


def slot_of(rec):
    """The rehearsal slot a finding's run started in, or None."""
    try:
        return layout.Rehearsal.name(layout.Rehearsal.of(dt.datetime.strptime(rec["started"], "%Y-%m-%dT%H:%M:%SZ")))
    except (KeyError, TypeError, ValueError):
        return None


def measured(kind, run, population, ids, by_key, shown, host, rel, date):
    """The edges one step's finding holds for a day (or the rehearsal slot its run started in), or why none."""
    step, dirname, _ = STEPS[kind]
    d = run / dirname if run else None
    rec = read_json(d / "finding.json") if d else None
    if isinstance(rec, dict) and population == "rehearsal" and slot_of(rec) != date:
        rec = None
    if not isinstance(rec, dict):
        return {"measured": False, "step": step,
                "says": f"not measured yet: no step {step} finding for this {'slot' if population == 'rehearsal' else 'day'}"}, []
    if finding.check_record(d, rec) or rec.get("population") != population:
        return {"measured": False, "step": step, "finding": rel(d / "finding.json"),
                "says": f"not measured yet: the step {step} finding does not pass the format check"}, []
    drawn, skipped = [], 0
    declared = {p["path"] for p in rec["proof"]}  # only bytes the finding hashes may draw a line
    for a, b, got in EDGES[kind](d, by_key, lambda f: f.relative_to(d).as_posix() in declared):
        a, b = (host if x == REPORTER else ids.get(x, x) for x in (a, b))
        if a in shown and b in shown and a != b:
            drawn.append({"kind": kind, "from": a, "to": b, "outcome": got})
        else:
            skipped += 1
    note = {"measured": True, "step": step, "finding": rel(d / "finding.json"), "result": rec["result"],
            "drawn": len(drawn), "answered": sum(x["outcome"] == "answered" for x in drawn), "not_drawn": skipped}
    if skipped:
        note["why_not_drawn"] = ("an end is not a node of this view on this " + ("slot" if population == "rehearsal" else "day")
                                 + ": not listed then, or a node of the loop's own TEST population")
    return note, sorted(drawn, key=lambda x: (x["from"], x["to"], x["outcome"]))


def pairs(shown, edges):
    """Every pair of the shown nodes: solid where a step's edge between the two brought something back, else dotted."""
    ids = sorted(shown)
    out = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            kinds = sorted({x["kind"] for x in edges if {x["from"], x["to"]} == {a, b} and x["outcome"] in EXCHANGED})
            out.append({"a": a, "b": b, "line": "solid" if kinds else "dotted", "kinds": kinds})
    return out


# --- The on-demand set ----------------------------------------------------------

def read_ondemand(path, problems):
    """One on-demand run's scrubbed file, held to the scrubbed format as stats_model holds a day file."""
    where = path.name
    n = len(problems)
    m = ONDEMAND_FILE.match(path.name)
    if not m:
        problems.append(f"{where}: not a <YYYY-MM-DD>T<HHMMSS>Z.json on-demand file")
        return None
    try:
        dt.datetime.strptime(path.name[:-5], "%Y-%m-%dT%H%M%SZ")
    except ValueError:
        problems.append(f"{where}: not a UTC time")
        return None
    raw = path.read_bytes()
    try:
        d = json.loads(raw)
    except ValueError as err:
        problems.append(f"{where}: not JSON ({err})")
        return None
    if not stats_model._keys(d, stats_model.DAY_KEYS, where, problems):
        return None
    if raw not in stats_model.canonical(d):
        problems.append(f"{where}: not in the fixed form (sorted keys, 2-space indent, trailing newline)")
    if d["date"] != path.name[:-5]:
        problems.append(f"{where}: date is {d['date']!r}, the file name says {path.name[:-5]}")
    if d["run"] not in ("settled", "missed"):
        problems.append(f"{where}: run must be settled or missed")
    if (d["run"] == "missed") != isinstance(d["missed_reason"], str) or d["missed_reason"] == "":
        problems.append(f"{where}: missed_reason is a non-empty string exactly when the run is missed")
    for key in ("versions_lock_sha256", "reporter_key_id"):
        if not stats_model._hex_or_null(d[key]):
            problems.append(f"{where}: {key} is not 64 hex or null")
    if not stats_model._int(d["listed"]) or not isinstance(d["nodes"], list):
        problems.append(f"{where}: listed is not a whole number, or nodes is not a list")
        return None
    if d["run"] == "missed" and d["nodes"]:
        problems.append(f"{where}: a missed run has no nodes")
    if len(d["nodes"]) > d["listed"]:
        problems.append(f"{where}: {len(d['nodes'])} nodes but only {d['listed']} listed")
    nodes = [x for x in (stats_model.read_node(node, f"{where}: nodes[{i}]", problems)
                         for i, node in enumerate(d["nodes"])) if x]
    if [x.node_id for x in nodes] != sorted({x.node_id for x in nodes}):
        problems.append(f"{where}: nodes are not sorted by node_id, or one is given twice")
    if len(problems) > n:
        return None
    return stats_model.Day(d["date"], d["run"], d["missed_reason"], d["versions_lock_sha256"], d["reporter_key_id"],
                           d["listed"], nodes, path.name)


def load_ondemand(root):
    """Every on-demand run under root, earliest first; no directory, no runs."""
    root = Path(root) if root else None
    if root is None or not root.is_dir():
        return []
    problems, runs = [], []
    for path in sorted(root.iterdir()):
        if path.name in stats_model.SKIP:
            continue
        if not path.is_file() or path.is_symlink():
            problems.append(f"{path.name}: not an on-demand file")
            continue
        got = read_ondemand(path, problems)
        if got:
            runs.append(got)
    if problems:
        raise stats_model.StatsError(problems)
    return runs


def ondemand_label(name):
    return f"on-demand {name[11:13]}:{name[13:15]}:{name[15:17]}Z"


# --- One view ---------------------------------------------------------------

def asked(n):
    """The reporter's two asks to one node, each where it settled."""
    out = []
    for stem, a in sorted(n.answers.items()):
        settled = next((t for t in a.attempts if t.state in layout.ANSWERED), a.attempts[-1])
        out.append({"question": stats_model.QUESTIONS[stem], "outcome": outcome(a.state), "state": a.state,
                    "at": settled.at, "attempts": [{"at": t.at, "state": t.state} for t in a.attempts]})
    return out


def entry_nodes(day, on, seen, first, left, date):
    """A day's (or run's) nodes: each in the file, then each that left and is not in it."""
    out = []
    here = {n.node_id for n in day.nodes} if day else set()
    for n in (day.nodes if day else []):
        asks = {k: 0 for k in OUTCOMES}
        for a in n.answers.values():
            asks[outcome(a.state)] += 1
        cp = n.answers.get("checkpoints")
        out.append({
            "node_id": n.node_id, "state": stats_model.node_day(n),
            "questions": {stats_model.QUESTIONS[s]: a.state for s, a in sorted(n.answers.items())},
            "asked": asked(n), "asks": asks, "records_at_checkpoint": cp.records if cp else None,
            "days_listed": seen.get(n.node_id, 0), "first_day": first.get(n.node_id) == date,
            "left_on": n.left_on, "left": bool(n.left_on and on >= n.left_on), "node_line": n.node_line})
    for nid, when in sorted(left.items()):
        if nid not in here and first.get(nid, "~") <= date and on >= when:
            out.append({"node_id": nid, "left_on": when, "left": True, "days_listed": seen.get(nid, 0)})
    return out


def view(name, label, period, data_dir, findings, by_key, population_dir, rel, ondemand=()):
    days = stats_model.load(data_dir, period) if data_dir and Path(data_dir).is_dir() else []
    cal = (stats_model.calendar if period is layout.Daily else stats_model.slots)(days)
    left = stats_model.left_on(days)
    first = stats_model.first_seen(days)
    ids = names(population_dir, name)
    runs = findings_by_day(findings)
    seen = {}  # node id -> day files it is in, to this day
    counts = []  # the seen counts after each day, for an on-demand run that falls after it
    out = []
    for date, day in cal:
        on = date[:10]
        for n in (day.nodes if day else []):
            seen[n.node_id] = seen.get(n.node_id, 0) + 1
        counts.append((date, dict(seen)))
        entry = {"date": date, "set": period.set, "label": date, "run": "no_file" if day is None else day.run,
                 "file": f"stats/data/{period.set}/{day.file}" if day else None,
                 "listed": day.listed if day else None, "nodes": entry_nodes(day, on, seen, first, left, date),
                 "edges": [], "measured": {}}
        host = None
        if day is not None and not day.missed:
            entry["reporter_key_id"] = day.reporter_key_id
            host = by_key.get(day.reporter_key_id)
        entry["reporter_runs_on"] = host
        shown = {n["node_id"] for n in entry["nodes"] if not n["left"]}
        if day is None or day.missed:
            entry["measured"]["ask"] = {"measured": False, "says": "no day file" if day is None
                                        else f"the reporter missed this {period.noun}"}
        else:
            entry["measured"]["ask"] = {"measured": True, "source": entry["file"]}
        run = runs.get((on, name))
        for kind in STEPS:
            note, edges = measured(kind, run, name, ids, by_key, shown, host, rel, date)
            entry["measured"][kind] = note
            entry["edges"] += edges
        entry["pairs"] = pairs(shown, entry["edges"])
        out.append(entry)
    # Each on-demand run is its own point between the days: never a day, and nothing of it reaches a day.
    for run in ondemand:
        before = [c for d, c in counts if d <= run.date[:10]]
        entry = {"date": run.date, "set": "ondemand", "label": ondemand_label(run.date), "run": run.run,
                 "file": f"stats/data/ondemand/{run.file}", "listed": run.listed,
                 "nodes": entry_nodes(run, run.date[:10], before[-1] if before else {}, first, left, run.date),
                 "edges": [], "measured": {}}
        for n in entry["nodes"]:  # a node's first day is a day file's, never a run's
            if "first_day" in n:
                n["first_day"] = False
        host = by_key.get(run.reporter_key_id) if not run.missed else None
        if not run.missed:
            entry["reporter_key_id"] = run.reporter_key_id
        entry["reporter_runs_on"] = host
        entry["measured"]["ask"] = ({"measured": False, "says": "the reporter missed this on-demand run"} if run.missed
                                    else {"measured": True, "source": entry["file"]})
        for kind, (step, _, _) in STEPS.items():
            entry["measured"][kind] = {"measured": False, "step": step,
                                       "says": f"not measured yet: an on-demand run is an ask round only; step {step} "
                                               "runs in the daily loop"}
        entry["pairs"] = pairs({n["node_id"] for n in entry["nodes"] if not n["left"]}, [])
        out.append(entry)
    out.sort(key=lambda x: x["date"])
    v = {"label": label, "unit": period.noun, "test": name == "rehearsal", "days": out}
    v["metrics"] = metrics(v)
    return v


def point(d, key):
    """One chart's value for one day or run, or why there is none."""
    shown = [n for n in d["nodes"] if not n["left"]]
    if key in ("deals", "settlements"):
        m = d["measured"]["deal" if key == "deals" else "settle"]
        if not m["measured"]:
            return {"value": None, "says": m["says"]}
        # A settlement that did not hold is drawn (dashed or dotted) but is not counted as a settlement.
        return {"value": m["answered"] if key == "settlements" else m["drawn"], "not_drawn": m["not_drawn"]}
    if d["run"] != "settled":
        return {"value": None, "says": "no file" if d["run"] == "no_file" else "the reporter missed it"}
    if key == "nodes":
        return {"value": len(shown)}
    if key == "records":
        known = [n["records_at_checkpoint"] for n in shown if n["records_at_checkpoint"] is not None]
        return {"value": sum(known)} if known else {"value": None, "says": "no checkpoint size in any answer"}
    return {"value": sum(n["asks"][key] for n in shown)}


def metrics(v):
    """One count per chart, x = the view's days; an on-demand run is its own point, never in a day's count.

    Steps 05 and 06 run in the daily loop only, so their charts have no on-demand points."""
    out = []
    for key, label in METRICS:
        pts = [{"date": d["date"], "set": d["set"], "label": d["label"]} | point(d, key) for d in v["days"]
               if not (d["set"] == "ondemand" and key in ("deals", "settlements"))]
        out.append({"key": key, "label": label, "points": pts})
    return out


def export(daily, rehearsal, findings, nodes_dir, population_dir, commitments_dir="lab/runs", ondemand=None,
           bindings_dir=None, lock=None):
    by_id, by_key = listing(nodes_dir)
    root = Path(findings).resolve().parent

    def rel(p):
        p = Path(p).resolve()
        return p.relative_to(root).as_posix() if p.is_relative_to(root) else p.name

    runs = load_ondemand(ondemand)
    views = {}
    for name, label, period in VIEWS:
        views[name] = view(name, label, period, daily if name == "live" else rehearsal, findings, by_key,
                           population_dir, rel, runs if name == "live" else ())
    used = sorted({n["node_id"] for v in views.values() for d in v["days"] for n in d["nodes"]})
    held = commitments(commitments_dir, nodes_dir, by_id, by_key, rel)
    nodes = {}
    for nid in used:
        n = ({k: by_id[nid].get(k) for k in NODE_FIELDS} | {"listed": True}) if nid in by_id else {"listed": False}
        path, c = held.get(nid, (None, {}))
        nodes[nid] = n | {"seed": seed(nid), "commitment": path, "history_cards": history_cards(c), "binding": None}
    groups, notes = bindings(bindings_dir, lock, by_id, by_key, rel)
    groups = [g | {"subjects": [x for x in g["subjects"] if x in nodes]} for g in groups]
    for g in groups:
        for x in g["subjects"]:
            nodes[x]["binding"] = {"status": "verified", "group": g["id"]}
    for x, note in notes.items():
        if x in nodes:
            nodes[x]["binding"] = note
    for v in views.values():
        for d in v["days"]:
            drawn(d, groups, nodes)
    return {"format": FORMAT, "edge_kinds": {k: v[2] for k, v in STEPS.items()}, "outcomes": list(OUTCOMES),
            "size": list(SIZE), "nodes": nodes, "groups": groups, "views": views}


# --- The page ----------------------------------------------------------------

def day_row(d):
    nodes = [n for n in d["nodes"] if not n["left"]]
    left = len(d["nodes"]) - len(nodes)
    asks = [a for n in nodes for a in n.get("asked", [])]
    solid = sum(1 for p in d["pairs"] if p["line"] == "solid")
    cells = [f'<td><time>{e(d["label"])}</time></td>', f'<td>{e(d["run"].replace("_", " "))}</td>']
    if d["listed"] is None or d["run"] == "missed":
        cells.append('<td class="num">-</td>')
    else:
        cells.append(f'<td class="num" data-k="nodes">{len(nodes)} of {d["listed"]} listed</td>')
    cells.append(f'<td class="num" data-k="left">{left}</td>')
    for o in OUTCOMES:
        n = sum(1 for x in asks if x["outcome"] == o)
        cells.append(f'<td class="num" data-k="ask-{o}">{n} of {len(asks)} asks</td>' if asks
                     else f'<td class="num" data-k="ask-{o}">-</td>')
    cells.append(f'<td class="num" data-k="pairs">{solid} solid of {len(d["pairs"])}</td>')
    for kind in STEPS:
        m = d["measured"][kind]
        if m["measured"]:
            got = [x for x in d["edges"] if x["kind"] == kind]
            words = ", ".join(f"{sum(1 for x in got if x['outcome'] == o)} {o}" for o in OUTCOMES)
            cells.append(f'<td data-k="{kind}">{words}; {m["not_drawn"]} not drawn</td>')
        else:
            cells.append(f'<td data-k="{kind}" class="unmeasured">{e(m["says"])}</td>')
    return f'<tr data-date="{e(d["date"])}" data-set="{e(d["set"])}">{"".join(cells)}</tr>'


def table(name, v, which):
    rows = [d for d in reversed(v["days"]) if (d["set"] == "ondemand") == (which == "ondemand")]
    if which == "ondemand" and not rows:
        return ""
    unit = "on-demand run" if which == "ondemand" else v["unit"]
    head = ('<tr><th scope="col">' + unit + ' (UTC)</th><th scope="col">run</th><th scope="col">nodes</th>'
            '<th scope="col">left</th><th scope="col">asks answered</th><th scope="col">asks refused</th>'
            '<th scope="col">asks absent</th><th scope="col">pairs</th><th scope="col">deal edges (05)</th>'
            '<th scope="col">settle edges (06)</th><th scope="col">ask edges (07)</th></tr>')
    test = '<span class="test">TEST</span> ' if v["test"] else ""
    what = ("Each on-demand run, its own row, never a day's" if which == "ondemand"
            else f'{e(v["label"].capitalize())}: the graph\'s counts, one row per {v["unit"]}')
    return (f'<details class="fallback" data-view="{name}" data-set="{which}">\n<summary>{test}{what}, newest first'
            f'</summary>\n<div class="scroll"><table>\n{head}\n' + "\n".join(day_row(d) for d in rows)
            + '\n</table></div>\n</details>')


def metric_cell(p):
    if p["value"] is None:
        return f'<td data-v class="unmeasured">{e(p["says"])}</td>'
    extra = f' ({p["not_drawn"]} not drawn)' if p.get("not_drawn") else ""
    return f'<td data-v>{p["value"]}{extra}</td>'


def metric_tables(name, v):
    """Each chart's numbers as text: the same points the chart draws."""
    out = []
    for m in v["metrics"]:
        rows = "\n".join(f'<tr data-date="{e(p["date"])}" data-set="{e(p["set"])}"><th scope="row">{e(p["label"])}'
                         f'</th>{metric_cell(p)}</tr>' for p in m["points"])
        out.append(f'<figure class="metric" data-view="{name}" data-metric="{m["key"]}">\n'
                   f'<figcaption>{e(m["label"])}</figcaption>\n<div class="chart" aria-hidden="true"></div>\n'
                   f'<details><summary>the numbers</summary><div class="scroll"><table>\n'
                   f'<tr><th scope="col">{v["unit"]} or run (UTC)</th><th scope="col">count</th></tr>\n{rows}\n'
                   f'</table></div></details>\n</figure>')
    return f'<div class="metrics" data-view="{name}">\n' + "\n".join(out) + "\n</div>"


def render(data):
    template = (HERE / "watch.html").read_text(encoding="utf-8")
    blob = json.dumps(data, sort_keys=True, separators=(",", ":")).replace("</", "<\\/")
    tables = "\n".join(table(n, v, w) for n, v in data["views"].items() for w in ("days", "ondemand"))
    charts = "\n".join(metric_tables(n, v) for n, v in data["views"].items())
    return template.replace("$tables", tables).replace("$metrics", charts).replace("$data", blob)


def build(daily, rehearsal, findings, nodes_dir, population_dir, out, commitments_dir="lab/runs", ondemand=None,
          bindings_dir=None, lock=None):
    data = export(daily, rehearsal, findings, nodes_dir, population_dir, commitments_dir, ondemand,
                  bindings_dir, lock)  # raise before anything is written
    site = Path(out) / PAGE
    if site.exists():
        shutil.rmtree(site)
    (site / D3).mkdir(parents=True)
    (site / "data.json").write_text(json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (site / "index.html").write_text(render(data), encoding="utf-8")
    for f in ("watch.js", "watch-layout.js"):
        shutil.copyfile(HERE / f, site / f)
    for f in ("d3.min.js", "LICENSE"):
        shutil.copyfile(HERE / D3 / f, site / D3 / f)
    return data


def main(argv):
    args = argv[1:]
    opts = {"--daily": "stats/data/daily", "--rehearsal": "stats/data/rehearsal", "--ondemand": None,
            "--findings": "findings", "--nodes": "nodes", "--population": "population", "--commitments": "lab/runs",
            "--bindings": None, "--lock": None, "--out": "_site"}
    while args:
        if args[0] not in opts or len(args) < 2:
            print("\n".join(__doc__.splitlines()[2:6]), file=sys.stderr)
            return 2
        opts[args[0]] = args[1]
        args = args[2:]
    try:
        data = build(opts["--daily"], opts["--rehearsal"], opts["--findings"], opts["--nodes"],
                     opts["--population"], opts["--out"], opts["--commitments"], opts["--ondemand"],
                     opts["--bindings"], opts["--lock"])
    except stats_model.StatsError as err:
        for p in err.problems:
            print(p, file=sys.stderr)
        print("the watch page was not built", file=sys.stderr)
        return 1
    live = data["views"]["live"]["days"]
    n = {"live": sum(1 for d in live if d["set"] == "daily"), "ondemand": sum(1 for d in live if d["set"] == "ondemand"),
         "rehearsal": len(data["views"]["rehearsal"]["days"])}
    print(f"{opts['--out']}/{PAGE}/index.html: {n['live']} live day{'' if n['live'] == 1 else 's'}, "
          f"{n['ondemand']} on-demand run{'' if n['ondemand'] == 1 else 's'}, "
          f"{n['rehearsal']} rehearsal slot{'' if n['rehearsal'] == 1 else 's'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
