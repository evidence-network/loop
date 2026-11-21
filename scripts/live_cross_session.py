#!/usr/bin/env python3
"""Step 13 on the live lab: a live Mesh node and a live Goose node ask each other for evidence, both ways (loop#442, #443).

Usage (in the Goose venv, on the lab host as the lab operator, with the lab's standing relay up: lab/lab.sh relay):

  live_cross_session.py --out DIR --mesh-node-id ID [--mesh-name lab-1] [--goose-name goose-1] [--lab-dir DIR]
                        [--nodes-dir nodes] [--console URL] [--relay-addr ws://127.0.0.1:9840]

The Mesh node is a lab node (its plugin is capsules v0.1.19 or later, advertising: lab/lab.sh with LAB_NOSTR_NODES), the Goose
node one of lab/goose.sh's persistent nodes. Both are listed in nodes/ with the commitments they published; the commitments
name wss://relay.loop-lab.example, which CAPSULES_NOSTR_RELAY_MAP (the plugin) and GOOSE_RELAY_MAP (this driver, for the
Goose node) point at the lab's relay. Nothing is paid and no node is restarted or reconfigured.

  mesh -> goose  the Mesh node's console tool `nostr_evidence_request` seals the request in its own ledger, publishes it to the
                 Goose node's mailbox and records the answer or the absence; this driver polls the Goose node (it answers only
                 while polled). The proof is the asker's ledger: the extract of the ask's request and outcome records, the
                 asker's whole chain and its signed ledger-checkpoint reply (steps/07-ask's live proof).
  goose -> mesh  the Goose node asks through loop_ask_mailbox the mailbox the Mesh node's commitment names; the Mesh plugin reads
                 and answers it. The proof is the Goose node's Evidence Bundle (loop_book_bundle), as the rehearsal's.

Asks: a history card each way (history_card/1 from the Mesh node, history_card/2 where the Goose node's v0.20.1 library finds it listed; stranger-safe, so a signed artifact is the answer; a refusal's policy check against a commitment
the plugin does not hold would be NOT_CHECKED). The relay's own log (lab/relay.py) is the carrier log: when it received each request
and reply. Writes OUT (the run tree laid out as scripts/cross_ask_session.py's) and OUT/population/population.json, whose
directory is LOOP_CROSS_POPULATION for ./loop.sh --stage cross --ecosystem cross --population live.
"""
import argparse
import asyncio
import datetime as dt
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import threading
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "steps"))
SRC = Path(os.environ["LOOP_GOOSE_SRC"]) / "python" / "examples" / "goose"
sys.path.insert(0, str(SRC))

import daily_report  # noqa: E402
import check_commitment  # noqa: E402
import goose_live  # noqa: E402
import nostr_mailbox as mb  # noqa: E402
from goose_ask_session import Goose, stamp, unb64  # noqa: E402
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

ASK = "history_card"
REQUEST = {"subject": {"checkpoints": None}, "derivation": "history_card/1", "coverage": {"min_freshness": 1}}
TOOL = "/api/plugins/capsules/tools/nostr_evidence_request"


def _step07():
    loader = importlib.machinery.SourceFileLoader("live_cross_step07", str(ROOT / "steps" / "07-ask"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


step07 = _step07()
ledger_proof = step07.ledger_proof


def now():
    return dt.datetime.now(dt.timezone.utc)


def carried(log):
    """[(received time, event)] the relay's log holds."""
    out = []
    for line in Path(log).read_text().splitlines():
        try:
            rec = json.loads(line)
            out.append((step07.utc(rec["received"]), rec["event"]))
        except (ValueError, KeyError):
            continue
    return out


def tag(event, name):
    return next((t[1] for t in event.get("tags", []) if t[0] == name and len(t) > 1), None)


def request_event(log, mailbox, since):
    """(received, event): the newest kind 4700 addressed to `mailbox` that the relay received at or after `since`."""
    found = [(at, e) for at, e in carried(log) if e.get("kind") == mb.REQUEST_KIND and tag(e, "p") == mailbox and at >= since]
    if not found:
        raise SystemExit(f"the relay's log holds no request to mailbox {mailbox[:12]} since {stamp(since)}")
    return max(found, key=lambda x: x[0])


def reply_event(log, request_id):
    found = [(at, e) for at, e in carried(log) if e.get("kind") == mb.REPLY_KIND and tag(e, "e") == request_id]
    return min(found, key=lambda x: x[0]) if found else None


def mailbox_of(commitment):
    nostr = (commitment.get("ask") or {}).get("nostr") or {}
    if not nostr.get("mailbox_key") or not nostr.get("relays"):
        raise SystemExit(f"the commitment of {commitment.get('key_id', '')[:12]} names no ask.nostr mailbox")
    return nostr["mailbox_key"], nostr["relays"]


def post(console, body, timeout=90):
    req = urllib.request.Request(console + TOOL, data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


class Carrier:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def note(self, ask, event, at, **fields):
        with self.path.open("a") as f:
            f.write(json.dumps({"ask": ask, "event": event, "at": stamp(at), **fields}, sort_keys=True) + "\n")


def advertise_copy(out, name, commitment_file, policies_dir):
    """<name>/advertised/commitment.json and policies/, the files step 13 judges the node's advertisement from."""
    doc = json.loads(Path(commitment_file).read_text())
    adv = Path(out) / name / "advertised"
    (adv / "policies").mkdir(parents=True)
    shutil.copyfile(commitment_file, adv / "commitment.json")
    for p in Path(policies_dir).glob("*.json"):
        shutil.copyfile(p, adv / "policies" / p.name)
    return doc


async def run(a):
    out = a.out.resolve()
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    nodes_dir, log = a.nodes_dir.resolve(), a.relay_log
    ids = json.loads((a.lab_dir / "goose" / "ids.json").read_text())
    gkey, g_first = ids[a.goose_name]["key_id"], ids[a.goose_name]["record_id"]
    g_commit_file = nodes_dir / "commitments" / f"{gkey}.json"
    m_entry = json.loads((nodes_dir / f"{a.mesh_node_id}.json").read_text())
    mkey = m_entry["key_id"]
    m_commit_file = nodes_dir / "commitments" / f"{a.mesh_node_id}.json"
    g_commit = advertise_copy(out, a.goose_name, g_commit_file, nodes_dir / "commitments" / "policies")
    m_commit = advertise_copy(out, a.mesh_name, m_commit_file, nodes_dir / "commitments" / "policies")
    gbox, grelays = mailbox_of(g_commit)
    mbox, mrelays = mailbox_of(m_commit)
    (out / "entries").mkdir()
    for node_id in (a.mesh_node_id, gkey):
        shutil.copyfile(nodes_dir / f"{node_id}.json", out / "entries" / f"{node_id}.json")
    nodes = {a.mesh_name: {"ecosystem": "mesh-llm", "key_id": mkey, "node_id": a.mesh_node_id,
                           "checkpoint_key": goose_live.checkpoint_key_id(nodes_dir / "commitments", a.mesh_node_id),
                           "first_record": None},
             a.goose_name: {"ecosystem": "goose", "key_id": gkey, "node_id": gkey,
                            "checkpoint_key": goose_live.checkpoint_key_id(nodes_dir / "commitments", gkey),
                            "first_record": g_first}}
    (out / "nodes.json").write_text(json.dumps(nodes, indent=2, sort_keys=True) + "\n")

    homes = a.lab_dir / "goose" / "homes"
    env = {**os.environ, "GOOSE_HOME": str(homes / a.goose_name), "GOOSE_OPERATOR": a.goose_name,
           "GOOSE_RELAY_MAP": json.dumps({a.relay_name: a.relay_addr})}
    params = StdioServerParameters(command=sys.executable, args=[str(SRC / "loop_server.py")], env=env)
    m2g, g2m = out / a.mesh_name / "asks" / a.goose_name, out / a.goose_name / "asks" / a.mesh_name
    m2g_log, g2m_log = Carrier(m2g / "carrier.jsonl"), Carrier(g2m / "carrier.jsonl")
    ledger_dir = a.data_dir / "ledger"

    async with stdio_client(params) as (gr, gw):
        async with ClientSession(gr, gw) as gs:
            await gs.initialize()
            goose = Goose(gs, a.goose_name)

            # --- mesh -> goose: the Mesh plugin asks; the Goose node answers while this driver polls it ----------
            d = m2g / ASK
            d.mkdir(parents=True)
            t0 = now() - dt.timedelta(seconds=1)
            result, err = {}, []

            def ask():
                try:
                    result.update(post(a.console, {"mailbox_key": gbox, "relays": grelays, "responder_key": gkey,
                                                   "commitment": check_commitment.commitment_input(g_commit_file),
                                                   "request": REQUEST}))
                except Exception as e:  # noqa: BLE001
                    err.append(f"{type(e).__name__}: {e}")
            t = threading.Thread(target=ask)
            t.start()
            while t.is_alive():
                await goose.call("loop_mailbox_poll")
                await asyncio.sleep(2)
            t.join()
            back = now()
            if err or not result:
                raise SystemExit(f"mesh -> goose {ASK}: the plugin's ask failed: {err}")
            at, req = request_event(log, gbox, t0)
            request_bytes = unb64(req["content"])
            (d / "ask.request.json").write_bytes(request_bytes)
            m2g_log.note(ASK, "sent", at, to=gkey, request_sha256=hashlib.sha256(request_bytes).hexdigest())
            got = reply_event(log, req["id"])
            if got is not None:
                reply_at, reply = got
                (d / "ask.answer.json").write_bytes(unb64(reply["content"]))
                m2g_log.note(ASK, "delivered", reply_at, reply_sha256=hashlib.sha256(unb64(reply["content"])).hexdigest())
            state = (result.get("verification") or {}).get("state")
            saw = {"artifact": "verified", "refusal": "refusal"}.get(state)
            request = json.loads(request_bytes)
            extract = step07.ledger_extract(ledger_dir, gkey, request, at, back, saw, step07.duration(g_commit["answers_within"]))
            step07.write_json(d / "ask.ledger.json", extract)
            step07.write_json(d / "ask.checkpoint.json", {"asker": a.mesh_name, **ledger_proof.fetch(
                a.console, ledger_proof.CHAIN, 60, daily_report.http)})
            step07.keep_askers(out, {a.mesh_name: ledger_dir}, [(d, "ask", gkey, at, back, a.mesh_name)])

            # --- goose -> mesh: the Goose node asks the mailbox the Mesh commitment names ----------------------
            d = g2m / ASK
            d.mkdir(parents=True)
            t0 = now() - dt.timedelta(seconds=1)
            # No derivation named: evidencebook v0.20.1 asks history_card/2 on checkpoints where the commitment lists it
            # (the lab nodes' history_card/1 outgrew a websocket frame of 1 MiB), else history_card/1.
            tool = await goose.call("loop_ask_mailbox", commitment_file=str(m_commit_file), subject_form="checkpoints",
                                    min_freshness="1")
            polled = {}
            for _ in range(60):  # the plugin reads its mailbox every five seconds; the window is the request's deadline
                polled = await goose.call("loop_ask_mailbox_poll", request_event_id=tool["request_event_id"])
                if polled["status"] in ("answered", "absent"):  # transport_error and pending: poll again
                    break
                await asyncio.sleep(2)
            at, req = request_event(log, mbox, t0)
            request_bytes = unb64(req["content"])
            (d / "request.json").write_bytes(request_bytes)
            g2m_log.note(ASK, "sent", at, to=a.mesh_name, request_sha256=hashlib.sha256(request_bytes).hexdigest())
            got = reply_event(log, req["id"])
            if got is not None:
                reply_at, reply = got
                (d / "answer.json").write_bytes(unb64(reply["content"]))
                g2m_log.note(ASK, "delivered", reply_at, reply_sha256=hashlib.sha256(unb64(reply["content"])).hexdigest())
            if polled.get("status") not in ("answered", "absent"):
                raise SystemExit(f"goose -> mesh {ASK}: the Goose node recorded no outcome: {polled}")
            await goose.call("loop_book_bundle", book_id="goose/node", record_id=polled["record_id"],
                             out_path=str(d / "outcome.bundle.json"), disclose=True)

    pop = out / "population"
    pop.mkdir()
    rev = os.popen(f"git -C {ROOT} rev-parse HEAD").read().strip()
    doc = {"population": "live", "pinned": ["goose-g1"],
           "cross": {"run": str(out), "asks": [ASK], "head": rev,
                     "nodes": {n: {"key_id": v["key_id"], "ecosystem": v["ecosystem"]} for n, v in nodes.items()}}}
    (pop / "population.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    print(f"live_cross_session: run tree {out}; LOOP_CROSS_POPULATION={pop}", file=sys.stderr)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--mesh-node-id", required=True, help="the Mesh node's id: nodes/<id>.json and its commitment")
    ap.add_argument("--mesh-name", default="lab-1")
    ap.add_argument("--goose-name", default="goose-1")
    ap.add_argument("--lab-dir", type=Path, default=Path(os.environ.get("LAB_DIR", "")))
    ap.add_argument("--nodes-dir", type=Path, default=ROOT / "nodes")
    ap.add_argument("--console", default="http://127.0.0.1:9811")
    ap.add_argument("--data-dir", type=Path, help="the Mesh node's capsules data dir (default <lab>/nodes/<name>/.local/share/capsules)")
    ap.add_argument("--relay-addr", default="ws://127.0.0.1:9840")
    ap.add_argument("--relay-name", default=goose_live.RELAY)
    ap.add_argument("--relay-log", type=Path, help="default <lab>/relay/events.jsonl")
    a = ap.parse_args(argv)
    a.data_dir = a.data_dir or a.lab_dir / "nodes" / a.mesh_name / ".local" / "share" / "capsules"
    a.relay_log = a.relay_log or a.lab_dir / "relay" / "events.jsonl"
    asyncio.run(run(a))
    return 0


if __name__ == "__main__":
    sys.exit(main())
