"""scripts/check_join.py: signed joins and leaves, and the refusals.

Fixtures: tests/fixtures/join/, made by make_fixtures.py from the rehearsal TEST
keys. Needs `openssl` (3.0 or later) for Ed25519; without it the tests SKIP and say why.
"""
import datetime as dt
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "join"
KEYS = ROOT / "population" / "rehearsal" / "keys"
sys.path.insert(0, str(ROOT / "scripts"))
import check_commitment  # noqa: E402
import check_join  # noqa: E402
import check_nodes  # noqa: E402

NOW = dt.datetime(2026, 10, 28, 12, 0, 0, tzinfo=dt.timezone.utc)


def openssl_ok():
    try:
        r = subprocess.run(["openssl", "version"], capture_output=True, text=True)
    except OSError:
        return False
    return r.returncode == 0 and not r.stdout.startswith(("OpenSSL 1.", "LibreSSL"))


NO_OPENSSL = "openssl 3.0 or later is not on PATH: Ed25519 signing and verifying need it"


def load(name):
    return json.loads((FIX / name).read_text())


def errors(name, now=NOW):
    return check_join.check_file(FIX / name, now)[0]


def resign(doc, owner="alice"):
    return check_commitment.sign(doc, KEYS / owner / "node-key.pem")


class SwitchStatement(unittest.TestCase):
    def test_the_file_is_one_line_with_no_trailing_newline(self):
        raw = (ROOT / "joins" / "SWITCH_CONSENT.txt").read_bytes()
        self.assertTrue(raw)
        self.assertNotIn(b"\n", raw)
        self.assertNotIn(b"\r", raw)
        self.assertEqual(raw.decode("utf-8"), check_join.CONSENT)

    def test_the_verifier_uses_its_digest(self):
        raw = (ROOT / "joins" / "SWITCH_CONSENT.txt").read_bytes()
        self.assertEqual(check_join.CONSENT_SHA256, hashlib.sha256(raw).hexdigest())

    def test_the_verifier_reads_it_from_the_repository_not_the_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run([sys.executable, "-B", "-c", "import check_join; print(check_join.CONSENT_SHA256)"],
                               cwd=tmp, env={"PYTHONPATH": str(ROOT / "scripts")}, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), check_join.CONSENT_SHA256)


@unittest.skipUnless(openssl_ok(), NO_OPENSSL)
class Fixtures(unittest.TestCase):
    def test_good_join_passes(self):
        self.assertEqual(errors("good-join.json"), [])

    def test_good_leave_passes(self):
        self.assertEqual(errors("good-leave.json"), [])

    def test_forged_join_signed_by_bob_is_refused(self):
        self.assertEqual(errors("forged-join.json"), ["signature does not verify under spki"])

    def test_forged_join_with_bobs_key_claiming_alices_key_id_is_refused(self):
        self.assertEqual(errors("forged-join-own-key.json"), ["key_id is not the key in spki"])

    def test_stale_consent_is_refused_and_named(self):
        errs = errors("stale-consent-join.json")
        self.assertEqual(len(errs), 1)
        self.assertIn("the first operator statement", errs[0])

    def test_extra_handle_field_is_refused(self):
        self.assertEqual(errors("handle-join.json"), ["unknown field handle"])

    def test_the_join_carries_no_personal_field(self):
        self.assertEqual(set(load("good-join.json")), set(check_join.FIELDS))
        self.assertFalse({"handle", "operator", "nostr", "npub", "email"} & set(check_join.FIELDS))

    def test_nodes_consent_join_is_refused_and_named(self):
        self.assertEqual(errors("nodes-consent-join.json"),
                         ["consent_sha256 is the digest of the nodes/ operator statement; "
                          "a join agrees to the switch statement in joins/SWITCH_CONSENT.txt"])

    def test_consent_digest_is_of_the_switch_statement_file(self):
        raw = (ROOT / "joins" / "SWITCH_CONSENT.txt").read_bytes()
        self.assertEqual(load("good-join.json")["consent_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(check_join.CONSENT_SHA256, hashlib.sha256(raw).hexdigest())
        self.assertNotEqual(check_join.CONSENT, check_nodes.CONSENT)

    def test_generator_reproduces_the_committed_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([sys.executable, "-B", str(FIX / "make_fixtures.py"), tmp], check=True)
            made = sorted(p.name for p in Path(tmp).glob("*.json"))
            self.assertEqual(made, sorted(p.name for p in FIX.glob("*.json")))
            for name in made:
                self.assertEqual((Path(tmp) / name).read_bytes(), (FIX / name).read_bytes(), name)


@unittest.skipUnless(openssl_ok(), NO_OPENSSL)
class Refusals(unittest.TestCase):
    def check(self, doc, now=NOW):
        return check_join.verify(doc, now)

    def test_tampered_field_breaks_the_signature(self):
        doc = load("good-join.json")
        doc["at"] = "2026-10-27T20:00:01Z"
        self.assertEqual(self.check(doc), ["signature does not verify under spki"])

    def test_missing_field_is_refused(self):
        doc = load("good-join.json")
        del doc["consent_sha256"]
        self.assertIn("missing consent_sha256", self.check(doc))

    def test_unknown_kind_is_refused(self):
        doc = resign({**load("good-join.json"), "kind": "join/2"})
        self.assertEqual(self.check(doc), ["kind must be one of join/1, leave/1"])

    def test_unknown_consent_digest_is_refused(self):
        doc = resign({**load("good-join.json"), "consent_sha256": "0" * 64})
        self.assertEqual(self.check(doc),
                         ["consent_sha256 is not the digest of the switch statement in joins/SWITCH_CONSENT.txt"])

    def test_at_more_than_five_minutes_ahead_is_refused(self):
        doc = load("good-join.json")
        at = check_join.parse_at(doc["at"])
        self.assertEqual(self.check(doc, now=at - dt.timedelta(minutes=5)), [])
        self.assertEqual(self.check(doc, now=at - dt.timedelta(minutes=5, seconds=1)),
                         [f"at {doc['at']} is in the future"])

    def test_at_must_be_utc(self):
        doc = resign({**load("good-join.json"), "at": "2026-10-27T20:00:00+00:00"})
        self.assertEqual(self.check(doc), ["at must be YYYY-MM-DDTHH:MM:SSZ (UTC)"])

    def test_personal_data_in_the_file_is_refused(self):
        doc = resign({**load("good-join.json"), "contact": "someone@" + "mail.org"})
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "j.json"
            path.write_text(json.dumps(doc, indent=2))
            errs = check_join.check_file(path, NOW)[0]
        self.assertIn("unknown field contact", errs)
        self.assertTrue(any(e.endswith("email address") for e in errs), errs)


@unittest.skipUnless(openssl_ok(), NO_OPENSSL)
class Directory(unittest.TestCase):
    def run_dir(self, docs):
        with tempfile.TemporaryDirectory() as tmp:
            for name, doc in docs.items():
                (Path(tmp) / name).write_text(json.dumps(doc, indent=2))
            return check_join.check_path(Path(tmp), NOW)

    def test_join_then_leave_passes(self):
        self.assertEqual(self.run_dir({"a.json": load("good-join.json"), "b.json": load("good-leave.json")}),
                         ([], 2))

    def test_leave_older_than_its_join_is_refused(self):
        leave = resign({**load("good-leave.json"), "at": "2026-10-27T19:00:00Z"})
        errs, _ = self.run_dir({"a.json": load("good-join.json"), "b.json": leave})
        self.assertEqual(errs, ["b.json: the leave at 2026-10-27T19:00:00Z is older than its join"])

    def test_leave_with_no_join_is_refused(self):
        errs, _ = self.run_dir({"b.json": load("good-leave.json")})
        self.assertEqual(errs, ["b.json: a leave with no join for its node_id"])

    def test_leave_by_another_key_is_refused(self):
        # bob signs a valid leave of his own key for alice's node_id
        der = check_commitment.spki_from_pem((KEYS / "bob" / "node-key.pub.pem").read_text())
        leave = resign({**load("good-leave.json"), "key_id": check_commitment.key_id_of(der),
                        "spki": load("forged-join-own-key.json")["spki"]}, "bob")
        errs, _ = self.run_dir({"a.json": load("good-join.json"), "b.json": leave})
        self.assertEqual(errs, ["b.json: the leave is signed by another key than its join"])

    def test_cli_refuses_the_fixture_folder_and_passes_the_good_pair(self):
        script = str(ROOT / "scripts" / "check_join.py")
        r = subprocess.run([sys.executable, "-B", script, str(FIX)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        for name in ("forged-join.json", "forged-join-own-key.json", "stale-consent-join.json",
                     "nodes-consent-join.json", "handle-join.json"):
            self.assertIn(name, r.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("good-join.json", "good-leave.json"):
                shutil.copy(FIX / name, tmp)
            r = subprocess.run([sys.executable, "-B", script, tmp], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
