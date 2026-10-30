"""lab/nostr_sign.py (BIP-340 against the BIP's vectors) and lab/bind.py (the plugin's join ceremony, against a fake node)."""
import csv
import hashlib
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lab"))
import bind  # noqa: E402
import nostr_sign as ns  # noqa: E402

VECTORS = ROOT / "tests" / "fixtures" / "bip340" / "test-vectors.csv"


class Bip340(unittest.TestCase):
    def rows(self):
        with open(VECTORS, newline="") as f:
            return list(csv.DictReader(f))

    def test_the_vector_file_has_all_nineteen_vectors(self):
        self.assertEqual(len(self.rows()), 19)

    def test_every_vector_verifies_as_the_bip_says(self):
        for r in self.rows():
            with self.subTest(index=r["index"]):
                got = ns.schnorr_verify(bytes.fromhex(r["message"]), bytes.fromhex(r["public key"]),
                                        bytes.fromhex(r["signature"]))
                self.assertEqual(got, r["verification result"] == "TRUE")

    def test_every_signing_vector_reproduces_the_signature(self):
        signing = [r for r in self.rows() if r["secret key"]]
        self.assertEqual(len(signing), 8)
        for r in signing:
            with self.subTest(index=r["index"]):
                sk = bytes.fromhex(r["secret key"])
                self.assertEqual(ns.pubkey(sk).hex(), r["public key"].lower())
                self.assertEqual(ns.schnorr_sign(bytes.fromhex(r["message"]), sk, bytes.fromhex(r["aux_rand"])).hex(),
                                 r["signature"].lower())

    def test_a_signed_event_has_the_nip01_id_and_a_signature_that_verifies(self):
        sk = bytes.fromhex("00" * 31 + "07")
        pub = ns.pubkey(sk).hex()
        event = {"pubkey": pub, "created_at": 1793000000, "kind": 3374, "tags": [], "content": "controller-binding/0:ab"}
        signed = ns.sign_event(event, sk)
        body = f'[0,"{pub}",1793000000,3374,[],"controller-binding/0:ab"]'
        self.assertEqual(signed["id"], hashlib.sha256(body.encode()).hexdigest())
        self.assertTrue(ns.schnorr_verify(bytes.fromhex(signed["id"]), bytes.fromhex(pub), bytes.fromhex(signed["sig"])))
        self.assertEqual({k: v for k, v in signed.items() if k not in ("id", "sig")}, event)

    def test_a_signature_or_key_of_the_wrong_length_does_not_verify(self):
        r = self.rows()[0]
        msg, pub, sig = (bytes.fromhex(r[k]) for k in ("message", "public key", "signature"))
        self.assertTrue(ns.schnorr_verify(msg, pub, sig))
        for bad_sig in (sig[:32] + b"\x00" + sig[32:], sig[:-1], sig + b"\x00", b""):
            self.assertFalse(ns.schnorr_verify(msg, pub, bad_sig))
        self.assertFalse(ns.schnorr_verify(msg, pub + b"\x00", sig))
        self.assertFalse(ns.schnorr_verify(msg, pub[:-1], sig))
        with self.assertRaises(ValueError):
            ns.pubkey(b"\x01" * 31)

    def test_an_event_for_another_key_is_refused(self):
        with self.assertRaises(ValueError):
            ns.sign_event({"pubkey": "11" * 32, "created_at": 1, "kind": 3374, "tags": [], "content": ""},
                          bytes.fromhex("00" * 31 + "07"))

    def test_a_made_key_is_private_and_kept(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "controllers", "lab-1", "nostr.key")
            first = ns.load_key(path, make=True)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertEqual(ns.load_key(path, make=True), first)
            self.assertEqual(len(first), 32)


class KeyMode(unittest.TestCase):
    def test_a_key_file_readable_beyond_its_user_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "nostr.key")
            ns.load_key(path, make=True)
            os.chmod(path, 0o640)
            with self.assertRaises(ValueError):
                ns.load_key(path)
            os.chmod(path, 0o600)
            self.assertEqual(len(ns.load_key(path)), 32)


class FakeNode:
    """A node console: join (the ceremony) and doctor, as the plugin answers them (capsules-plugin#121)."""

    def __init__(self, bound=None):
        node = self
        self.bound, self.joins, self.pending = bound, [], None

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path.endswith("/tools/doctor"):
                    f = {"check": "controller_binding", "state": "bound" if node.bound else "unbound"}
                    if node.bound:
                        f["records"] = [node.bound]
                    out = {"findings": [f]}
                elif "controller" in body:
                    node.pending = {"pubkey": body["controller"], "created_at": 1793000000, "kind": 3374, "tags": [],
                                    "content": "controller-binding/0:" + "cd" * 32}
                    out = {"controller_binding_ceremony": {"event": node.pending}}
                else:
                    ev = body["controller_event"]
                    ok = (node.pending and ev["pubkey"] == node.pending["pubkey"]
                          and ns.schnorr_verify(bytes.fromhex(ev["id"]), bytes.fromhex(ev["pubkey"]),
                                                bytes.fromhex(ev["sig"])))
                    if ok:
                        node.bound = "cd" * 32
                    out = {"joined": bool(ok)}
                node.joins.append(body)
                data = json.dumps(out).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class Bind(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.key = os.path.join(self.tmp.name, "controllers", "lab-1", "nostr.key")
        self.addCleanup(self.tmp.cleanup)

    def node(self, **kw):
        n = FakeNode(**kw)
        self.addCleanup(n.close)
        return n

    def test_the_ceremony_binds_the_node_and_reports_the_digest(self):
        n = self.node()
        digest, ran = bind.bind(n.url, self.key)
        self.assertEqual((digest, ran), ("cd" * 32, True))
        self.assertEqual(n.bound, "cd" * 32)
        self.assertEqual(n.pending["pubkey"], ns.pubkey(ns.load_key(self.key)).hex())

    def test_no_secret_key_or_event_reaches_the_output_or_the_disk(self):
        n = self.node()
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bind.main(["--console", n.url, "--key", self.key])
        secret = ns.load_key(self.key).hex()
        self.assertNotIn(secret, out.getvalue())
        self.assertIn("binding digest " + "cd" * 32, out.getvalue())
        self.assertEqual(sorted(os.listdir(os.path.dirname(self.key))), ["nostr.key"])

    def test_a_node_already_bound_is_left_as_it_is(self):
        n = self.node(bound="ab" * 32)
        digest, ran = bind.bind(n.url, self.key)
        self.assertEqual((digest, ran), ("ab" * 32, False))
        self.assertFalse(any("controller" in j or "controller_event" in j for j in n.joins))

    def test_rebind_runs_the_ceremony_again(self):
        n = self.node(bound="ab" * 32)
        digest, ran = bind.bind(n.url, self.key, rebind=True)
        self.assertTrue(ran)
        self.assertTrue(any("controller" in j for j in n.joins))

    def test_a_node_that_does_not_report_bound_afterwards_is_an_error(self):
        n = self.node()
        n.pending = None
        real = ns.schnorr_verify
        ns.schnorr_verify = lambda *a: False  # the fake then refuses the event
        try:
            with self.assertRaises(SystemExit):
                bind.bind(n.url, self.key)
        finally:
            ns.schnorr_verify = real


if __name__ == "__main__":
    unittest.main()
