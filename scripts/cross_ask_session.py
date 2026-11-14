#!/usr/bin/env python3
"""Step 13 on the rehearsal: a Mesh node and a Goose node ask each other for evidence, both ways (loop#440).

Usage (in the Goose venv): cross_ask_session.py OUT [--plant wrong-key|late-counted|unsigned-refusal]

Two rehearsal nodes, one per ecosystem, each with its own rehearsal key (never a live key, wallet or lab VM):

  mesh   a capsulectl book node (steps/07-ask's join(): join.sh, its book, signed policy and commitment), the
         rehearsal population's `alice`. Its commitment is re-signed by the same key with a rehearsal-only mailbox
         (`ask.nostr`): the mesh plugin does not read a Nostr mailbox yet (loop#442), so THE MAILBOX OF THIS NODE IS
         THIS SCRIPT'S ADAPTER (serve_mesh), not the mesh plugin. Entry and commitment are made together so they
         agree (ask_route nostr).
  goose  evidencebook's G1 node (python/examples/goose at the goose-g1 pin), spoken to over MCP stdio as Goose speaks
         to it; its commitment is the node's own (loop_advertise) and it answers by polling its mailbox.

The relay is this process's in-memory NIP-01 relay (scripts/goose_ask_session.py), the rehearsal carrier: it hands the
events over and logs when it received each (carrier.jsonl). Three asks go each way, as step 07's goose backend makes them:

  exchange       an exchange request about the asker's own first record; the asker is a stranger on a mailbox, so a
                 `counterparties` policy refuses it with a signed refusal (a correct answer)
  history_card   history_card/1, answered to anyone with a signed artifact
  exchange_late  the same exchange request, whose answer is posted only after the deadline: the asker records an
                 absence at the deadline and keeps the late reply

  mesh -> goose  the mesh node seals the request in its book (`capsulectl book request --sent`, the request carries its
                 own `deadline`, the commitment's answers_within after it was sealed), posts it as a kind 4700 event to
                 the goose node's mailbox, the goose node polls (loop_mailbox_poll) and answers, the mesh node records
                 the answer (`--response`) or its absence (`--absent-until`).
  goose -> mesh  the goose node asks through loop_ask_mailbox and polls for the answer (loop_ask_mailbox_poll); the
                 adapter reads the mailbox, answers with `capsulectl book respond` and posts the reply (kind 4701).

--plant makes one asker or responder misbehave (one fixture per class, tests/fixtures/step13/make_fixtures.py):

  wrong-key          the answer to the mesh node's exchange ask is signed by a key other than the goose node's, and the
                     mesh node records it under that key
  late-counted       the mesh node records the late reply as an answer after the deadline instead of as an absence
  unsigned-refusal   the adapter answers the goose node's exchange ask with a refusal that carries no signature

Layout, as steps/07-ask judges the goose backend (<asker>/asks/<asked>/<ask>/, <asked>/advertised/):
  nodes.json                     {name: {ecosystem, key_id, node_id, checkpoint_key, first_record}}
  entries/<node_id>.json         each node's nodes/ entry (rehearsal), made with its commitment
  <node>/advertised/commitment.json, policy and policies/
  <asker>/asks/<asked>/carrier.jsonl
  <asker>/asks/<asked>/<ask>/request.json, answer.json, outcome.bundle.json
"""
import argparse
import asyncio
import datetime as dt
import hashlib
import importlib.machinery
import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "steps"))
SRC = Path(os.environ["LOOP_GOOSE_SRC"]) / "python" / "examples" / "goose"
sys.path.insert(0, str(SRC))

import check_commitment as cc  # noqa: E402
import check_nodes  # noqa: E402
import nostr_mailbox as mb  # noqa: E402
from goose_ask_session import ADVERTISED_RELAY, ANSWERS_WITHIN, POLL_INTERVAL, Goose, Relay, stamp, unb64  # noqa: E402
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

MEDIA = "application/json"
ASKS = ("exchange", "history_card", "exchange_late")
PLANTS = ("wrong-key", "late-counted", "unsigned-refusal")
MESH, GOOSE = "mesh", "goose"
MESH_KEY_DIR = "population/rehearsal/keys/alice"  # the mesh rehearsal node's TEST key
WITHIN = dt.timedelta(minutes=1)


def _step07():
    loader = importlib.machinery.SourceFileLoader("step07_ask", str(ROOT / "steps" / "07-ask"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


step07 = _step07()


def now():
    return dt.datetime.now(dt.timezone.utc)


def request_map(name, first_record, deadline):
    """The request a node composes for an ask: step 07's three asks, with the request's own deadline."""
    if name == "history_card":
        body = {"subject": {"checkpoints": None}, "derivation": "history_card/1", "coverage": {"min_freshness": 1}}
    else:
        body = {"subject": {"exchange": first_record}, "coverage": {"min_freshness": 1}}
    return {**body, "deadline": deadline.strftime("%Y-%m-%dT%H:%M:%SZ")}


def entry_for(key_id, operator):
    """A rehearsal nodes/ entry that carries the step 13 consent statement (check_nodes.CONSENT_ASK_ACROSS)."""
    return {"node_id": key_id, "key_id": key_id, "operator": operator, "consent": check_nodes.CONSENT_ASK_ACROSS,
            "mesh": "rehearsal", "ask_route": "nostr",
            "disclosure": {"policy": "counterparties", "stranger_safe": list(check_nodes.STRANGER_SAFE)},
            "host_build": "rehearsal", "plugin_version": "rehearsal"}


def sign_refusal(request_bytes, reason, key_pem, unsigned=False):
    """A refusal of the request, signed by key_pem (JCS of request_digest, reason, issued_at), or without a signature."""
    body = {"request_digest": hashlib.sha256(request_bytes).hexdigest(), "reason": reason,
            "issued_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    pub = subprocess.run(["openssl", "pkey", "-in", str(key_pem), "-pubout", "-outform", "DER"], check=True,
                         capture_output=True).stdout[-32:].hex()
    if unsigned:
        return pub, cc.canonical({**body, "key_id": pub})
    with tempfile.NamedTemporaryFile() as f:
        f.write(cc.canonical(body))
        f.flush()
        sig = subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", str(key_pem), "-rawin", "-in", f.name],
                             check=True, capture_output=True).stdout.hex()
    return pub, cc.canonical({**body, "key_id": pub, "sig": sig})


class Log:
    """carrier.jsonl for one (asker, asked) pair: when the relay received each request and reply."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def note(self, ask, event, at, **fields):
        with self.path.open("a") as f:
            f.write(json.dumps({"ask": ask, "event": event, "at": stamp(at), **fields}, sort_keys=True) + "\n")


class Mesh:
    """The mesh rehearsal node, and its adapter: the mailbox the mesh plugin does not have yet (loop#442)."""

    def __init__(self, out, tmp, capsulectl):
        self.node = step07.join({"name": MESH, "key_dir": MESH_KEY_DIR}, tmp, capsulectl, out)
        self.key_pem = ROOT / MESH_KEY_DIR / "node-key.pem"
        self.secret = mb.new_secret()
        self.mailbox_key = mb.public_key(self.secret)
        adv = Path(out) / MESH / "advertised"
        doc = json.loads((adv / "commitment.json").read_text())
        doc.pop("sig", None)
        doc["ask"] = {**doc["ask"], "nostr": {"mailbox_key": self.mailbox_key, "relays": [ADVERTISED_RELAY]}}
        (adv / "commitment.json").write_text(json.dumps(cc.sign(doc, str(self.key_pem)), indent=2, sort_keys=True) + "\n")
        self.commitment = adv / "commitment.json"
        self.answered, self.work = set(), Path(tmp) / "mesh-answers"
        self.work.mkdir()

    def serve(self, unsigned=None):
        """One poll of the mesh node's mailbox: answer each request addressed to it with `capsulectl book respond`, as a
        stranger (the carriage key says nothing about who the asker is). `unsigned` is the sha256 of a request that is
        answered with a refusal that carries no signature."""
        relay = mb.Relay(ADVERTISED_RELAY)
        for req in relay.query({"kinds": [mb.REQUEST_KIND], "#p": [self.mailbox_key]}):
            octets = mb.decode(req.content)
            if req.id in self.answered or octets is None or not mb.check_event(req):
                continue
            digest = hashlib.sha256(octets).hexdigest()
            d = self.work / digest[:16]
            d.mkdir(exist_ok=True)
            (d / "request.json").write_bytes(octets)
            if digest == unsigned:
                _, reply = sign_refusal(octets, "not_authorized", self.key_pem, unsigned=True)
            else:
                self.node.cap("book", "respond", "--policy", str(self.node.joined / "policy.json"),
                              "--request", str(d / "request.json"), "--output", str(d / "answer.json"))
                reply = (d / "answer.json").read_bytes()
            event = mb.sign_event(self.secret, mb.REPLY_KIND,
                                  [("e", req.id), ("p", req.pubkey), ("m", MEDIA)], mb.encode(reply),
                                  int(now().timestamp()))
            relay.publish(event)
            self.answered.add(req.id)


async def run(out, plant):
    shutil.rmtree(out, ignore_errors=True)  # a rerun starts clean: the carrier log is appended to
    out.mkdir(parents=True)
    relay = Relay()
    os.environ["GOOSE_RELAY_MAP"] = json.dumps({ADVERTISED_RELAY: relay.address})
    capsulectl = step07.tools()
    if not capsulectl.is_file():
        raise SystemExit(f"cross_ask_session: no capsulectl at {capsulectl}; run steps/07-ask.setup.sh")
    try:
        with tempfile.TemporaryDirectory(prefix="13-ask-across-") as tmp:
            await session(out, Path(tmp), relay, capsulectl, plant)
    finally:
        relay.close()


class Pair:
    """The two directions of one run, with what each needs to send, answer and record."""

    def __init__(self, out, relay, mesh, goose, gkey, gckpt, gmailbox, first_goose, plant):
        self.out, self.relay, self.mesh, self.goose, self.plant = out, relay, mesh, goose, plant
        self.gkey, self.gckpt, self.gmailbox, self.first_goose = gkey, gckpt, gmailbox, first_goose
        self.m2g = out / MESH / "asks" / GOOSE
        self.g2m = out / GOOSE / "asks" / MESH
        self.m2g_log, self.g2m_log = Log(self.m2g / "carrier.jsonl"), Log(self.g2m / "carrier.jsonl")
        self.m, self.g = {}, {}  # ask -> what the mesh asker / the goose asker holds of it

    def send(self, names):
        for name in names:
            d = self.m2g / name
            d.mkdir(parents=True)
            # the request's own deadline: the commitment's answers_within after it is sealed (a second more, so
            # the window a book records is not shorter than the minute it must be)
            deadline = (now() + WITHIN + dt.timedelta(seconds=1)).replace(microsecond=0)
            (d / "request.in.json").write_text(json.dumps(request_map(name, self.mesh.node.first_record, deadline)))
            r = self.mesh.node.cap("book", "request", "--request", str(d / "request.in.json"), "--responder",
                                   self.gkey, "--sent", "--output", str(d / "request.json"))
            (d / "request.in.json").unlink()
            data = (d / "request.json").read_bytes()
            event = mb.sign_event(mb.new_secret(), mb.REQUEST_KIND,
                                  [("p", self.gmailbox), ("m", MEDIA), ("expiration", str(int(deadline.timestamp())))],
                                  mb.encode(data), int(now().timestamp()))
            mb.Relay(ADVERTISED_RELAY).publish(event)
            self.m2g_log.note(name, "sent", self.relay.received[event.id], to=self.gkey,
                              request_sha256=hashlib.sha256(data).hexdigest())
            self.m[name] = {"dir": d, "record": step07.keep(r.stdout), "event": event, "deadline": deadline}

    async def send_goose(self, names):
        for name in names:
            spec = ({"subject_form": "checkpoints", "derivation": "history_card/1", "min_freshness": "1"}
                    if name == "history_card" else
                    {"subject_form": "exchange", "subject_value": json.dumps(self.first_goose), "min_freshness": "1"})
            tool = await self.goose.call("loop_ask_mailbox", commitment_file=str(self.mesh.commitment), **spec)
            req = self.relay.events[tool["request_event_id"]]
            d = self.g2m / name
            d.mkdir(parents=True)
            data = unb64(req["content"])
            (d / "request.json").write_bytes(data)
            self.g2m_log.note(name, "sent", self.relay.received[req["id"]], to=MESH,
                              request_sha256=hashlib.sha256(data).hexdigest())
            self.g[name] = {"dir": d, "tool": tool, "request": data, "event_id": req["id"]}

    def reply_to(self, event_id):
        ev = self.relay.reply_to(event_id)
        return None if ev is None else (unb64(ev["content"]), self.relay.received[ev["id"]])

    # --- the mesh asker records what it got ---------------------------------------------------------------------
    def mesh_record_answer(self, name, impostor=None):
        a = self.m[name]
        reply, at = self.reply_to(a["event"].id)
        key = self.gkey
        if impostor is not None:  # wrong-key: an answer signed by another key, recorded under that key
            key, reply = sign_refusal((a["dir"] / "request.json").read_bytes(), "not_authorized", impostor)
        (a["dir"] / "answer.json").write_bytes(reply)
        self.m2g_log.note(name, "delivered", at, reply_sha256=hashlib.sha256(reply).hexdigest())
        r = self.mesh.node.cap("book", "request", "--for", a["record"]["record_id"], "--response",
                               str(a["dir"] / "answer.json"), "--responder-key", key,
                               "--responder-checkpoint-key", self.gckpt)
        a["outcome"] = step07.keep(r.stdout)

    def mesh_record_absence(self, name):
        a = self.m[name]
        until = dt.datetime.fromtimestamp(math.ceil(a["deadline"].timestamp()), dt.timezone.utc)
        r = self.mesh.node.cap("book", "request", "--for", a["record"]["record_id"], "--absent-until",
                               until.strftime("%Y-%m-%dT%H:%M:%SZ"), "--route", mb.nprofile(self.gmailbox, [ADVERTISED_RELAY]))
        a["outcome"] = step07.keep(r.stdout)

    def mesh_keep_late(self, name):
        a = self.m[name]
        got = self.reply_to(a["event"].id)
        if got is None:
            raise SystemExit(f"mesh -> goose {name}: the goose node posted no late reply")
        reply, at = got
        (a["dir"] / "answer.json").write_bytes(reply)
        self.m2g_log.note(name, "delivered", at, reply_sha256=hashlib.sha256(reply).hexdigest())

    def mesh_disclose(self, name):
        a = self.m[name]
        (a["dir"] / "outcome.json").write_text(json.dumps(a["outcome"], indent=2, sort_keys=True) + "\n")
        self.mesh.node.cap("disclose", "--root", a["outcome"]["record_id"], "--out", str(a["dir"] / "outcome.bundle.json"))

    # --- the goose asker polls ---------------------------------------------------------------------------------
    async def goose_poll(self, name):
        return await self.goose.call("loop_ask_mailbox_poll", request_event_id=self.g[name]["tool"]["request_event_id"])

    def goose_keep_reply(self, name):
        a = self.g[name]
        got = self.reply_to(a["event_id"])
        if got is not None:
            reply, at = got
            (a["dir"] / "answer.json").write_bytes(reply)
            self.g2m_log.note(name, "delivered", at, reply_sha256=hashlib.sha256(reply).hexdigest())

    async def goose_disclose(self, name, record_id):
        await self.goose.call("loop_book_bundle", book_id="goose/node", record_id=record_id,
                              out_path=str(self.g[name]["dir"] / "outcome.bundle.json"), disclose=True)


async def session(out, tmp, relay, capsulectl, plant):
    mesh = Mesh(out, tmp, capsulectl)

    def params():
        env = {**os.environ, "GOOSE_HOME": str(tmp / "goose-home"), "GOOSE_OPERATOR": "goose-rehearsal"}
        return StdioServerParameters(command=sys.executable, args=[str(SRC / "loop_server.py")], env=env)

    async with stdio_client(params()) as (gr, gw):
        async with ClientSession(gr, gw) as gs:
            await gs.initialize()
            goose = Goose(gs, GOOSE)
            joined = await goose.call("loop_join", commitment_dir=str(tmp / "goose-joined"))
            await goose.call("loop_advertise", out_dir=str(tmp / "goose-commitments"), relays=ADVERTISED_RELAY,
                             answers_within=ANSWERS_WITHIN, poll_interval=POLL_INTERVAL)
            gkey, gckpt = joined["key_id"], joined["checkpoint_key_id"]
            adv = out / GOOSE / "advertised"
            (adv / "policies").mkdir(parents=True)
            shutil.copyfile(tmp / "goose-commitments" / f"{gkey}.json", adv / "commitment.json")
            shutil.copyfile(tmp / "goose-commitments" / "policies" / f"{gkey}.json", adv / "policies" / f"{gkey}.json")
            gmailbox = json.loads((adv / "commitment.json").read_text())["ask"]["nostr"]["mailbox_key"]
            nodes = {MESH: {"ecosystem": "mesh-llm", "key_id": mesh.node.key, "node_id": mesh.node.key,
                            "checkpoint_key": mesh.node.checkpoint_key, "first_record": mesh.node.first_record},
                     GOOSE: {"ecosystem": "goose", "key_id": gkey, "node_id": gkey, "checkpoint_key": gckpt,
                             "first_record": joined["record_id"]}}
            (out / "nodes.json").write_text(json.dumps(nodes, indent=2, sort_keys=True) + "\n")
            (out / "entries").mkdir()
            for n in nodes.values():
                (out / "entries" / f"{n['node_id']}.json").write_text(
                    json.dumps(entry_for(n["key_id"], "loop-rehearsal"), indent=2, sort_keys=True) + "\n")

            p = Pair(out, relay, mesh, goose, gkey, gckpt, gmailbox, joined["record_id"], plant)
            impostor = None
            if plant == "wrong-key":
                impostor = tmp / "impostor-key.pem"
                step07.step12.make_test_controller_key(impostor)
            on_time = {"wrong-key": ("exchange",), "unsigned-refusal": ("exchange",), "late-counted": ()}.get(
                plant, ("exchange", "history_card"))
            late = ("exchange_late",) if plant in (None, "late-counted") else ()

            # --- the asks that are answered in time ------------------------------------------------------------
            if on_time:
                p.send(on_time)
                await p.send_goose(on_time)
                unsigned = hashlib.sha256(p.g["exchange"]["request"]).hexdigest() if plant == "unsigned-refusal" else None
                await goose.call("loop_mailbox_poll")  # the goose node answers the mesh node's asks
                p.mesh.serve(unsigned)  # the mesh node's adapter answers the goose node's asks
                for name in on_time:
                    p.mesh_record_answer(name, impostor)
                    p.mesh_disclose(name)
                    p.goose_keep_reply(name)
                    got = await p.goose_poll(name)
                    if got["status"] == "answered":
                        await p.goose_disclose(name, got["record_id"])
                    elif plant != "unsigned-refusal":
                        raise SystemExit(f"goose -> mesh {name}: {got}")

            # --- the asks whose answer is posted only after the deadline --------------------------------------
            pending = {**{n: "late" for n in late}, **({"exchange": "unsigned"} if plant == "unsigned-refusal" else {})}
            if late:
                p.send(late)
                await p.send_goose(late)
            if pending:
                last = max([p.m[n]["deadline"] for n in late] + [dt.datetime.fromtimestamp(
                    p.g[n]["tool"]["deadline"], dt.timezone.utc) for n in (*late, *( ("exchange",) if plant == "unsigned-refusal" else ()))])
                while now() < last + dt.timedelta(seconds=1.5):
                    await asyncio.sleep(0.5)
                if plant == "unsigned-refusal":  # the goose node's poll sees no answer it can verify, so records an absence
                    for _ in range(20):
                        got = await p.goose_poll("exchange")
                        if got["status"] == "absent":
                            await p.goose_disclose("exchange", got["record_id"])
                            break
                        await asyncio.sleep(0.5)
                    else:
                        raise SystemExit(f"goose -> mesh exchange: no absence recorded: {got}")
                for name in late:
                    if plant == "late-counted":  # the reply that arrives after the deadline is counted as an answer
                        await goose.call("loop_mailbox_poll")
                        p.mesh_record_answer(name)
                    else:
                        p.mesh_record_absence(name)
                    p.mesh_disclose(name)
                    for _ in range(20):
                        got = await p.goose_poll(name)
                        if got["status"] == "absent":
                            await p.goose_disclose(name, got["record_id"])
                            break
                        await asyncio.sleep(0.5)
                    else:
                        raise SystemExit(f"goose -> mesh {name}: no absence recorded: {got}")
                # now, after the deadline, the late replies are posted
                await goose.call("loop_mailbox_poll")
                p.mesh.serve()
                for name in late:
                    if plant != "late-counted":
                        p.mesh_keep_late(name)
                    p.goose_keep_reply(name)
                    got = await p.goose_poll(name)
                    if not got.get("late"):
                        raise SystemExit(f"goose -> mesh {name}: the late reply was not kept: {got}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", type=Path)
    ap.add_argument("--plant", choices=PLANTS)
    a = ap.parse_args(argv)
    asyncio.run(run(a.out.resolve(), a.plant))
    return 0


if __name__ == "__main__":
    sys.exit(main())
