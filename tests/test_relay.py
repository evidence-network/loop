"""The lab's standing NIP-01 relay (lab/relay.py, evidence-network/loop#442): its filter, its event log and what it
answers, with a fake websocket (the websockets package is stubbed, so the logic runs where the package is not installed).

Run: python3 -m unittest tests.test_relay -v
"""
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_relay():
    stubs = {"websockets": types.ModuleType("websockets"),
             "websockets.exceptions": types.ModuleType("websockets.exceptions"),
             "websockets.sync": types.ModuleType("websockets.sync"),
             "websockets.sync.server": types.ModuleType("websockets.sync.server")}
    stubs["websockets.exceptions"].ConnectionClosed = type("ConnectionClosed", (Exception,), {})
    stubs["websockets.sync.server"].serve = lambda *a, **k: None
    saved = {k: sys.modules.get(k) for k in stubs}
    sys.modules.update(stubs)
    try:
        spec = importlib.util.spec_from_file_location("lab_relay", ROOT / "lab" / "relay.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


relay = load_relay()


class FakeWs:
    def __init__(self, frames):
        self.frames, self.sent = [json.dumps(f) for f in frames], []

    def __iter__(self):
        return iter(self.frames)

    def send(self, text):
        self.sent.append(json.loads(text))


def event(eid, kind=4700, pubkey="aa", tags=()):
    return {"id": eid, "kind": kind, "pubkey": pubkey, "tags": [list(t) for t in tags], "content": "x"}


class RelayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "events.jsonl"
        self.relay = relay.Relay(self.log)

    def tearDown(self):
        self.tmp.cleanup()

    def serve(self, *frames):
        ws = FakeWs(frames)
        self.relay.serve_connection(ws)
        return ws.sent

    def test_a_filter_matches_by_kind_id_author_and_tag(self):
        ev = event("e1", kind=4700, pubkey="aa", tags=[("p", "mailbox")])
        m = relay.Relay.matches
        self.assertTrue(m(ev, {"kinds": [4700], "#p": ["mailbox"]}))
        self.assertTrue(m(ev, {"ids": ["e1"], "authors": ["aa"]}))
        self.assertFalse(m(ev, {"kinds": [4701]}))
        self.assertFalse(m(ev, {"ids": ["e2"]}))
        self.assertFalse(m(ev, {"authors": ["bb"]}))
        self.assertFalse(m(ev, {"#p": ["other"]}))
        self.assertFalse(m(ev, {"#e": ["e1"]}))

    def test_an_event_is_stored_once_logged_with_its_receive_time_and_answered_ok(self):
        ev = event("e1")
        sent = self.serve(["EVENT", ev], ["EVENT", ev])
        self.assertEqual(sent, [["OK", "e1", True, ""], ["OK", "e1", True, ""]])
        lines = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(len(lines), 1, "a repeated event is not logged again")
        self.assertEqual(lines[0]["event"], ev)
        self.assertRegex(lines[0]["received"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")

    def test_an_event_without_a_string_id_is_refused_and_the_connection_goes_on(self):
        good = event("e2")
        sent = self.serve(["EVENT", {"kind": 4700}], ["EVENT", {"id": 7}], ["EVENT", good])
        self.assertEqual(sent[0][:3], ["OK", None, False])
        self.assertEqual(sent[1][:3], ["OK", None, False])
        self.assertEqual(sent[2], ["OK", "e2", True, ""])
        self.assertEqual(len(self.log.read_text().splitlines()), 1)

    def test_a_req_gets_the_matching_stored_events_then_eose(self):
        a, b = event("a", tags=[("p", "m1")]), event("b", tags=[("p", "m2")])
        sent = self.serve(["EVENT", a], ["EVENT", b], ["REQ", "sub", {"#p": ["m2"]}])
        self.assertEqual(sent[2:], [["EVENT", "sub", b], ["EOSE", "sub"]])

    def test_malformed_frames_are_skipped(self):
        ws = FakeWs([])
        ws.frames = ["not json", "[]", json.dumps({"a": 1}), json.dumps(["EVENT"])]
        self.relay.serve_connection(ws)
        self.assertEqual(ws.sent, [])


if __name__ == "__main__":
    unittest.main()
