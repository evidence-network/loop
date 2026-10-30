#!/usr/bin/env python3
"""The growth report: the stapled history cards of the network's commitments, checked offline, week by week.

Usage: growth.py --out DIR [--repo .] [--ref HEAD]
       writes DIR/growth/index.html and DIR/growth/data.json

It is its own report (loop#151, ruling ops dd525c02). It reads nothing from stats/, status/ or a loop
finding, and /stats/ and /status/ read nothing from it. Its only inputs are the committed files under
nodes/ (the listing) and nodes/commitments/ (what each node published), read from git history.

Each week is judged against the tree as it was committed at the end of that week, not against today's
files: nodes/*.json is the listing, nodes/commitments/<node_id>.json is what a node had published, and
scripts/check_commitment.py checks it offline (signature, policy digest, every stapled card). A week's
numbers come from the cards of the commitments that verified in that week's tree. The last week ends at
the ref's own commit time, so the same ref gives the same bytes; nothing here reads the clock.

"Records sealed" is the number of log entries covered by a signed checkpoint, padding and admission
decisions included. It measures evidence activity, never deals. Every count reads "n of N" or names what
it is out of. No score, rank or rating. Volume is network totals (and a split by community); a card's
status is per node, but there is no per-node volume.
"""
import argparse
import html
import json
import re
import subprocess
import sys
import tarfile
import tempfile
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_commitment as cc  # noqa: E402
import history_cards as hc  # noqa: E402

NODE_FILE = re.compile(r"^[0-9a-f]{64}\.json$")


def git(repo, *args, binary=False):
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True).stdout
    return out if binary else out.decode("utf-8")


def parse_time(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def stamp(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def weeks(repo, ref):
    """[(start, end, last)]: Monday-to-Monday UTC windows from the first commit touching nodes/ to the ref's time.

    `last` marks the window cut short at the ref's commit time (its end is then inclusive).
    """
    first = git(repo, "log", "--reverse", "--format=%cI", ref, "--", "nodes").split()
    if not first:
        return []
    head = parse_time(git(repo, "log", "-1", "--format=%cI", ref).strip())
    day = parse_time(first[0]).replace(hour=0, minute=0, second=0, microsecond=0)
    start = day - timedelta(days=day.weekday())
    out = []
    while start <= head:
        end = start + timedelta(days=7)
        out.append((start, min(end, head), end > head))
        start = end
    return out


def snapshot_sha(repo, ref, end, last):
    """The commit the week is judged at: the newest at or before the window's end (before it, if exclusive)."""
    before = end if last else end - timedelta(seconds=1)
    return git(repo, "rev-list", "-1", f"--before={stamp(before)}", ref).strip()


def export_nodes(repo, sha, into):
    """Extract nodes/ as committed at `sha` into `into`; False when the commit has no nodes/."""
    try:
        raw = git(repo, "archive", sha, "nodes", binary=True)
    except subprocess.CalledProcessError:
        return False
    with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
        tar.extractall(into, filter="data")
    return True


def checkpoints_of(entry):
    """[(payload, witnessed)] for each checkpoint of a staple entry."""
    answer = hc.card_answer(entry)
    history = hc._history(answer)
    payloads = hc.card_checkpoints(answer)
    out = []
    for item, payload in zip(history, payloads):
        receipts = item.get("receipts")
        witnessed = any(isinstance(r, dict) and isinstance(r.get(hc.RECEIPT_WITNESS), str) and r[hc.RECEIPT_WITNESS]
                        for r in (receipts if isinstance(receipts, list) else []))
        out.append((payload, witnessed))
    return out


def card_time(payload):
    """A checkpoint's issued_at as a UTC time (seconds may carry a fraction), or None."""
    issued = payload.get("issued_at") if payload else None
    try:
        return parse_time(issued) if isinstance(issued, str) else None
    except ValueError:
        return None


def is_count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def judge_tree(tree):
    """{listed: [node ids], nodes: {id: {"ok", "reason", "cards"}}} for the nodes/ directory at `tree`."""
    nodes_dir = Path(tree) / "nodes"
    listed = sorted(p.stem for p in nodes_dir.glob("*.json") if NODE_FILE.match(p.name))
    nodes = {}
    for node_id in listed:
        path = nodes_dir / "commitments" / f"{node_id}.json"
        if not path.is_file():
            nodes[node_id] = {"ok": False, "reason": "no commitment published", "cards": []}
            continue
        errs, _ = cc.check_file(path)
        if errs:
            nodes[node_id] = {"ok": False, "reason": errs[0], "cards": []}
            continue
        doc = json.loads(path.read_text(encoding="utf-8"))
        cards = []
        for entry in hc.entries(doc):
            cards.append({"stream": entry.get("stream"), "community": entry.get("community"),
                          "checkpoints": checkpoints_of(entry)})
        nodes[node_id] = {"ok": True, "reason": "", "cards": cards}
    return {"listed": listed, "nodes": nodes}


def week_numbers(judged, start, end, last):
    """The week's counts from the judged tree: nodes, and volume per community (cards, checkpoints, records)."""
    verifying = [n for n in judged["listed"] if judged["nodes"][n]["ok"]]
    communities = {}
    for node_id in verifying:
        for card in judged["nodes"][node_id]["cards"]:
            c = communities.setdefault(card["community"], {"cards": 0, "checkpoints": 0, "records": 0, "witnessed": 0})
            c["cards"] += 1
            for payload, witnessed in card["checkpoints"]:
                issued = card_time(payload)
                if issued is None or not (start <= issued < end or (last and issued == end)):
                    continue
                size, prev = payload.get("log_size"), payload.get("prev_size")
                if not (is_count(size) and is_count(prev) and size >= prev):
                    continue
                c["checkpoints"] += 1
                c["records"] += size - prev
                c["witnessed"] += bool(witnessed)
    return {"listed": len(judged["listed"]), "verifying": len(verifying),
            "communities": {k: communities[k] for k in sorted(communities)}}


def build_model(repo, ref):
    repo = Path(repo)
    rows, latest = [], None
    cache = {}
    for start, end, last in weeks(repo, ref):
        sha = snapshot_sha(repo, ref, end, last)
        if sha not in cache:
            with tempfile.TemporaryDirectory() as tmp:
                cache[sha] = judge_tree(tmp) if export_nodes(repo, sha, tmp) else {"listed": [], "nodes": {}}
        judged = cache[sha]
        row = week_numbers(judged, start, end, last)
        row.update({"week": start.strftime("%Y-%m-%d"), "to": end.strftime("%Y-%m-%d"), "partial": last, "commit": sha[:12]})
        rows.append(row)
        latest = judged
    return {"weeks": rows, "latest": latest or {"listed": [], "nodes": {}}}


def first_checkpoint(card):
    """(date, witnessed) of the stream's first checkpoint, or (None, False)."""
    for payload, witnessed in card["checkpoints"][:1]:
        issued = payload.get("issued_at") if payload else None
        if isinstance(issued, str) and re.match(r"^\d{4}-\d{2}-\d{2}T", issued):
            return issued[:10], witnessed
    return None, False


def export(model):
    """The numbers the page shows, as data.json: per-week counts and per-node card status; no per-node volume."""
    cards = []
    for node_id in model["latest"]["listed"]:
        node = model["latest"]["nodes"][node_id]
        if not node["ok"]:
            cards.append({"node_id": node_id, "verifies": False, "reason": node["reason"]})
            continue
        for card in node["cards"]:
            date, witnessed = first_checkpoint(card)
            cards.append({"node_id": node_id, "verifies": True, "stream": card["stream"], "community": card["community"],
                          "first_checkpoint": date, "first_checkpoint_witnessed": witnessed})
    return {"weeks": model["weeks"], "cards": cards}


def esc(text):
    return html.escape(str(text), quote=True)


def week_label(w):
    return f"{w['week']} to {w['to']}" + (" (so far)" if w["partial"] else "")


def render(data):
    weeks_rows = []
    for w in reversed(data["weeks"]):
        total = {"checkpoints": 0, "records": 0, "witnessed": 0}
        for c in w["communities"].values():
            for k in total:
                total[k] += c[k]
        v = w["verifying"]
        weeks_rows.append(
            f"<tr><th scope=\"row\">{esc(week_label(w))}</th>"
            f"<td class=\"num\">{v} of {w['listed']} listed nodes</td>"
            f"<td class=\"num\">{total['checkpoints']} from {v} of {w['listed']} nodes</td>"
            f"<td class=\"num\">{total['records']} from {v} of {w['listed']} nodes</td>"
            f"<td class=\"num\">{total['witnessed']} of {total['checkpoints']} checkpoints cut</td></tr>")
    community_rows = []
    for w in reversed(data["weeks"]):
        for name, c in w["communities"].items():
            community_rows.append(
                f"<tr><th scope=\"row\">{esc(week_label(w))}</th><td>{esc(name)}</td>"
                f"<td class=\"num\">{c['checkpoints']} from {c['cards']} verifying cards</td>"
                f"<td class=\"num\">{c['records']} from {c['cards']} verifying cards</td>"
                f"<td class=\"num\">{c['witnessed']} of {c['checkpoints']} checkpoints cut</td></tr>")
    card_rows = []
    listed = len({c["node_id"] for c in data["cards"]})
    ok_nodes = len({c["node_id"] for c in data["cards"] if c["verifies"]})
    for c in data["cards"]:
        if not c["verifies"]:
            card_rows.append(f"<tr><th scope=\"row\"><code>{esc(c['node_id'])}</code></th><td>no card</td><td>fails: {esc(c['reason'])}</td>"
                             "<td class=\"gapcell\">not shown</td></tr>")
            continue
        first = c["first_checkpoint"] or "not readable"
        label = "witnessed" if c["first_checkpoint_witnessed"] else "stated, not proven"
        card_rows.append(f"<tr><th scope=\"row\"><code>{esc(c['node_id'])}</code></th><td>{esc(c['community'])}: {esc(c['stream'])}</td>"
                         f"<td>verifies</td><td>{esc(first)} ({label})</td></tr>")
    return PAGE.format(
        nodes_line=f"{ok_nodes} of {listed} listed nodes have a stapled card that verifies" if listed else "No node is listed yet",
        weeks="\n".join(weeks_rows) or "<tr><td colspan=\"5\">No week yet.</td></tr>",
        communities="\n".join(community_rows) or "<tr><td colspan=\"5\">No verifying card yet.</td></tr>",
        cards="\n".join(card_rows) or "<tr><td colspan=\"4\">No node is listed.</td></tr>")


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Evidence network growth</title>
<style>
:root {{ --bg: #fdfdfb; --fg: #1d1d1b; --muted: #5c5c57; --line: #d9d9d2; --code: #f0f0ea; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: #161615; --fg: #e8e8e3; --muted: #a3a39c; --line: #3a3a36; --code: #262624; }} }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--fg); font: 16px/1.55 system-ui, sans-serif; font-variant-numeric: tabular-nums; }}
main {{ max-width: 72rem; margin: 0 auto; padding: 1.5rem 16px 4rem; }}
h1 {{ font-size: 1.7rem; margin: 0 0 .3rem; }}
h2 {{ font-size: 1.3rem; margin: 2.5rem 0 .5rem; border-bottom: 1px solid var(--line); padding-bottom: .3rem; }}
p {{ max-width: 44rem; }}
.note {{ color: var(--muted); font-size: .9rem; }}
code {{ background: var(--code); padding: 0 .25em; border-radius: 3px; font-size: .9em; overflow-wrap: anywhere; }}
.scroll {{ overflow-x: auto; }}
table {{ border-collapse: collapse; width: 100%; font-size: .9rem; margin: .4rem 0; }}
th, td {{ border: 1px solid var(--line); padding: .35rem .5rem; vertical-align: top; text-align: left; }}
td.num {{ text-align: right; }}
td.gapcell {{ color: var(--muted); }}
</style>
</head>
<body>
<main>
<h1>Evidence network growth</h1>
<p class="note">What the evidence network's nodes have published about their own history, week by week. It is built from
the committed commitments and their stapled history cards, checked offline, and nothing else. It is not the stats
page and not the status page.</p>
<p>{nodes_line}.</p>

<h2>Week by week</h2>
<p class="note">Each week is judged against the commitments as committed at the end of that week, not against today's
files. A node counts only if its commitment and every card stapled to it check offline in that week's files.
"Records sealed" is the number of log entries covered by a signed checkpoint, padding and admission decisions included:
it measures evidence activity, not deals. Counts come from the nodes with a verifying card that week, as each cell says.</p>
<div class="scroll"><table>
<thead><tr><th scope="col">Week (UTC)</th><th scope="col">Nodes with a verifying card</th><th scope="col">Checkpoints cut</th><th scope="col">Records sealed</th><th scope="col">Witnessed checkpoints</th></tr></thead>
<tbody>
{weeks}
</tbody></table></div>

<h2>By community</h2>
<p class="note">The same counts split by the community each card names. A node can staple one card per book.</p>
<div class="scroll"><table>
<thead><tr><th scope="col">Week (UTC)</th><th scope="col">Community</th><th scope="col">Checkpoints cut</th><th scope="col">Records sealed</th><th scope="col">Witnessed checkpoints</th></tr></thead>
<tbody>
{communities}
</tbody></table></div>

<h2>Each node's card, now</h2>
<p class="note">Whether the card verifies against the newest commitment, and if not, why. The first checkpoint is the
date the card says its stream began: stated, not proven, unless that checkpoint carries a witness receipt. Nodes are
listed by node id; the order means nothing.</p>
<div class="scroll"><table>
<thead><tr><th scope="col">Node</th><th scope="col">Stream</th><th scope="col">Card</th><th scope="col">First checkpoint</th></tr></thead>
<tbody>
{cards}
</tbody></table></div>
</main>
</body>
</html>
"""


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--repo", default=str(ROOT))
    ap.add_argument("--ref", default="HEAD")
    args = ap.parse_args(argv)
    data = export(build_model(args.repo, args.ref))
    out = Path(args.out) / "growth"
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(render(data), encoding="utf-8")
    (out / "data.json").write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
