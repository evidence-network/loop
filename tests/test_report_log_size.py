"""Tests for the log size at checkpoint: report_layout.last_checkpoint() and the reporter's verified line.

The checkpoint statements in tests/reporter/fixtures/verified.json are the two
COSE_Sign1 cases of checkpointed-local-log's checkpoint conformance vectors
(checkpoint-conformance-vectors/cose-vectors.json): log sizes 3 and 11.

Run: python3 -B -m unittest discover -s tests -v
"""
import base64
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import daily_report  # noqa: E402
import report_layout as layout  # noqa: E402

FIXTURES = ROOT / "tests" / "reporter" / "fixtures"
VERIFIED = (FIXTURES / "verified.json").read_bytes()


def artifact(reply):
    text = reply["answer"]["artifact"]
    return json.loads(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)))


def with_artifact(art):
    """verified.json with its artifact replaced by `art`."""
    reply = json.loads(VERIFIED)
    reply["answer"]["artifact"] = base64.urlsafe_b64encode(json.dumps(art).encode()).decode().rstrip("=")
    return json.dumps(reply).encode()


class LastCheckpointTest(unittest.TestCase):
    def setUp(self):
        self.art = artifact(json.loads(VERIFIED))
        self.first, self.last = self.art["checkpoints"]

    def test_reads_the_anchor_checkpoints_log_size_and_time(self):
        self.assertEqual(layout.last_checkpoint(VERIFIED), (11, "2026-01-01T00:05:00Z"))

    def test_the_earlier_checkpoint_reads_on_its_own(self):
        art = {**self.art, "checkpoints": [self.first],
               "anchor": hashlib.sha256(bytes.fromhex(self.first)).hexdigest()}
        self.assertEqual(layout.last_checkpoint(with_artifact(art)), (3, "2026-01-01T00:00:00Z"))

    def test_a_history_card_reads_its_last_entry(self):
        art = {"anchor": self.art["anchor"], "derivation": "history_card/1", "evidence_stream": "stand-in",
               "history": [{"checkpoint": self.first, "consistency_from_previous": None},
                           {"checkpoint": self.last, "consistency_from_previous": "00"}],
               "subject": {"checkpoints": None}}
        self.assertEqual(layout.last_checkpoint(with_artifact(art)), (11, "2026-01-01T00:05:00Z"))

    def test_a_last_statement_that_is_not_the_anchor_is_not_read(self):
        """Planted: the list ends with a checkpoint other than the one the artifact is anchored at."""
        art = {**self.art, "checkpoints": [self.last, self.first]}
        self.assertIsNone(layout.last_checkpoint(with_artifact(art)))

    def test_altered_statement_bytes_are_not_read(self):
        """Planted: a statement whose bytes no longer hash to the anchor (log size 11 -> 12)."""
        altered = self.last.replace("686c6f675f73697a650b", "686c6f675f73697a650c", 1)
        self.assertNotEqual(altered, self.last)
        self.assertIsNone(layout.last_checkpoint(with_artifact({**self.art, "checkpoints": [self.first, altered]})))

    def test_no_checkpoint_or_no_artifact_is_none(self):
        for body in (with_artifact({**self.art, "checkpoints": []}),
                     (FIXTURES / "verified_history_card.json").read_bytes(),  # a stand-in with no statement
                     (FIXTURES / "refusal.json").read_bytes(),
                     b"not json", b"{}"):
            self.assertIsNone(layout.last_checkpoint(body), body[:40])

    def test_truncated_cbor_is_none(self):
        cut = self.last[:len(self.last) // 4 * 2]
        art = {**self.art, "checkpoints": [cut], "anchor": hashlib.sha256(bytes.fromhex(cut)).hexdigest()}
        self.assertIsNone(layout.last_checkpoint(with_artifact(art)))


class VerifiedLineTest(unittest.TestCase):
    def line(self, body):
        return daily_report.verification_line(200, body, None, "2" * 64, "2" * 64)

    def test_the_verified_line_carries_mmr_size_and_timestamp(self):
        self.assertEqual(self.line(VERIFIED),
                         f"verified checkpoints=2 anchor={'a' * 64} mmr_size=11 timestamp=2026-01-01T00:05:00Z "
                         f"key={'2' * 64} listed_key=match")

    def test_no_checkpoint_in_the_answer_leaves_them_off(self):
        line = self.line((FIXTURES / "verified_history_card.json").read_bytes())
        self.assertTrue(line.startswith("verified halves=2 "), line)
        self.assertNotIn("mmr_size", line)
        self.assertNotIn("timestamp", line)

    def test_only_a_verified_answer_gets_them(self):
        """The plugin, not the reporter, verifies: a reply it did not call an artifact gets no log size."""
        reply = json.loads(VERIFIED)
        reply["verification"] = {"state": "not_evidence", "why": "bad signature"}
        line = self.line(json.dumps(reply).encode())
        self.assertTrue(line.startswith("not_evidence "), line)
        self.assertNotIn("mmr_size", line)


if __name__ == "__main__":
    unittest.main()
