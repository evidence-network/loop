"""steps/ledger_proof.py: keeping a node's ledger file with its checkpoint reply, and judging the kept copies (loop#288).

The cryptography is loop-ledger-check's (steps/ledger-check.d); the tests that run it on the real signed fixture
(tests/fixtures/ledger-check, made by the crate's `fixture` test) SKIP without it, or with LOOP_REQUIRE_TOOLS=1 (CI,
after the setups ran) fail to load.
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "steps"))
import ledger_proof  # noqa: E402
import live  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "ledger-check"
NO_CHECKER = "loop-ledger-check is not installed: run steps/ledger-check.setup.sh (CI does)"
if ledger_proof.checker() is None and os.environ.get("LOOP_REQUIRE_TOOLS") == "1":
    raise RuntimeError(f"LOOP_REQUIRE_TOOLS=1: {NO_CHECKER}")


def settlement(preimage="ab" * 32):
    return {"capsule_id": "c1", "model_attestation": {"compute_attestation": {"x-mesh-settlement-v1": {
        "payment_hash": "cd" * 32, "preimage": preimage, "observed_by": "payer"}}}}


def kept_line(record):
    """The line live.copy_redacted keeps for `record`'s line."""
    with tempfile.TemporaryDirectory() as tmp:
        src, dest = Path(tmp) / "src", Path(tmp) / "dest"
        src.write_text(json.dumps(record) + "\n")
        live.copy_redacted(src, dest)
        return dest.read_bytes().split(b"\n")[0]


class HeldAPreimage(unittest.TestCase):
    CHAIN = ledger_proof.CHAIN

    def test_a_settlement_observations_withheld_preimage_in_the_chain_is_one(self):
        self.assertTrue(ledger_proof.held_a_preimage(kept_line(settlement()), self.CHAIN))

    def test_a_marker_anywhere_else_is_not(self):
        sha = hashlib.sha256(b"x").hexdigest()
        decoy = {"capsule_id": "c", "received_from": "00" * 32,  # a restated line carrying a decoy settlement
                 "decoy": {"payment_hash": "cd", "preimage": live.REDACTED}}
        moved = settlement(live.REDACTED)
        moved["model_attestation"]["compute_attestation"]["note"] = live.REDACTED
        cases = {
            "a decoy settlement object off the plugin's path": decoy,
            "no payment_hash beside it": {"model_attestation": {"compute_attestation": {"x-mesh-settlement-v1": {
                "preimage": live.REDACTED}}}},
            "the marker also under another key": moved,
            "the marker inside a longer string": settlement(live.REDACTED + " and more"),
            "no withheld value at all": settlement(None),
        }
        for why, rec in cases.items():
            with self.subTest(why):
                line = json.dumps(dict(rec, **{live.REDACTION: {"line_sha256": sha}})).encode()
                self.assertFalse(ledger_proof.held_a_preimage(line, self.CHAIN))
        with self.subTest("a genuine settlement line in a file that holds no preimage"):
            self.assertFalse(ledger_proof.held_a_preimage(kept_line(settlement()), "received-capsules.jsonl"))
        with self.subTest("no marker digest"):
            rec = json.loads(kept_line(settlement()))
            rec[live.REDACTION]["line_sha256"] = "not a digest"
            self.assertFalse(ledger_proof.held_a_preimage(json.dumps(rec).encode(), self.CHAIN))
        with self.subTest("not JSON"):
            self.assertFalse(ledger_proof.held_a_preimage(live.REDACTED.encode(), self.CHAIN))


class KeepChecked(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.src = Path(self.tmp.name) / "ledger" / "received-capsules.jsonl"
        self.src.parent.mkdir()
        self.dest = Path(self.tmp.name) / "out" / "lab-a" / "ledger" / "received-capsules.jsonl"
        self.asked = []

    def http(self, covers):
        """A console whose reply covers `covers` lines; None refuses. The file grows by one line once asked, as a
        node's file may between the ask and the copy."""
        def answer(method, url, body, timeout):
            self.asked.append(url)
            with self.src.open("a") as f:
                f.write('{"appended": "after the ask"}\n')
            if covers is None:
                return 400, b"invalid request, ask again later"
            return 200, json.dumps({"ledger_checkpoint": 1, "file": self.src.name,
                                    "range_proof": {"from_index": 0, "to_index": covers - 1}}).encode()
        return answer

    def beside(self):
        return json.loads(Path(f"{self.dest}{ledger_proof.KEPT_SUFFIX}").read_text())

    def test_the_copy_is_cut_to_the_lines_the_reply_covers(self):
        self.src.write_text('{"a":1}\n' + json.dumps(settlement()) + "\n")
        ledger_proof.keep_checked("http://node", self.src, self.dest, self.src.name, http=self.http(2))
        self.assertEqual(self.asked, [f"http://node{ledger_proof.ROUTE}?file=received-capsules.jsonl"])
        kept = self.dest.read_bytes().split(b"\n")
        self.assertEqual(len(kept), 3)  # two lines, each newline-ended; the line appended after the ask is not kept
        self.assertIn(live.REDACTION.encode(), kept[1])
        self.assertNotIn(b"ab" * 32, self.dest.read_bytes())
        self.assertEqual(self.beside()["reply"]["range_proof"]["to_index"], 1)

    def test_a_refusal_keeps_the_whole_copy_and_says_why(self):
        self.src.write_text('{"a":1}\n')
        ledger_proof.keep_checked("http://node", self.src, self.dest, self.src.name, http=self.http(None))
        self.assertEqual(self.dest.read_text().count("\n"), 2)
        self.assertEqual(self.beside()["reply"], None)
        self.assertIn("HTTP 400", self.beside()["refusal"])

    def test_no_console_asks_nothing(self):
        self.src.write_text('{"a":1}\n')
        ledger_proof.keep_checked(None, self.src, self.dest, self.src.name, http=self.http(1))
        self.assertEqual(self.asked, [])
        self.assertIn("no console", self.beside()["refusal"])

    def test_a_file_the_node_does_not_hold_is_asked_about_and_not_copied(self):
        ledger_proof.keep_checked("http://node", self.src, self.dest, self.src.name,
                                  http=lambda *a: (400, b"no complete line yet"))
        self.assertFalse(self.dest.exists())
        self.assertEqual(self.beside()["file"], self.src.name)

    def test_consoles_reads_name_url_pairs(self):
        old = os.environ.get(ledger_proof.CONSOLES)
        self.addCleanup(lambda: os.environ.pop(ledger_proof.CONSOLES, None) if old is None else
                        os.environ.update({ledger_proof.CONSOLES: old}))
        os.environ[ledger_proof.CONSOLES] = "a=http://x:1/,b=http://y:2"
        self.assertEqual(ledger_proof.consoles(), {"a": "http://x:1", "b": "http://y:2"})
        os.environ[ledger_proof.CONSOLES] = "a"
        with self.assertRaises(ValueError):
            ledger_proof.consoles()


@unittest.skipUnless(ledger_proof.checker(), NO_CHECKER)
class JudgeOnTheSignedFixture(unittest.TestCase):
    """judge over a node's kept copies, on the real signed replies: the chain (5 lines covered of 6 kept, the copy cut
    as keep_checked cuts it) and a sha256-line file holding a settlement observation's withheld preimage."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = Path(self.tmp.name) / "lab-a" / "ledger"
        self.ledger.mkdir(parents=True)
        self.key = (FIXTURE / "key").read_text().strip()
        for f in ("capsules.jsonl", "received-capsules.jsonl"):
            reply = json.loads((FIXTURE / f.replace(".jsonl", ".reply.json")).read_text())
            raw = (FIXTURE / f).read_bytes()
            (self.ledger / f).write_bytes(ledger_proof.prefix(raw, ledger_proof.covered(reply)))
            Path(f"{self.ledger / f}{ledger_proof.KEPT_SUFFIX}").write_text(
                json.dumps({"file": f, "reply": reply, "refusal": None}))
        self.files = ("capsules.jsonl", "received-capsules.jsonl", "received-inclusion.jsonl")
        Path(f"{self.ledger / 'received-inclusion.jsonl'}{ledger_proof.KEPT_SUFFIX}").write_text(
            json.dumps({"file": "received-inclusion.jsonl", "reply": None, "refusal": "HTTP 400: no complete line"}))

    def judge(self, key=None):
        return ledger_proof.judge(self.ledger, self.files, key or self.key, "lab-a")

    def test_the_kept_copies_check(self):
        errs, notes, proof = self.judge()
        self.assertEqual((errs, notes), ([], []))  # the file the node held no line of has nothing to show
        self.assertEqual(proof, ["lab-a/ledger/capsules.jsonl.checkpoint.json", "lab-a/ledger/capsules.jsonl",
                                 "lab-a/ledger/received-capsules.jsonl.checkpoint.json",
                                 "lab-a/ledger/received-capsules.jsonl",
                                 "lab-a/ledger/received-inclusion.jsonl.checkpoint.json"])

    def test_another_key_fails(self):
        errs, _, _ = self.judge("0" * 64)
        self.assertEqual(len(errs), 2, errs)
        self.assertTrue(all("not by the given key" in e for e in errs), errs)

    def test_a_restated_line_a_moved_marker_and_a_longer_copy_fail(self):
        path = self.ledger / "received-capsules.jsonl"
        good = path.read_bytes()
        lines = good.split(b"\n")
        plain = json.loads(lines[0])
        # A marker naming line 2's own digest, which the checker takes as its leaf, on any other content: in a file
        # the plugin keeps no preimage in, that is a restated line.
        forged = dict(settlement(live.REDACTED), **{live.REDACTION: {"line_sha256": hashlib.sha256(lines[1]).hexdigest()}})
        cases = {
            "a restated line": (b"\n".join([b'{"a":2}', *lines[1:]]), "not leaves"),
            "a marker in a file that holds no preimage": (
                b"\n".join([lines[0], json.dumps(forged, separators=(",", ":")).encode(), *lines[2:]]),
                "did not hold a payment preimage"),
            "a line past the checkpoint": (good + json.dumps(plain).encode() + b"\n", "holds lines past the 3"),
        }
        for why, (raw, says) in cases.items():
            with self.subTest(why):
                path.write_bytes(raw)
                errs, _, _ = self.judge()
                self.assertEqual(len(errs), 1, errs)
                self.assertIn(says, errs[0])
        path.write_bytes(good)

    def test_a_reply_with_no_copy_and_a_lost_reply_fail(self):
        (self.ledger / "received-capsules.jsonl").unlink()
        Path(f"{self.ledger / 'capsules.jsonl'}{ledger_proof.KEPT_SUFFIX}").unlink()
        errs, _, _ = self.judge()
        self.assertEqual(sorted(errs), sorted([
            "capsules.jsonl.checkpoint.json is missing or not readable (FileNotFoundError: "
            f"[Errno 2] No such file or directory: '{self.ledger / 'capsules.jsonl'}{ledger_proof.KEPT_SUFFIX}')",
            "received-capsules.jsonl: the node signed a checkpoint over it, but no copy is kept"]))

    def test_a_refusal_over_a_copy_no_key_and_no_checker_are_not_checked_never_a_pass(self):
        Path(f"{self.ledger / 'received-capsules.jsonl'}{ledger_proof.KEPT_SUFFIX}").write_text(
            json.dumps({"file": "received-capsules.jsonl", "reply": None, "refusal": "HTTP 400: ask again later"}))
        errs, notes, _ = self.judge()
        self.assertEqual(errs, [])
        self.assertEqual([(n["what"], n["issue"]) for n in notes],
                         [("lab-a: each copied ledger file is the node's whole file", ledger_proof.ISSUE)])
        errs, notes, _ = ledger_proof.judge(self.ledger, self.files, None, "lab-a")
        self.assertIn("no nodes/ listing", notes[0]["why"])
        self.assertEqual({n["issue"] for n in notes}, {live.ISSUE, ledger_proof.ISSUE})  # one entry per owner
        old = os.environ.get("LOOP_LEDGER_CHECK_BIN")
        os.environ["LOOP_LEDGER_CHECK_BIN"] = str(Path(self.tmp.name) / "none")
        try:
            errs, notes, _ = self.judge()
        finally:
            os.environ.pop("LOOP_LEDGER_CHECK_BIN") if old is None else os.environ.update(LOOP_LEDGER_CHECK_BIN=old)
        self.assertEqual(errs, [])
        self.assertIn(ledger_proof.NOT_INSTALLED, next(n["why"] for n in notes if n["issue"] == ledger_proof.TOOLS_ISSUE))


class ChainOnTheShelf(unittest.TestCase):
    """A node on the shelf keeps ledger/shelf.db and no capsules.jsonl: the chain is `capsules chain-lines` (loop#296)."""
    LINES = b'{"capsule_id": "a"}\n{"padding": 1}\n'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"
        (self.data / "ledger").mkdir(parents=True)
        self.bin = Path(self.tmp.name) / "capsules"
        self.args = Path(self.tmp.name) / "args"

    def fake(self, code):
        self.bin.write_text(f"#!/bin/sh\necho \"$@\" > {self.args}\n{code}\n")
        self.bin.chmod(0o755)
        env = mock.patch.dict(os.environ, {live.CAPSULES_BIN: str(self.bin)})
        env.start()
        self.addCleanup(env.stop)

    def test_a_shelf_node_is_read_through_chain_lines(self):
        (self.data / "ledger" / live.CHAIN_STORE).write_bytes(b"sqlite")
        self.fake("printf '%s' '{\"capsule_id\": \"a\"}\n{\"padding\": 1}\n'")
        self.assertEqual(live.chain_bytes(self.data / "ledger"), self.LINES)
        self.assertEqual(self.args.read_text().split(), ["chain-lines", "--data-dir", str(self.data)])
        self.assertEqual(live.readable({"data_dir": str(self.data)}, "data_dir", "ledger/capsules.jsonl"), self.data)

    def test_a_flat_node_is_read_from_its_file(self):
        (self.data / "ledger" / "capsules.jsonl").write_bytes(self.LINES)
        self.assertEqual(live.chain_bytes(self.data / "ledger"), self.LINES)

    def test_no_chain_is_not_readable(self):
        self.assertIsNone(live.readable({"data_dir": str(self.data)}, "data_dir", "ledger/capsules.jsonl"))
        with self.assertRaises(OSError):
            live.chain_bytes(self.data / "ledger")

    def test_a_failing_chain_lines_is_an_unreadable_chain(self):
        (self.data / "ledger" / live.CHAIN_STORE).write_bytes(b"sqlite")
        self.fake("echo cannot read >&2; exit 1")
        with self.assertRaises(OSError):
            live.chain_bytes(self.data / "ledger")

    def test_the_kept_copy_is_named_capsules_jsonl_with_the_preimage_taken_out(self):
        (self.data / "ledger" / live.CHAIN_STORE).write_bytes(b"sqlite")
        line = json.dumps(settlement())
        self.fake(f"printf '%s\\n' '{line}'")
        dest = Path(self.tmp.name) / "out" / "ledger" / "capsules.jsonl"
        ledger_proof.keep_checked(None, self.data / "ledger" / "capsules.jsonl", dest, "capsules.jsonl")
        self.assertEqual(dest.read_bytes(), kept_line(settlement()) + b"\n")
        self.assertNotIn(b"ab" * 32, dest.read_bytes())


if __name__ == "__main__":
    unittest.main()
