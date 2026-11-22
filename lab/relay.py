#!/usr/bin/env python3
"""The lab's standing NIP-01 relay (evidence-network/loop#442, #443): one process on the lab host that the mesh nodes'
capsules plugin (CAPSULES_NOSTR_RELAY_MAP) and the Goose nodes (GOOSE_RELAY_MAP) both reach, so a live cross run of step 13
does not depend on a relay inside one session process.

  relay.py serve --port N --log FILE [--host 127.0.0.1]

It is the rehearsal's relay (scripts/goose_ask_session.py Relay) made standing: it stores every event it is sent, answers a
REQ with the stored events whose kind and `#x` tags match, and checks nothing (the nodes check the events). Each event it
receives is also appended to FILE as one JSON line {"received": <UTC time>, "event": <event>}, so a driver in another
process can read what the relay carried and when (the carrier log step 13 judges lateness by). The store is in memory:
a restart of the relay forgets the mailbox, and a node publishes again. It listens on 127.0.0.1 only; "no authenticating
public relay is approved yet" (lab/goose.sh), and a mailbox is readable by whoever reads the relay.
"""
import argparse
import datetime as dt
import json
import threading
from pathlib import Path

from websockets.exceptions import ConnectionClosed
from websockets.sync.server import serve


class Relay:
    def __init__(self, log):
        self.events, self.lock, self.log = {}, threading.Lock(), Path(log)

    def handle(self, ws):
        try:
            self.serve_connection(ws)
        except ConnectionClosed:
            pass  # a client that went away

    def serve_connection(self, ws):
        for raw in ws:
            try:
                msg = json.loads(raw)
                kind = msg[0]
            except (ValueError, IndexError, TypeError, KeyError):  # not JSON, or not a NIP-01 frame (an object has no [0])
                continue
            if kind == "EVENT" and len(msg) > 1 and isinstance(msg[1], dict):
                ev = msg[1]
                if not isinstance(ev.get("id"), str):  # NIP-01: an event has an id; answer, do not drop the connection
                    ws.send(json.dumps(["OK", None, False, "invalid: the event has no id"]))
                    continue
                with self.lock:
                    new = ev.get("id") not in self.events
                    if new:
                        self.events[ev["id"]] = ev
                        with self.log.open("a") as f:
                            f.write(json.dumps({"received": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                                                "event": ev}, sort_keys=True) + "\n")
                ws.send(json.dumps(["OK", ev.get("id"), True, ""]))
            elif kind == "REQ" and len(msg) > 2:
                with self.lock:
                    held = list(self.events.values())
                for ev in held:
                    if any(self.matches(ev, flt) for flt in msg[2:] if isinstance(flt, dict)):
                        ws.send(json.dumps(["EVENT", msg[1], ev]))
                ws.send(json.dumps(["EOSE", msg[1]]))

    @staticmethod
    def matches(ev, flt):
        if "kinds" in flt and ev.get("kind") not in flt["kinds"]:
            return False
        if "ids" in flt and ev.get("id") not in flt["ids"]:
            return False
        if "authors" in flt and ev.get("pubkey") not in flt["authors"]:
            return False
        return all(any(t[0] == key[1:] and t[1] in want for t in ev.get("tags", []) if len(t) > 1)
                   for key, want in flt.items() if key.startswith("#"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, required=True)
    s.add_argument("--log", type=Path, required=True)
    s.add_argument("--host", default="127.0.0.1")
    a = ap.parse_args(argv)
    a.log.parent.mkdir(parents=True, exist_ok=True)
    relay = Relay(a.log)
    with serve(relay.handle, a.host, a.port, max_size=4 * 1024 * 1024) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
