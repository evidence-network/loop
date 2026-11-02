#!/usr/bin/env python3
"""Step 07 on the Goose rehearsal: two G1 nodes ask each other through the mailbox their commitments advertise (loop#330).

Usage (in the Goose venv, which has `mcp` and `websockets`): goose_ask_session.py OUT

Two loop_server.py processes (a buyer and a seller), spoken to over MCP stdio as Goose speaks to them, advertise a
Nostr mailbox with loop_advertise and ask each other, three asks each way, with the answering side polling its
mailbox (loop_mailbox_poll), never serving:

  exchange        an exchange request about the asker's own join record: the asker is a stranger on a mailbox
                  (its carriage key says nothing about who it is), so the node's disclosure policy refuses it with
                  a signed refusal
  history_card    history_card/1, which the node advertises to anyone: a signed artifact
  exchange_late   the same exchange request, whose answer the node posts only after the deadline: the asker records
                  an absence at the deadline and keeps the late reply

The relay is this process's in-memory NIP-01 relay, the rehearsal carrier: it hands the events over and logs when it
received each (carrier.jsonl). The commitments name a wss:// relay, as step 03's checker requires; GOOSE_RELAY_MAP
points both nodes at the local one. Each outcome (the response or the absence) is exported with loop_book_bundle
`disclose`, which reveals the outcome's header and the request's it cites, with the reply bytes the response keeps.

Layout, as steps/07-ask judges the book backend (<asker>/asks/<asked>/<ask>/, <asked>/advertised/):
  nodes.json
  <node>/advertised/commitment.json, policies/<node_id>.json
  <asker>/asks/<asked>/carrier.jsonl
  <asker>/asks/<asked>/<ask>/request.json, answer.json, outcome.bundle.json (+ the files loop_book_bundle writes beside it)
"""
import asyncio
import base64
import datetime as dt
import hashlib
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from websockets.sync.server import serve

SRC = Path(os.environ["LOOP_GOOSE_SRC"]) / "python" / "examples" / "goose"
ADVERTISED_RELAY = "wss://relay.rehearsal.example"
ANSWERS_WITHIN = "PT1M"  # the shortest window an absence may claim, so the late ask's absence is recorded in this run
POLL_INTERVAL = 30
NAMES = ("buyer", "seller")
REQUEST_KIND, REPLY_KIND = 4700, 4701


def stamp(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Relay:
    """An in-memory NIP-01 relay that stores every event it is sent, with the time it received it, and answers a REQ
    with the stored events matching its filter. It checks nothing: the nodes check the events."""

    def __init__(self):
        self.events, self.received = {}, {}
        self._server = serve(self._handle, "127.0.0.1", 0)
        self.address = f"ws://127.0.0.1:{self._server.socket.getsockname()[1]}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def _handle(self, ws):
        for raw in ws:
            msg = json.loads(raw)
            if msg[0] == "EVENT":
                ev = msg[1]
                if ev["id"] not in self.events:
                    self.events[ev["id"]] = ev
                    self.received[ev["id"]] = dt.datetime.now(dt.timezone.utc)
                ws.send(json.dumps(["OK", ev["id"], True, ""]))
            elif msg[0] == "REQ":
                for ev in list(self.events.values()):
                    if self._matches(ev, msg[2]):
                        ws.send(json.dumps(["EVENT", msg[1], ev]))
                ws.send(json.dumps(["EOSE", msg[1]]))

    @staticmethod
    def _matches(ev, flt):
        if "kinds" in flt and ev["kind"] not in flt["kinds"]:
            return False
        return all(any(t[0] == key[1:] and t[1] in want for t in ev["tags"])
                   for key, want in flt.items() if key.startswith("#"))

    def close(self):
        self._server.shutdown()

    def reply_to(self, request_id):
        return next((e for e in self.events.values()
                     if e["kind"] == REPLY_KIND and ["e", request_id] in [t[:2] for t in e["tags"]]), None)


class Goose:
    """One node's MCP session, as Goose holds it."""

    def __init__(self, session, name):
        self.session, self.name = session, name

    async def call(self, tool, **arguments):
        result = await self.session.call_tool(tool, arguments)
        out = json.loads("".join(c.text for c in result.content if getattr(c, "type", "") == "text"))
        if not out.get("ok"):
            raise SystemExit(f"{self.name} {tool}: {out.get('error')}")
        print(f"{self.name:6} {tool:22} ok", file=sys.stderr)
        return out


async def run(out, relay, names=NAMES, homes=None, published=None, relay_name=ADVERTISED_RELAY, joined=None):
    """Two nodes ask each other. By default (the rehearsal) each starts in a fresh home under out/run and advertises
    now. The live lab passes `homes` (the persistent GOOSE_HOME root, a directory per name) and `published` (the
    directory holding the commitments the nodes already published, nodes/commitments/): the nodes keep their keys, and
    ask through the commitment each published, advertising nothing new. `relay_name` is the relay those name. `joined`
    ({name: {key_id, record_id}}, from the nodes' first join) stops the run joining them again, so a persistent node's
    book holds one join record, not one per run."""
    shutil.rmtree(out, ignore_errors=True)  # a rerun starts clean: the carrier log is appended to
    out.mkdir(parents=True)
    relay_map = json.dumps({relay_name: relay.address})

    def params(name):
        home = Path(homes) / name if homes else out / "run" / name
        env = {**os.environ, "GOOSE_HOME": str(home), "GOOSE_OPERATOR": f"goose-{name}", "GOOSE_RELAY_MAP": relay_map}
        return StdioServerParameters(command=sys.executable, args=[str(SRC / "loop_server.py")], env=env)

    async with stdio_client(params(names[0])) as (br, bw), stdio_client(params(names[1])) as (sr, sw):
        async with ClientSession(br, bw) as bs, ClientSession(sr, sw) as ss:
            await bs.initialize()
            await ss.initialize()
            nodes = {names[0]: Goose(bs, names[0]), names[1]: Goose(ss, names[1])}
            joined = joined or {name: await g.call("loop_join") for name, g in nodes.items()}
            for name, g in nodes.items():
                adv = out / name / "advertised"
                node_id = joined[name]["key_id"]
                if published:
                    adv.mkdir(parents=True)
                    shutil.copyfile(Path(published) / f"{node_id}.json", adv / f"{node_id}.json")
                    (adv / "policies").mkdir()
                    shutil.copyfile(Path(published) / "policies" / f"{node_id}.json", adv / "policies" / f"{node_id}.json")
                else:
                    await g.call("loop_advertise", out_dir=str(adv), relays=relay_name, answers_within=ANSWERS_WITHIN,
                                 poll_interval=POLL_INTERVAL)
                shutil.copyfile(adv / f"{node_id}.json", adv / "commitment.json")
            (out / "nodes.json").write_text(json.dumps(
                {n: {"key_id": j["key_id"], "first_record": j["record_id"]} for n, j in joined.items()},
                indent=2, sort_keys=True) + "\n")
            pairs = [(a, b) for a in names for b in names if a != b]
            sent = {}  # (asker, asked, ask) -> the ask tool's answer

            async def ask(asker, asked, name):
                spec = {"subject_form": "exchange", "subject_value": json.dumps(joined[asker]["record_id"]),
                        "min_freshness": "1"}
                if name == "history_card":
                    spec = {"subject_form": "checkpoints", "derivation": "history_card/1", "min_freshness": "1"}
                node_id = joined[asked]["key_id"]
                sent[asker, asked, name] = await nodes[asker].call(
                    "loop_ask_mailbox", commitment_file=str(out / asked / "advertised" / f"{node_id}.json"), **spec)

            async def answer_all():
                for g in nodes.values():
                    await g.call("loop_mailbox_poll")

            async def collect(name, only=None):
                results = {}
                for asker, asked in pairs:
                    if (asker, asked, name) not in sent or (only and (asker, asked) not in only):
                        continue
                    results[asker, asked] = await nodes[asker].call(
                        "loop_ask_mailbox_poll", request_event_id=sent[asker, asked, name]["request_event_id"])
                return results

            for name in ("exchange", "history_card"):
                for asker, asked in pairs:
                    await ask(asker, asked, name)
            await answer_all()
            outcomes = {}
            for name in ("exchange", "history_card"):
                for key, got in (await collect(name)).items():
                    if got["status"] != "answered":
                        raise SystemExit(f"{key} {name}: {got}")
                    outcomes[key + (name,)] = got
            for asker, asked in pairs:
                await ask(asker, asked, "exchange_late")
            deadline = max(sent[k + ("exchange_late",)]["deadline"] for k in pairs)
            while time.time() < deadline + 1.5:
                await asyncio.sleep(0.5)
            for _ in range(20):  # the window closes at the later of the deadline and a minute after the request
                late = await collect("exchange_late")
                if all(got["status"] == "absent" for got in late.values()):
                    break
                await asyncio.sleep(0.5)
            for key, got in late.items():
                if got["status"] != "absent":
                    raise SystemExit(f"{key} exchange_late: {got}")
                outcomes[key + ("exchange_late",)] = got
            await answer_all()  # now, after the deadline
            for key, got in (await collect("exchange_late")).items():
                if not got.get("late"):
                    raise SystemExit(f"{key} exchange_late: the late reply was not kept: {got}")
            # the proof: request and reply bytes as the relay carried them, the carrier log, each outcome's bundle
            for (asker, asked, name), tool in sent.items():
                d = out / asker / "asks" / asked / name
                d.mkdir(parents=True, exist_ok=True)
                req = relay.events[tool["request_event_id"]]
                request = unb64(req["content"])
                (d / "request.json").write_bytes(request)
                reply = relay.reply_to(req["id"])
                if reply is not None:
                    (d / "answer.json").write_bytes(unb64(reply["content"]))
                events = [{"ask": name, "event": "sent", "at": stamp(relay.received[req["id"]]), "to": asked,
                           "request_sha256": hashlib.sha256(request).hexdigest()}]
                if reply is not None:
                    events.append({"ask": name, "event": "delivered", "at": stamp(relay.received[reply["id"]]),
                                   "reply_sha256": hashlib.sha256(unb64(reply["content"])).hexdigest()})
                log = out / asker / "asks" / asked / "carrier.jsonl"
                with log.open("a") as f:
                    for e in events:
                        f.write(json.dumps(e, sort_keys=True) + "\n")
                await nodes[asker].call("loop_book_bundle", book_id="goose/node",
                                        record_id=outcomes[asker, asked, name]["record_id"],
                                        out_path=str(d / "outcome.bundle.json"), disclose=True)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: goose_ask_session.py OUT")
    relay = Relay()
    try:
        asyncio.run(run(Path(sys.argv[1]), relay))
    finally:
        relay.close()
