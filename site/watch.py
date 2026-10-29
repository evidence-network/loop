#!/usr/bin/env python3
"""Build the watch page: the network as a graph, day by day, from committed files only.

Usage: watch.py [--daily DIR] [--rehearsal DIR] [--findings DIR] [--nodes DIR] [--population DIR] [--out DIR]
       (defaults: stats/data/daily, stats/data/rehearsal, findings, nodes, population, _site)

Writes DIR/watch/index.html, DIR/watch/data.json and DIR/watch/watch.js,
served at <pages root>/watch/. Run it after site/build.py, which empties DIR.

data.json is the export the page draws (its keys are in site/README.md). It
holds two views, `live` (the daily set) and `rehearsal` (the reporter
rehearsal, a TEST), never one graph with both and never added together. Each
view is one entry per day (or 10-minute slot) from the first to the last: the
nodes in that day's file, the nodes that left, the reporter's asks as edges,
and, for each edge kind a loop step measures (05 deal, 06 settle, 07 ask),
the edges its finding for that day holds, or "not measured yet" naming the
step. An edge is drawn only between two nodes of the view; one whose ends are
not both nodes of the view is counted as not drawn, with the reason.

The fields come from the scrubbed set (site/stats_model.py reads it,
strictly), nodes/<node_id>.json (operator, host_build, plugin_version) and
findings that pass steps/finding.py's check. No answer bytes, no amount, no
band. The page carries the same JSON inline, so it loads nothing; watch.js is
handwritten, standard DOM and SVG only. Without scripts, the tables under the
graph hold the same counts. The output depends only on the inputs. Standard
library only.
"""
import datetime as dt
import html
import json
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
FORMAT = "watch/1"
REPORTER = "reporter"
# A question's settled state as an edge outcome: a reply, a signed refusal, or nothing back.
OUTCOMES = ("answered", "refused", "absent")
# The edge kinds a loop step measures: kind -> (step, step directory name, what the edge is).
STEPS = {"deal": ("05", "05-deal", "a pushed half of a deal, sender to receiver"),
         "settle": ("06", "06-settle", "a settlement, payer to payee"),
         "ask07": ("07", "07-ask", "a later ask, asker to asked")}
VIEWS = (("live", "daily set", layout.Daily), ("rehearsal", "reporter rehearsal, a TEST", layout.Rehearsal))
NODE_FIELDS = ("operator", "host_build", "plugin_version")
RUN_DIR = re.compile(r"^(\d{4}-\d{2}-\d{2})-(rehearsal|live)$")

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


# --- Edges from loop findings -------------------------------------------------

def lines(path):
    try:
        return [json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, ValueError):
        return []


def deal_edges(d, ids, by_key):
    """Step 05: one edge per side that should hold the other side's half: held, refused, or nothing held."""
    out = []
    sides = [p.name for p in sorted(d.iterdir()) if p.is_dir()]
    if (d / "deal.json").is_file():  # the rehearsal driver's proof: <side>/push.json, refusal.json, held/
        for rx in sides:
            for tx in (s for s in sides if s != rx):
                if (d / rx / "refusal.json").is_file():
                    got = "refused"
                elif lines(d / rx / "held" / "received-provenance.jsonl"):
                    got = "answered"
                else:
                    got = "absent"
                out.append((tx, rx, got))
        return out
    for rx in sides:  # the live step: <node>/checks.json, one entry per half the node holds
        checks = read_json(d / rx / "checks.json") or {}
        for held in checks.get("held", []):
            m = re.match(r"received from (\S+)", str((held.get("provenance") or {}).get("detail", "")))
            out.append((m.group(1) if m else None, rx, "answered"))
    return out


def settle_edges(d, ids, by_key):
    """Step 06: the exchange's payer to its payee, settled when the finding's checks on it held."""
    ex = read_json(d / "exchange.json")
    if not isinstance(ex, dict):
        return []
    checks = (read_json(d / "checks.json") or {}).get("checks", {})
    final = all((checks.get(k) or {}).get("ok") is True for k in ("reference", "finality", "exchange_match"))
    return [(by_key.get(ex.get("payer"), None), by_key.get(ex.get("payee"), None), "answered" if final else "absent")]


def ask07_edges(d, ids, by_key):
    """Step 07: <asker>/asks/<asked>/<kind>/outcome.json, or the reporter's attempt per node and question."""
    out = []
    for o in sorted(d.glob("*/asks/*/*/outcome.json")):
        got = (read_json(o) or {}).get("outcome")
        out.append((o.parts[-5], o.parts[-3],
                    {"artifact": "answered", "refusal": "refused"}.get(got, "absent")))
    for v in sorted(d.glob("*/attempts/*/*.verification")):
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


def measured(kind, run, population, ids, by_key, shown, rel, date):
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
    for a, b, got in EDGES[kind](d, ids, by_key):
        a, b = ids.get(a, a), ids.get(b, b)
        if a in shown and b in shown and a != b:
            drawn.append({"kind": kind, "from": a, "to": b, "outcome": got})
        else:
            skipped += 1
    note = {"measured": True, "step": step, "finding": rel(d / "finding.json"), "result": rec["result"],
            "drawn": len(drawn), "not_drawn": skipped}
    if skipped:
        note["why_not_drawn"] = ("an end is not a node of this view on this " + ("slot" if population == "rehearsal" else "day")
                                 + ": not listed then, or a node of the loop's own TEST population")
    return note, sorted(drawn, key=lambda x: (x["from"], x["to"], x["outcome"]))


# --- One view ---------------------------------------------------------------

def ask_edges(day):
    out = []
    for n in day.nodes:
        for stem, a in sorted(n.answers.items()):
            settled = next((t for t in a.attempts if t.state in layout.ANSWERED), a.attempts[-1])
            out.append({"kind": "ask", "from": REPORTER, "to": n.node_id, "question": stats_model.QUESTIONS[stem],
                        "outcome": outcome(a.state), "state": a.state, "at": settled.at,
                        "attempts": [{"at": t.at, "state": t.state} for t in a.attempts]})
    return out


def view(name, label, period, data_dir, findings, by_key, population_dir, rel):
    days = stats_model.load(data_dir, period) if data_dir and Path(data_dir).is_dir() else []
    cal = (stats_model.calendar if period is layout.Daily else stats_model.slots)(days)
    left = stats_model.left_on(days)
    first = stats_model.first_seen(days)
    ids = names(population_dir, name)
    runs = findings_by_day(findings)
    seen = {}  # node id -> day files it is in, to this day
    out = []
    for date, day in cal:
        on = date[:10]
        entry = {"date": date, "run": "no_file" if day is None else day.run,
                 "file": f"stats/data/{'daily' if period is layout.Daily else 'rehearsal'}/{day.file}" if day else None,
                 "listed": day.listed if day else None, "nodes": [], "edges": [], "measured": {}}
        if day is not None and not day.missed:
            entry["reporter_key_id"] = day.reporter_key_id
        here = {n.node_id for n in day.nodes} if day else set()
        for n in (day.nodes if day else []):
            seen[n.node_id] = seen.get(n.node_id, 0) + 1
            asks = {k: 0 for k in OUTCOMES}
            for a in n.answers.values():
                asks[outcome(a.state)] += 1
            cp = n.answers.get("checkpoints")
            entry["nodes"].append({
                "node_id": n.node_id, "state": stats_model.node_day(n),
                "questions": {stats_model.QUESTIONS[s]: a.state for s, a in sorted(n.answers.items())},
                "asks": asks, "records_at_checkpoint": cp.records if cp else None,
                "days_listed": seen[n.node_id], "first_day": first.get(n.node_id) == date,
                "left_on": n.left_on, "left": bool(n.left_on and on >= n.left_on), "node_line": n.node_line})
        for nid, when in sorted(left.items()):
            if nid not in here and first.get(nid, "~") <= date and on >= when:
                entry["nodes"].append({"node_id": nid, "left_on": when, "left": True, "days_listed": seen.get(nid, 0)})
        shown = {n["node_id"] for n in entry["nodes"] if not n["left"]} | {REPORTER}
        if day is None or day.missed:
            entry["measured"]["ask"] = {"measured": False, "says": "no day file" if day is None
                                        else f"the reporter missed this {period.noun}"}
        else:
            entry["edges"] = ask_edges(day)
            entry["measured"]["ask"] = {"measured": True, "source": entry["file"]}
        run = runs.get((on, name))
        for kind in STEPS:
            note, edges = measured(kind, run, name, ids, by_key, shown, rel, date)
            entry["measured"][kind] = note
            entry["edges"] += edges
        out.append(entry)
    return {"label": label, "unit": period.noun, "test": name == "rehearsal", "days": out}


def export(daily, rehearsal, findings, nodes_dir, population_dir):
    by_id, by_key = listing(nodes_dir)
    root = Path(findings).resolve().parent

    def rel(p):
        p = Path(p).resolve()
        return p.relative_to(root).as_posix() if p.is_relative_to(root) else p.name

    views = {}
    for name, label, period in VIEWS:
        views[name] = view(name, label, period, daily if name == "live" else rehearsal, findings, by_key,
                           population_dir, rel)
    used = sorted({n["node_id"] for v in views.values() for d in v["days"] for n in d["nodes"]})
    nodes = {nid: ({k: by_id[nid].get(k) for k in NODE_FIELDS} | {"listed": True}) if nid in by_id
             else {"listed": False} for nid in used}
    reporter = {}
    for v in views.values():
        for d in v["days"]:
            k = d.get("reporter_key_id")
            if k:
                reporter[k] = by_key.get(k)
    return {"format": FORMAT, "edge_kinds": {"ask": "the reporter's ask, one per node per question",
                                             **{k: v[2] for k, v in STEPS.items()}},
            "outcomes": list(OUTCOMES), "nodes": nodes,
            "reporters": [{"key_id": k, "node_id": reporter[k]} for k in sorted(reporter)], "views": views}


# --- The page ----------------------------------------------------------------

def count(n, m, what):
    return f'<span class="count">{n} of {m} {what}</span>'


def day_row(d):
    nodes = [n for n in d["nodes"] if not n["left"]]
    left = len(d["nodes"]) - len(nodes)
    asks = [x for x in d["edges"] if x["kind"] == "ask"]
    cells = [f'<td><time>{e(d["date"])}</time></td>', f'<td>{e(d["run"].replace("_", " "))}</td>']
    if d["listed"] is None or d["run"] == "missed":
        cells.append('<td class="num">-</td>')
    else:
        cells.append(f'<td class="num" data-k="nodes">{len(nodes)} of {d["listed"]} listed</td>')
    cells.append(f'<td class="num" data-k="left">{left}</td>')
    for o in OUTCOMES:
        n = sum(1 for x in asks if x["outcome"] == o)
        cells.append(f'<td class="num" data-k="ask-{o}">{n} of {len(asks)} asks</td>' if asks
                     else f'<td class="num" data-k="ask-{o}">-</td>')
    for kind in STEPS:
        m = d["measured"][kind]
        if m["measured"]:
            got = [x for x in d["edges"] if x["kind"] == kind]
            words = ", ".join(f"{sum(1 for x in got if x['outcome'] == o)} {o}" for o in OUTCOMES)
            cells.append(f'<td data-k="{kind}">{words}; {m["not_drawn"]} not drawn</td>')
        else:
            cells.append(f'<td data-k="{kind}" class="unmeasured">{e(m["says"])}</td>')
    return f'<tr data-date="{e(d["date"])}">{"".join(cells)}</tr>'


def table(name, v):
    head = ('<tr><th scope="col">' + v["unit"] + ' (UTC)</th><th scope="col">run</th><th scope="col">nodes</th>'
            '<th scope="col">left</th><th scope="col">asks answered</th><th scope="col">asks refused</th>'
            '<th scope="col">asks absent</th><th scope="col">deal edges (05)</th><th scope="col">settle edges (06)'
            '</th><th scope="col">ask edges (07)</th></tr>')
    rows = "\n".join(day_row(d) for d in reversed(v["days"]))
    test = '<span class="test">TEST</span> ' if v["test"] else ""
    return (f'<details class="fallback" data-view="{name}" open>\n<summary>{test}{e(v["label"].capitalize())}: the '
            f'graph\'s counts, one row per {v["unit"]}, newest first</summary>\n<div class="scroll"><table>\n'
            f'{head}\n{rows}\n</table></div>\n</details>')


def render(data):
    template = (HERE / "watch.html").read_text(encoding="utf-8")
    blob = json.dumps(data, sort_keys=True, separators=(",", ":")).replace("</", "<\\/")
    tables = "\n".join(table(n, v) for n, v in data["views"].items())
    return (template.replace("$tables", tables).replace("$data", blob))


def build(daily, rehearsal, findings, nodes_dir, population_dir, out):
    data = export(daily, rehearsal, findings, nodes_dir, population_dir)  # raise before anything is written
    site = Path(out) / PAGE
    if site.exists():
        shutil.rmtree(site)
    site.mkdir(parents=True)
    (site / "data.json").write_text(json.dumps(data, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (site / "index.html").write_text(render(data), encoding="utf-8")
    shutil.copyfile(HERE / "watch.js", site / "watch.js")
    return data


def main(argv):
    args = argv[1:]
    opts = {"--daily": "stats/data/daily", "--rehearsal": "stats/data/rehearsal", "--findings": "findings",
            "--nodes": "nodes", "--population": "population", "--out": "_site"}
    while args:
        if args[0] not in opts or len(args) < 2:
            print(__doc__.splitlines()[2], file=sys.stderr)
            return 2
        opts[args[0]] = args[1]
        args = args[2:]
    try:
        data = build(opts["--daily"], opts["--rehearsal"], opts["--findings"], opts["--nodes"],
                     opts["--population"], opts["--out"])
    except stats_model.StatsError as err:
        for p in err.problems:
            print(p, file=sys.stderr)
        print("the watch page was not built", file=sys.stderr)
        return 1
    n = {k: len(v["days"]) for k, v in data["views"].items()}
    print(f"{opts['--out']}/{PAGE}/index.html: {n['live']} live day{'' if n['live'] == 1 else 's'}, "
          f"{n['rehearsal']} rehearsal slot{'' if n['rehearsal'] == 1 else 's'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
