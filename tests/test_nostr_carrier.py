"""Step 03's second carrier: a node's commitment read back from its Nostr announce event equals the git carrier's bytes.

Rehearsed on recorded relay responses (tests/fixtures/step03/nostr/cases/, made by make_nostr_fixtures.py); no relay is
contacted. The live read-back against a real producer waits on Mesh-LLM/capsules-plugin#212.
Run: python3 -B -m unittest tests.test_nostr_carrier -v
"""
import importlib.machinery
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "step03"
POPULATION = FIXTURES / "population" / "nostr"
CASES = FIXTURES / "nostr" / "cases"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "steps"))
import finding  # noqa: E402
import nostr_carrier as nc  # noqa: E402

TOOLS = shutil.which("openssl") and shutil.which("git")
SKIP_WHY = "SKIPPED LOUDLY: step 03 signs and verifies with the openssl command and publishes with git; one is missing"


def load_step():
    loader = importlib.machinery.SourceFileLoader("step03_advertise_nostr", str(ROOT / "steps" / "03-advertise"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@unittest.skipUnless(TOOLS, SKIP_WHY)
class NostrCarrier(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.step = load_step()
        # key_dir is read relative to the population's grandparent: keep the layout.
        shutil.copytree(FIXTURES, self.tmp / "step03", ignore=shutil.ignore_patterns("nostr", "__pycache__"))
        shutil.copytree(FIXTURES / "population" / "nostr", self.tmp / "step03" / "population" / "nostr", dirs_exist_ok=True)
        shutil.copytree(ROOT / "tests" / "fixtures" / "step12" / "population", self.tmp / "step12" / "population")
        self.pop = self.tmp / "step03" / "population" / "nostr"

    def run_case(self, case=None, recording=None):
        """Run step 03 on the population with the named case's recording as alice's (or none): (finding, alice, bob, out)."""
        target = self.pop / "advertise" / "alice" / "nostr.json"
        if recording is not None:
            target.write_text(json.dumps(recording))
        elif case == "none":
            target.unlink()
        elif case:
            shutil.copyfile(CASES / f"{case}.json", target)
        out = self.tmp / f"out-{case}" / "03-advertise"
        self.assertEqual(self.step.main(["--population", str(self.pop), "--out", str(out)]), 0)
        nodes = {n["name"]: n for n in json.loads((out / "verification.json").read_text())["nodes"]}
        return json.loads((out / "finding.json").read_text()), nodes["alice"], nodes["bob"], out

    def test_read_back_bytes_equal_git_and_the_finding_checks(self):
        f, alice, bob, out = self.run_case("good")
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertEqual(finding.check(out.parent), [])
        self.assertEqual(alice["nostr"]["result"], "PASS")
        self.assertEqual(alice["nostr"]["sha256"], alice["sha256"])  # the nostr octets are the git carrier's bytes
        self.assertEqual(alice["nostr"]["relays"], ["wss://relay.example", "wss://relay2.example"])
        self.assertIn("alice.nostr.json", [p["path"] for p in f["proof"]])
        self.assertIn("announce event read back", f["asserts"])
        self.assertIn("alice: PASS via git scratch repository (carrier.bundle) HEAD:nodes/commitments/", f["detail"])
        self.assertIn("and nostr", f["detail"])

    def test_advertising_off_is_not_advertised_on_nostr_not_a_failure(self):
        f, alice, bob, out = self.run_case("good")
        self.assertEqual(bob["result"], "PASS")
        self.assertEqual(bob["nostr"], {"result": "NOT_ADVERTISED", "note": "not advertised on nostr"})
        self.assertIn("bob: PASS via git", f["detail"])
        self.assertIn("not advertised on nostr", f["detail"])
        f, alice, bob, out = self.run_case("none")  # alice off too: the finding is the git carrier's alone
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertEqual(alice["nostr"]["note"], "not advertised on nostr")
        self.assertNotIn("announce event read back", f["asserts"])

    def assert_fails(self, case, reason):
        f, alice, bob, out = self.run_case(case)
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertEqual(alice["result"], "FAIL")
        self.assertTrue(any(e.startswith(reason + ":") for e in alice["errors"]), alice["errors"])
        self.assertEqual(bob["result"], "PASS")
        return alice

    def test_forged_event_fails_by_signature_or_id(self):
        self.assert_fails("forged-signature", "nostr.event_signature")
        self.assert_fails("forged-id", "nostr.event_id")

    def test_event_whose_digest_is_not_the_octets_fails(self):
        self.assert_fails("digest-mismatch", "nostr.digest_mismatch")

    def test_commitment_edited_in_a_validly_signed_event_fails_the_commitment_signature(self):
        alice = self.assert_fails("edited-commitment", "nostr.octets_invalid")
        self.assertIn("signature", " ".join(alice["errors"]))

    def test_key_other_than_the_announced_mailbox_key_is_relayed_not_announced(self):
        alice = self.assert_fails("relayed-only", "nostr.not_announced")
        self.assertEqual(alice["nostr"]["events"]["relayed"][0]["relays"], ["wss://relay.example", "wss://relay2.example"])

    def test_older_beside_newer_newer_wins_and_older_is_flagged(self):
        f, alice, bob, out = self.run_case("older-beside-newer")
        self.assertEqual(f["result"], "PASS", f["detail"])
        events = alice["nostr"]["events"]
        self.assertEqual([a["issued_at"] for a in events["superseded"]], ["2026-11-04T00:00:00Z"])
        self.assertEqual(events["superseded"][0]["relays"], ["wss://relay.example"])
        self.assertEqual(alice["nostr"]["sha256"], alice["sha256"])

    def test_only_an_older_commitment_on_the_relays_is_not_the_git_bytes(self):
        self.assert_fails("only-older", "nostr.bytes_differ")

    def test_relays_with_the_same_issued_at_and_different_octets_report_both_versions(self):
        alice = self.assert_fails("relays-conflict", "nostr.relays_conflict")
        text = " ".join(alice["errors"])
        self.assertEqual(text.count("sha256"), 0)
        for relay in ("wss://relay.example", "wss://relay2.example"):
            self.assertIn(relay, text)
        self.assertEqual(len({w for w in text.replace(",", " ").split() if len(w) == 64}), 2)

    def test_junk_beside_a_good_event_is_listed_and_does_not_fail_the_node(self):
        f, alice, bob, out = self.run_case("forged-beside-good")
        self.assertEqual(f["result"], "PASS", f["detail"])
        events = alice["nostr"]["events"]
        self.assertEqual([r["reason"] for r in events["rejected"]], ["nostr.event_signature"])
        self.assertEqual(len(events["relayed"]), 1)

    def test_future_dated_commitment_takes_no_part(self):
        f, alice, bob, out = self.run_case("future-dated-beside-good")
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertEqual([r["reason"] for r in alice["nostr"]["events"]["rejected"]], ["nostr.future_dated"])

    def test_event_for_another_node_is_refused(self):
        self.assert_fails("wrong-d", "nostr.event_form")

    def test_a_newer_commitment_with_a_rotated_mailbox_key_is_not_missed(self):
        # The event is attributed by the mailbox key its own commitment names (§5.3), so the rotated commitment is
        # announced, is the newest, and is not the git carrier's bytes.
        self.assert_fails("key-rotated", "nostr.bytes_differ")
        self.assert_fails("key-rotated-only", "nostr.bytes_differ")

    def test_malformed_events_are_rejected_one_by_one(self):
        f, alice, bob, out = self.run_case("malformed-beside-good")
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertEqual([r["reason"] for r in alice["nostr"]["events"]["rejected"]], ["nostr.event_form"] * 2)

    def test_an_event_without_octets_does_not_turn_a_failure_into_not_checked(self):
        alice = self.assert_fails("conflict-with-over-size", "nostr.relays_conflict")
        self.assertEqual(len(alice["nostr"]["events"]["unread"]), 1)

    def test_a_recording_that_is_not_an_object_fails_by_name(self):
        for text in ("null", "[]", "{"):
            (self.pop / "advertise" / "alice" / "nostr.json").write_text(text)
            f, alice, bob, out = self.run_case()
            self.assertEqual(alice["result"], "FAIL", text)
            self.assertTrue(any(e.startswith("nostr.recording:") for e in alice["errors"]), alice["errors"])

    def test_a_newer_commitment_with_another_policy_makes_the_git_copy_stale(self):
        # Another commitment's policy is not on the git carrier: its digest is not matched to bytes, so it still ranks.
        self.assert_fails("newer-policy-changed", "nostr.bytes_differ")

    def test_an_older_commitment_with_another_policy_is_flagged_superseded(self):
        f, alice, bob, out = self.run_case("older-policy-changed")
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertEqual([a["issued_at"] for a in alice["nostr"]["events"]["superseded"]], ["2026-11-04T00:00:00Z"])
        self.assertEqual(alice["nostr"]["events"]["rejected"], [])

    def test_a_commitment_the_node_signed_counts_in_v4_whoever_posted_it(self):
        alice = self.assert_fails("relayed-newer", "nostr.bytes_differ")
        events = alice["nostr"]["events"]
        self.assertEqual([r["issued_at"] for r in events["relayed"]], ["2026-11-05T06:00:00Z"])
        self.assertEqual([r["issued_at"] for r in events["superseded"]], ["2026-11-05T00:00:00Z"])
        self.assert_fails("relayed-conflict", "nostr.relays_conflict")

    def test_a_commitment_for_another_node_id_is_refused_even_under_this_d(self):
        f, alice, bob, out = self.run_case("other-node-inside")
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertEqual([r["reason"] for r in alice["nostr"]["events"]["rejected"]], ["nostr.octets_invalid"])

    def test_a_lone_surrogate_in_a_tag_rejects_that_event_only(self):
        f, alice, bob, out = self.run_case("surrogate-tag")
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertEqual([r["reason"] for r in alice["nostr"]["events"]["rejected"]], ["nostr.event_form"])

    def test_octets_that_are_not_a_commitment_are_refused_by_name(self):
        self.assert_fails("not-a-commitment", "nostr.octets_form")

    def test_an_unread_event_by_the_nodes_key_naming_the_git_digest_leaves_a_stale_inline_one_unproven(self):
        f, alice, bob, out = self.run_case("older-and-over-size")
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])

    def test_every_commitment_dated_after_the_read_fails_future_dated(self):
        self.assert_fails("only-future-dated", "nostr.future_dated")

    def test_a_read_at_that_is_not_a_utc_second_is_not_checked_not_passed(self):
        rec = json.loads((CASES / "good.json").read_text())
        rec["read_at"] = "2026-11-05T12:00:00.5Z"
        f, alice, bob, out = self.run_case(recording=rec)
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        self.assertTrue(any(e.startswith("nostr.recording:") for e in alice["errors"]))

    def test_a_deeply_nested_recording_fails_by_name_instead_of_crashing(self):
        (self.pop / "advertise" / "alice" / "nostr.json").write_text("[" * 300000)
        f, alice, bob, out = self.run_case()
        self.assertTrue(any(e.startswith("nostr.recording:") for e in alice["errors"]), alice["errors"])

    def test_a_recording_beside_a_git_carrier_that_failed_is_not_checked(self):
        def corrupt(carrier):
            for f in (carrier / "nodes" / "commitments").glob("*.json"):
                f.write_text("not json")
        self.step.PUBLISH_HOOK = corrupt
        shutil.copyfile(CASES / "good.json", self.pop / "advertise" / "alice" / "nostr.json")
        out = self.tmp / "out-gitfail" / "03-advertise"
        self.step.main(["--population", str(self.pop), "--out", str(out)])
        alice = {n["name"]: n for n in json.loads((out / "verification.json").read_text())["nodes"]}["alice"]
        self.assertEqual(alice["result"], "FAIL")
        self.assertEqual(alice["nostr"]["result"], "NOT_CHECKED")

    def test_an_unread_event_by_the_nodes_key_decides_only_what_it_can(self):
        for case, result in (("git-copy-plus-unread-other", "NOT_CHECKED"),  # it may be a newer commitment
                             ("outsider-copy-plus-unread-git", "PASS"),  # the node's own event names the git digest
                             ("forged-plus-unread", "NOT_CHECKED"),  # an outsider's forgery cannot make it a FAIL
                             ("outsider-copy-plus-unread-other", "NOT_CHECKED"),  # not a FAIL for the outsider's copy
                             ("future-copy-plus-unread-git", "NOT_CHECKED"),  # a future-dated copy takes no part
                             ("git-future-dated-plus-unread-git", "FAIL"),  # future-dated git bytes cannot be current
                             ("newer-inline-plus-unread-git", "FAIL")):  # git is older than the inline commitment
            f, alice, bob, out = self.run_case(case)
            self.assertEqual(f["result"], result, (case, f["detail"]))

    def test_hostile_octets_are_refused_by_the_checker_not_raised(self):
        check = self.step.octets_checker("00" * 32)
        self.assertEqual(check(b"[" * 300000)[0], None)
        good = json.loads((self.pop / "advertise" / "alice" / "signed" / "commitment.json").read_text())
        def with_card(raw):  # digest kept right so the checker goes on to read the card
            doc = json.loads(json.dumps(good))
            doc["history_cards"][0]["bytes"] = __import__("base64").b64encode(raw).decode()
            doc["history_cards"][0]["sha256"] = __import__("hashlib").sha256(raw).hexdigest()
            return doc
        deep, wrong_type = with_card(b"[" * 300000), with_card(json.dumps({"artifact": 5}).encode())
        for doc in (deep, wrong_type):
            doc_back, errs = check(json.dumps(doc).encode())  # returns, whichever way it refuses
            self.assertTrue(errs, errs)

    def test_a_read_at_past_the_last_representable_time_is_not_checked(self):
        rec = json.loads((CASES / "good.json").read_text())
        rec["read_at"] = "9999-12-31T23:59:59Z"
        f, alice, bob, out = self.run_case(recording=rec)
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])

    def test_no_event_on_any_relay_fails(self):
        self.assert_fails("no-events", "nostr.no_events")

    def test_event_with_no_octets_is_not_checked_not_passed(self):
        f, alice, bob, out = self.run_case("over-size")
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        self.assertTrue(any(e.startswith("nostr.over_size:") for e in alice["errors"]))

    def test_a_node_whose_commitment_names_no_nostr_route_cannot_be_announced(self):
        shutil.copytree(FIXTURES / "population" / "rehearsal", self.tmp / "step03" / "population" / "rehearsal",
                        dirs_exist_ok=True)
        pop = self.tmp / "step03" / "population" / "rehearsal"
        shutil.copyfile(CASES / "good.json", pop / "advertise" / "alice" / "nostr.json")
        out = self.tmp / "out-noroute" / "03-advertise"
        self.step.main(["--population", str(pop), "--out", str(out)])
        alice = {n["name"]: n for n in json.loads((out / "verification.json").read_text())["nodes"]}["alice"]
        self.assertEqual(alice["result"], "FAIL")
        self.assertTrue(any(e.startswith("nostr.no_mailbox_route:") for e in alice["errors"]), alice["errors"])


class EventForm(unittest.TestCase):
    def good(self):
        return json.loads((CASES / "good.json").read_text())["relays"]["wss://relay.example"][0]

    def test_the_recorded_event_is_well_formed_and_carries_the_octets(self):
        ev = self.good()
        self.assertIsNone(nc.event_error(ev))
        self.assertEqual(nc.tag_values(ev, "x")[0], __import__("hashlib").sha256(nc.octets_of(ev)).hexdigest())

    def test_each_form_rule_has_a_named_reason(self):
        for change, reason in ((lambda e: e.update(kind=1), "nostr.event_form"),
                               (lambda e: e.update(content="a=b"), "nostr.event_form"),
                               (lambda e: e["tags"].append(["d", "x"]), "nostr.event_form"),
                               (lambda e: e.update(pubkey="g" * 64), "nostr.event_form"),
                               (lambda e: e.update(created_at=e["created_at"] + 1), "nostr.event_id")):
            ev = self.good()
            change(ev)
            self.assertEqual(nc.event_error(ev)[0], reason)
        self.assertEqual(nc.event_error("nope")[0], "nostr.event_form")


if __name__ == "__main__":
    unittest.main()
