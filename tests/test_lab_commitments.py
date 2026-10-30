"""lab/commitments.py: a node's own signed commitment, published exactly as its console returned it."""
import base64
import contextlib
import io
import json
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lab"))
sys.path.insert(0, str(ROOT / "scripts"))
import check_commitment as cc  # noqa: E402
import commitments  # noqa: E402

FIX = ROOT / "tests/fixtures/commitment1/two-books"
KEY = ROOT / "tests/fixtures/step12/population/keys/alice/node-key.pem"


def signed(mutate=None):
    """(commitment, policy bytes) as the plugin returns them: policy at policies/<node_id>.json, signed by the TEST key."""
    doc = json.loads((FIX / "commitment.json").read_text())
    doc.pop("sig")
    doc["lcap"]["policy_uri"] = f"policies/{doc['node_id']}.json"
    if mutate:
        mutate(doc)
    return cc.sign(doc, KEY), (FIX / "disclosure-policy.json").read_bytes()


class Console:
    """A fake node console whose `commitment` tool answers with fixed bytes."""

    def __init__(self, body):
        self.body = body
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(outer.body)

            def log_message(self, *a):
                pass

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_port}"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def reply(doc, policy, **extra):
    """The console's reply text: unsorted, compact, so any re-serialization shows."""
    members = ['"policy":"%s"' % base64.b64encode(policy).decode(),
               '"commitment":' + json.dumps(doc, separators=(",", ":"))]
    members += [f'"{k}":{json.dumps(v)}' for k, v in extra.items()]
    return ("{" + ",".join(members) + "}").encode()


class CommitmentsTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        (self.root / "nodes").mkdir()
        self.doc, self.policy = signed()
        self.id = self.doc["node_id"]
        self.entry = self.root / "nodes" / f"{self.id}.json"
        self.entry.write_text(json.dumps({"node_id": self.id, "key_id": self.doc["key_id"]}))

    def publish(self, body, **kw):
        c = Console(body)
        self.addCleanup(c.close)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            commitments.publish(c.url, self.root, **kw)
        return out.getvalue()

    def refused(self, body):
        with self.assertRaises(SystemExit) as cm:
            self.publish(body)
        self.assertIn("commitments:", str(cm.exception))
        self.assertFalse((self.root / "nodes/commitments").exists(), "wrote despite refusing")
        return str(cm.exception)

    def test_writes_the_commitment_exactly_as_returned_and_the_policy_bytes(self):
        body = reply(self.doc, self.policy)
        self.publish(body)
        text = body.decode()
        member = text[text.index('"commitment":') + len('"commitment":'):text.rindex("}")]
        got = (self.root / f"nodes/commitments/{self.id}.json").read_bytes()
        self.assertEqual(got, (member + "\n").encode())
        self.assertNotEqual(got, (json.dumps(self.doc, indent=2, sort_keys=True) + "\n").encode())
        self.assertEqual((self.root / f"nodes/commitments/policies/{self.id}.json").read_bytes(), self.policy)

    def test_the_written_files_verify_the_way_step_03_reads_them(self):
        self.publish(reply(self.doc, self.policy))
        tree = self.root / "nodes/commitments"
        errs, _ = cc.verify(json.loads((tree / f"{self.id}.json").read_text()), cc.file_fetcher(tree))
        self.assertEqual(errs, [])

    def test_a_second_run_with_the_same_commitment_changes_nothing(self):
        body = reply(self.doc, self.policy)
        self.publish(body)
        self.assertIn("unchanged", self.publish(body))

    def test_a_changed_commitment_replaces_the_file(self):
        self.publish(reply(self.doc, self.policy))
        newer, _ = signed(lambda d: d.update(issued_at="2026-10-31T00:00:00Z"))
        out = self.publish(reply(newer, self.policy))
        self.assertIn("wrote", out)
        got = json.loads((self.root / f"nodes/commitments/{self.id}.json").read_text())
        self.assertEqual(got["issued_at"], "2026-10-31T00:00:00Z")

    def test_dry_run_writes_nothing(self):
        out = self.publish(reply(self.doc, self.policy), dry_run=True)
        self.assertIn("would write", out)
        self.assertFalse((self.root / "nodes/commitments").exists())

    def test_refuses_a_node_with_no_entry(self):
        self.entry.unlink()
        self.assertIn("no entry", self.refused(reply(self.doc, self.policy)))

    def test_refuses_a_key_the_entry_does_not_list(self):
        self.entry.write_text(json.dumps({"node_id": self.id, "key_id": "0" * 64}))
        self.assertIn("lists", self.refused(reply(self.doc, self.policy)))

    def test_refuses_a_tampered_commitment(self):
        bad = dict(self.doc, answers_within="P9D")
        self.assertIn("does not verify", self.refused(reply(bad, self.policy)))

    def test_refuses_a_member_outside_the_profile_such_as_a_controller_key(self):
        doc, policy = signed(lambda d: d.update(controller={"npub": "npub1" + "q" * 58}))
        self.assertIn("does not verify", self.refused(reply(doc, policy)))

    def test_accepts_only_the_binding_digest_of_a_binding(self):
        doc, policy = signed(lambda d: d.update(controller_binding={"digest": "a" * 64}))
        self.publish(reply(doc, policy))
        got = json.loads((self.root / f"nodes/commitments/{self.id}.json").read_text())
        self.assertEqual(got["controller_binding"], {"digest": "a" * 64})

    def test_refuses_a_policy_that_is_not_the_one_signed(self):
        self.assertIn("does not verify", self.refused(reply(self.doc, self.policy + b" ")))

    def test_refuses_a_policy_uri_step_03_would_not_find(self):
        doc, policy = signed(lambda d: d["lcap"].update(policy_uri="disclosure-policy.json"))
        self.assertIn("policy_uri", self.refused(reply(doc, policy)))

    def test_refuses_a_reply_with_no_commitment(self):
        self.assertIn("no commitment", self.refused(b'{"error":"no checkpoint yet"}'))


if __name__ == "__main__":
    unittest.main()
