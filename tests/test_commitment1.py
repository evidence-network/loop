"""evidence-commitment/1: one stapled history card per book; /0 still reads, as one card.

Also: communities, a cleared history read as a fresh stream, binding verdicts (bound groups), and
the Nostr mailbox route ask.nostr.
Fixtures: tests/fixtures/commitment1/ (two-books/, cleared/, bound/, revoked/, nostr/; made by its
make_fixtures.py with join.sh and the pinned capsulectl), signed by TEST keys from
tests/fixtures/step12/population.
Run: python3 -B -m unittest tests.test_commitment1 -v
"""
import base64
import copy
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "commitment1"
TWO = FIX / "two-books"
CLEARED = FIX / "cleared"
NOSTR = FIX / "nostr"
KEY = ROOT / "tests" / "fixtures" / "step12" / "population" / "keys" / "alice" / "node-key.pem"
ZERO = sorted((ROOT / "lab" / "runs" / "2026-10-28" / "join").glob("*/commitment.json")) + \
    [ROOT / "tests" / "fixtures" / "step12" / "joined" / "alice" / "commitment.json"]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "steps"))
import check_binding as cb  # noqa: E402
import check_commitment as cc  # noqa: E402
import finding  # noqa: E402
import history_cards as hc  # noqa: E402

OPENSSL = shutil.which("openssl")
NO_OPENSSL = "SKIPPED LOUDLY: the checker signs and verifies with the openssl command, which is missing"
CAPSULECTL = Path(os.environ.get("CAPSULECTL") or
                  Path(os.environ.get("LOOP_TOOLS", Path.home() / ".cache/evidence-network-loop")) / "bin/capsulectl")
NO_TOOL = f"SKIPPED LOUDLY: capsulectl not installed at {CAPSULECTL}: run steps/12-join.setup.sh (CI does)"


def needs_capsulectl(test):
    if CAPSULECTL.is_file() and OPENSSL:
        return test
    if os.environ.get("LOOP_REQUIRE_TOOLS") == "1":  # CI, after the setups: a missing tool is an error
        raise RuntimeError(f"LOOP_REQUIRE_TOOLS=1: {NO_TOOL}")
    print(f"\n{NO_TOOL}", file=sys.stderr)
    return unittest.skip(NO_TOOL)(test)


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def answer(stream, responder, receipts=None):
    """A history_card/1 answer in the plugin's evidence-interop/0 shape; receipts per checkpoint, or none."""
    history = [{"checkpoint": "00", "consistency_from_previous": None}, {"checkpoint": "01",
                                                                        "consistency_from_previous": "ab"}]
    for entry, r in zip(history, receipts or []):
        entry["receipts"] = r
    artifact = json.dumps({"derivation": "history_card/1", "evidence_stream": stream, "history": history}).encode()
    return {"profile": cc.STAPLE_PROFILE, "derivation": "history_card/1", "responder": responder,
            "artifact": cc.b64url(artifact), "artifact_digest": hashlib.sha256(artifact).hexdigest(),
            "subject": {"checkpoints": None}, "sig": "unverified"}


def entry(stream, card, community="mesh-llm"):
    raw = json.dumps(card).encode()
    return {"stream": stream, "community": community, "derivation": "history_card/1",
            "sha256": hashlib.sha256(raw).hexdigest(), "bytes": base64.b64encode(raw).decode()}


class TwoBooks(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads((TWO / "commitment.json").read_text())

    def verify(self, doc):
        return cc.verify(doc, cc.file_fetcher(TWO))

    def resigned(self, change):
        doc = copy.deepcopy(self.doc)
        change(doc)
        return cc.sign(doc, KEY)


@unittest.skipUnless(OPENSSL, NO_OPENSSL)
class CheckerTest(TwoBooks):
    """(a) the two-book join verifies; each refusal of a card is named."""

    def assertRefused(self, doc, code):
        errs, _ = self.verify(doc)
        self.assertTrue([e for e in errs if e.startswith(code + ":")], errs)
        self.assertNotIn("signature does not verify under spki", errs)
        return errs

    def test_the_two_book_join_verifies_with_two_stapled_cards(self):
        self.assertEqual(self.doc["profile"], "evidence-commitment/1")
        self.assertNotIn("history_card", self.doc)
        self.assertEqual([c["stream"] for c in self.doc["history_cards"]], ["alice-book", "alice-ledger"])
        self.assertEqual(self.verify(self.doc), ([], []))

    def test_the_cli_passes_it_as_ci_does(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            shutil.copy(TWO / "commitment.json", d / f"{self.doc['node_id']}.json")
            shutil.copy(TWO / "disclosure-policy.json", d / "disclosure-policy.json")
            path = d / f"{self.doc['node_id']}.json"  # a file: the policy beside it is not a commitment
            r = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "check_commitment.py"), str(path)],
                               capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1 commitment ok", r.stdout)

    def test_a_duplicate_stream_is_refused_by_name(self):
        def dup(d):
            d["history_cards"][1] = dict(d["history_cards"][0])
        errs = self.assertRefused(self.resigned(dup), cc.CARD_DUPLICATE)
        self.assertEqual(len([e for e in errs if e.startswith(cc.CARD_DUPLICATE)]), 1)

    def test_bytes_that_do_not_match_are_refused_by_name_on_either_card(self):
        for i in (0, 1):
            with self.subTest(card=i):
                def alter(d, i=i):
                    d["history_cards"][i]["sha256"] = hashlib.sha256(b"other").hexdigest()
                errs = self.assertRefused(self.resigned(alter), cc.CARD_BYTES)
                self.assertIn(f"history_cards[{i}].bytes do not match history_cards[{i}].sha256",
                              " ".join(errs))

    def test_bytes_swapped_between_cards_are_refused(self):
        def swap(d):
            a, b = d["history_cards"]
            a["bytes"], b["bytes"] = b["bytes"], a["bytes"]
        errs = self.assertRefused(self.resigned(swap), cc.CARD_BYTES)
        self.assertEqual(len(errs), 2, errs)

    def test_a_stream_that_is_not_the_cards_own_log_id_is_refused_by_name(self):
        def rename(d):
            d["history_cards"][1]["stream"] = "alice-other"
        errs = self.assertRefused(self.resigned(rename), cc.CARD_STREAM)
        self.assertIn("is not the card's own log id (alice-ledger)", " ".join(errs))

    def test_a_card_presented_by_another_key_is_refused_by_name(self):
        def other(d):
            d["history_cards"][1] = entry("alice-ledger", answer("alice-ledger", "0" * 63 + "1"))
        self.assertRefused(self.resigned(other), cc.CARD_NOT_SUBJECT)

    def test_a_card_that_is_not_an_answer_is_refused_by_name(self):
        def junk(d):
            raw = b'{"profile": "something-else/0"}'
            d["history_cards"][1].update(sha256=hashlib.sha256(raw).hexdigest(), bytes=base64.b64encode(raw).decode())
        self.assertRefused(self.resigned(junk), cc.CARD_NOT_ANSWER)

    def test_a_trailing_newline_is_refused_by_each_pattern(self):
        cases = {
            "stream": (lambda d: d["history_cards"][0].__setitem__("stream", "alice-book\n"), "stream must be a plain log id"),
            "timestamp": (lambda d: d.__setitem__("issued_at", d["issued_at"] + "\n"), "issued_at must be"),
            "duration": (lambda d: d.__setitem__("answers_within", "PT1H\n"), "answers_within must be"),
            "token": (lambda d: d["answers"]["subjects"].__setitem__(0, "history\n"), "answers.subjects must be"),
            "predicate_class": (lambda d: d["lcap"]["predicate_classes"].__setitem__(0, "kind:source\n"),
                                "lcap.predicate_classes must be"),
        }
        for name, (change, msg) in cases.items():
            with self.subTest(pattern=name):
                errs, _ = self.verify(self.resigned(change))
                self.assertIn(msg, " ".join(errs))

    def test_shape_refusals_are_named(self):
        cases = {
            cc.CARD_EMPTY: lambda d: d.__setitem__("history_cards", []),
            cc.CARD_UNSORTED: lambda d: d["history_cards"].reverse(),
            cc.CARD_DERIVATION: lambda d: d["history_cards"][1].__setitem__("derivation", "checkpoints"),
            cc.CARD_SHAPE: lambda d: d["history_cards"][1].pop("stream"),
        }
        for code, change in cases.items():
            with self.subTest(code=code):
                self.assertRefused(self.resigned(change), code)

    def test_a_stream_that_is_not_a_plain_log_id_is_refused_by_name(self):
        for stream in ("../../x", "a/b", " alice-book", "-a", ""):
            with self.subTest(stream=stream):
                errs = self.assertRefused(self.resigned(lambda d: d["history_cards"][0].__setitem__("stream", stream)),
                                          cc.CARD_SHAPE)
                self.assertIn("history_cards[0].stream must be a plain log id", " ".join(errs))

    def test_every_bad_card_is_reported_not_only_the_first(self):
        def two_bad(d):
            d["history_cards"][0]["community"] = "other"
            d["history_cards"][1].pop("derivation")
        errs = self.assertRefused(self.resigned(two_bad), cc.CARD_COMMUNITY)
        self.assertIn("missing history_cards[1].derivation", " ".join(errs))

        def shape_then_content(d):
            d["history_cards"][0].pop("derivation")
            d["history_cards"][1]["community"] = "other"
        errs = self.assertRefused(self.resigned(shape_then_content), cc.CARD_COMMUNITY)
        self.assertIn("missing history_cards[0].derivation", " ".join(errs))

    def test_an_unknown_community_is_refused_by_name(self):
        for community in ("other", "Mesh-LLM", ""):
            with self.subTest(community=community):
                errs = self.assertRefused(
                    self.resigned(lambda d: d["history_cards"][1].__setitem__("community", community)),
                    cc.CARD_COMMUNITY)
                self.assertEqual(len(errs), 1, errs)
        errs, _ = self.verify(self.resigned(lambda d: d["history_cards"][1].pop("community")))
        self.assertIn(f"{cc.CARD_SHAPE}: missing history_cards[1].community", errs)

    def test_both_communities_are_accepted(self):
        for community in hc.COMMUNITIES:
            with self.subTest(community=community):
                doc = self.resigned(lambda d: [c.__setitem__("community", community) for c in d["history_cards"]])
                self.assertEqual(self.verify(doc), ([], []))

    def test_the_checkpoint_key_is_optional_and_must_be_an_ed25519_spki(self):
        self.assertTrue(all("checkpoint_key" in c for c in self.doc["history_cards"]))
        self.assertEqual(self.verify(self.resigned(lambda d: d["history_cards"][1].pop("checkpoint_key"))), ([], []))
        for bad in ("not-a-key", cc.b64url(b"\x00" * 44), self.doc["spki"][:-4]):
            with self.subTest(bad=bad):
                self.assertRefused(self.resigned(lambda d: d["history_cards"][1].__setitem__("checkpoint_key", bad)),
                                   cc.CARD_CHECKPOINT_KEY)
        # The commitment's own spki encoding (standard base64) reads as the same key.
        key = self.doc["history_cards"][1]["checkpoint_key"]
        std = base64.b64encode(cc.unb64url(key)).decode()
        self.assertEqual(cc.card_checkpoint_key({"checkpoint_key": std}), cc.card_checkpoint_key({"checkpoint_key": key}))

    def test_answers_derivations_must_include_history_card(self):
        errs, _ = self.verify(self.resigned(lambda d: d["answers"].__setitem__("derivations", ["checkpoints"])))
        self.assertIn("answers.derivations must include history_card/1, the derivation stapled", errs)

    def test_members_are_the_profiles_own(self):
        errs, _ = self.verify(self.resigned(lambda d: d.__setitem__("history_card", d["history_cards"][0])))
        self.assertIn("unknown field history_card", errs)
        errs, _ = self.verify(self.resigned(lambda d: d.pop("history_cards")))
        self.assertIn("missing history_cards", errs)
        errs, _ = self.verify(self.resigned(lambda d: d.__setitem__("profile", "evidence-commitment/2")))
        self.assertIn("profile must be evidence-commitment/0 or evidence-commitment/1", errs)

    def test_a_controller_binding_digest_is_optional_on_one_too(self):
        errs, _ = self.verify(self.resigned(lambda d: d.__setitem__("controller_binding", {"digest": "a" * 64})))
        self.assertEqual(errs, [])
        errs, _ = self.verify(self.resigned(lambda d: d.__setitem__("controller_binding", {"digest": "npub1x"})))
        self.assertIn("controller_binding.digest is not a sha256 (64 lowercase hex)", errs)
        errs, _ = self.verify(self.resigned(
            lambda d: d.__setitem__("controller_binding", {"digest": "a" * 64, "npub": "npub1x"})))
        self.assertIn("unknown field controller_binding.npub", errs)

    def test_a_card_altered_after_signing_breaks_the_signature(self):
        doc = copy.deepcopy(self.doc)
        doc["history_cards"][1]["stream"] = "alice-other"
        self.assertIn("signature does not verify under spki", self.verify(doc)[0])

    def test_the_count_rule_skips_card_bytes_only(self):
        self.assertEqual(cc.count_errors({"history_cards": [{"bytes": "123"}]}), [])
        self.assertTrue(cc.count_errors({"history_cards": [{"stream": "123"}]}))
        self.assertTrue(cc.count_errors({"history_cards": [{"card_count": "x"}]}))


@unittest.skipUnless(OPENSSL, NO_OPENSSL)
class ZeroStillReads(unittest.TestCase):
    """(b) the committed /0 commitments still verify, and read as one card on their book's stream."""

    def test_committed_zero_commitments_verify_and_read_as_one_card(self):
        self.assertGreaterEqual(len(ZERO), 4)
        for path in ZERO:
            with self.subTest(path=str(path.relative_to(ROOT))):
                doc = json.loads(path.read_text())
                self.assertEqual(doc["profile"], "evidence-commitment/0")
                self.assertEqual(cc.verify(doc, cc.file_fetcher(path.parent)), ([], []))
                book = json.loads((path.parent / "book.json").read_text())["book_id"]
                cards = hc.history_cards(doc)
                self.assertEqual(len(cards), 1)
                self.assertEqual({k: cards[0][k] for k in ("stream", "community", "derivation", "sha256",
                                                           "witnesses", "has_history")},
                                 {"stream": book, "community": "mesh-llm", "derivation": "history_card/1",
                                  "sha256": doc["history_card"]["sha256"], "witnesses": [], "has_history": True})
                self.assertRegex(cards[0]["stream_started"], r"^2026-10-2[78]$")


class HelperTest(unittest.TestCase):
    """history_cards(doc): witnesses come from the card's receipts; [] when unwitnessed."""

    def test_the_two_book_fixture_reads_two_unwitnessed_cards(self):
        doc = json.loads((TWO / "commitment.json").read_text())
        cards = hc.history_cards(doc)
        self.assertEqual([(c["stream"], c["community"], c["witnesses"], c["has_history"]) for c in cards],
                         [("alice-book", "mesh-llm", [], True), ("alice-ledger", "mesh-llm", [], True)])
        self.assertTrue(all(c["stream_started"] for c in cards))
        self.assertEqual([c["sha256"] for c in cards], [c["sha256"] for c in doc["history_cards"]])

    def test_witnesses_are_read_from_the_receipts(self):
        key = "ab" * 32
        witnessed = answer("s-a", key, receipts=[[{"witness": "w-one"}, {"witness": "w-two"}], [{"witness": "w-one"}]])
        doc = {"profile": "evidence-commitment/1",
               "history_cards": [entry("s-a", witnessed), entry("s-b", answer("s-b", key))]}
        self.assertEqual([(c["stream"], c["witnesses"]) for c in hc.history_cards(doc)],
                         [("s-a", ["w-one", "w-two"]), ("s-b", [])])

    def test_a_zero_card_takes_its_stream_from_the_card(self):
        raw = json.dumps(answer("s-a", "ab" * 32, receipts=[[{"witness": "w-one"}]])).encode()
        doc = {"profile": "evidence-commitment/0",
               "history_card": {"derivation": "history_card/1", "sha256": hashlib.sha256(raw).hexdigest(),
                                "bytes": base64.b64encode(raw).decode()}}
        self.assertEqual(hc.history_cards(doc), [{"stream": "s-a", "community": "mesh-llm",
                                                  "derivation": "history_card/1",
                                                  "sha256": hashlib.sha256(raw).hexdigest(),
                                                  "witnesses": ["w-one"], "stream_started": None,
                                                  "has_history": False}])

    def test_undecodable_cards_read_without_stream_or_witnesses(self):
        doc = {"profile": "evidence-commitment/0", "history_card": {"derivation": "history_card/1",
                                                                    "sha256": "x", "bytes": "!!"}}
        self.assertEqual(hc.history_cards(doc)[0]["stream"], None)
        self.assertEqual(hc.history_cards(doc)[0]["witnesses"], [])
        self.assertEqual(hc.history_cards(doc)[0]["stream_started"], None)
        self.assertFalse(hc.history_cards(doc)[0]["has_history"])
        self.assertEqual(hc.history_cards({}), [])


class DeepCbor(unittest.TestCase):
    def test_a_checkpoint_nested_past_the_recursion_limit_reads_as_unreadable(self):
        self.assertIsNone(hc.checkpoint_payload("c0" * 5000 + "00"))
        self.assertIsNone(hc.checkpoint_payload("81" * 5000 + "00"))
        card = answer("s-a", "ab" * 32)
        result = json.loads(cc.unb64url(card["artifact"]))
        result["history"] = [{"checkpoint": "c0" * 5000 + "00", "receipts": []}]
        card["artifact"] = cc.b64url(json.dumps(result).encode())
        [read] = hc.history_cards({"profile": "evidence-commitment/1", "history_cards": [entry("s-a", card)]})
        self.assertEqual((read["stream_started"], read["has_history"]), (None, False))


class Step12Staple(unittest.TestCase):
    """Step 12 reads both profiles: the join's own book is stapled, each card is the stranger's kept answer."""

    def setUp(self):
        self.step = load("step12_join_c1", ROOT / "steps" / "12-join")
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def node(self, src):
        d = self.tmp / "node"
        shutil.copytree(src, d)
        return d

    def test_both_profiles_hold(self):
        for src in (TWO, ROOT / "tests" / "fixtures" / "step12" / "joined" / "alice"):
            with self.subTest(src=src.name):
                shutil.rmtree(self.tmp / "node", ignore_errors=True)
                d = self.node(src)
                self.assertEqual(self.step.check_staple(d, self.step.advertised(d)), [])

    def test_a_card_that_is_not_the_kept_answer_is_a_failure(self):
        d = self.node(TWO)
        kept = d / "probe" / "cards" / "alice-ledger.answer.json"
        kept.write_bytes(kept.read_bytes() + b" ")
        self.assertEqual(self.step.check_staple(d, self.step.advertised(d)),
                         ["the stapled card for stream alice-ledger is not the answer the stranger kept for that book"])

    def resign(self, d, change):
        doc = json.loads((d / "commitment.json").read_text())
        change(doc)
        (d / "commitment.json").write_text(json.dumps(cc.sign(doc, KEY), indent=2, sort_keys=True) + "\n")

    def test_an_own_book_entry_naming_another_checkpoint_key_is_a_failure(self):
        d = self.node(TWO)
        self.resign(d, lambda doc: doc["history_cards"][0].__setitem__(
            "checkpoint_key", doc["history_cards"][1]["checkpoint_key"]))
        self.assertEqual(self.step.check_staple(d, self.step.advertised(d)),
                         ["the card entry for alice-book names a checkpoint key other than the book's checkpoint's"])

    @needs_capsulectl
    def test_step12_verifies_the_second_card_under_the_key_its_entry_names(self):
        d = self.node(TWO)
        errs, not_checked, proof = self.step.judge(d, self.step.tools(), self.tmp / "verify")
        self.assertEqual((errs, not_checked), ([], []))
        self.assertIn("probe/cards/alice-ledger.verified.json", proof)
        verified = json.loads((d / "probe" / "cards" / "alice-ledger.verified.json").read_text())
        self.assertEqual((verified["exit"], verified["result"]["outcome"]), (0, "artifact"))

    @needs_capsulectl
    def test_a_second_card_under_the_wrong_checkpoint_key_is_a_failure(self):
        d = self.node(TWO)
        self.resign(d, lambda doc: doc["history_cards"][1].__setitem__(
            "checkpoint_key", cc.b64url(cc.ED25519_SPKI + bytes.fromhex("ab" * 32))))
        errs, _, _ = self.step.judge(d, self.step.tools(), self.tmp / "verify")
        self.assertEqual(len(errs), 1, errs)
        self.assertIn("the history_card of alice-ledger answer does not verify", errs[0])

    @needs_capsulectl
    def test_a_second_card_without_a_checkpoint_key_is_not_checked(self):
        d = self.node(TWO)
        self.resign(d, lambda doc: doc["history_cards"][1].pop("checkpoint_key"))
        errs, not_checked, proof = self.step.judge(d, self.step.tools(), self.tmp / "verify")
        self.assertEqual(errs, [])
        self.assertEqual([x["what"] for x in not_checked],
                         ["the stapled history_card/1 of book alice-ledger verifies under the node key and that "
                          "book's checkpoint key"])
        self.assertIn("probe/cards/alice-ledger.answer.json", proof)


class RealJoin(unittest.TestCase):
    """(a) end to end: join.sh on a node serving two books staples two cards, and the checker passes it."""

    @needs_capsulectl
    def test_join_sh_staples_one_card_per_book(self):
        make = load("commitment1_make", FIX / "make_fixtures.py")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            make.make(out, Path(tmp) / "home")
            doc = json.loads((out / "commitment.json").read_text())
            self.assertEqual(cc.verify(doc, cc.file_fetcher(out)), ([], []))
        self.assertEqual([c["stream"] for c in hc.history_cards(doc)], ["alice-book", "alice-ledger"])


@unittest.skipUnless(OPENSSL and shutil.which("git"), NO_OPENSSL + " (or git)")
class Step03ReadsOne(unittest.TestCase):
    """Step 03 (live: it never signs) fetches and verifies a /1 commitment, and records each card's stream."""

    def test_a_published_two_book_commitment_passes(self):
        f, node = self.publish(TWO)
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertEqual([c["stream"] for c in node["history_cards"]], ["alice-book", "alice-ledger"])

    def test_one_naming_its_nostr_route_passes_the_same(self):
        self.assertEqual(self.publish(NOSTR)[0]["result"], self.publish(TWO)[0]["result"])

    def publish(self, fixture):
        """Step 03 live over a carrier holding fixture's commitment: (finding, the node's verification)."""
        step = load("step03_advertise_c1", ROOT / "steps" / "03-advertise")
        doc = json.loads((fixture / "commitment.json").read_text())
        node_id = doc["node_id"]
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo, pop = tmp / "carrier", tmp / "population"
            (repo / "nodes" / "commitments").mkdir(parents=True)
            pop.mkdir()
            shutil.copy(fixture / "commitment.json", repo / "nodes" / "commitments" / f"{node_id}.json")
            shutil.copy(fixture / "disclosure-policy.json", repo / "nodes" / "commitments" / "disclosure-policy.json")
            (repo / "nodes" / f"{node_id}.json").write_text(json.dumps({"node_id": node_id, "key_id": doc["key_id"]}))
            subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
            subprocess.run(["git", "-C", str(repo), *step.GIT_ID, "add", "nodes"], check=True)
            subprocess.run(["git", "-C", str(repo), *step.GIT_ID, "commit", "-qm", "list"], check=True)
            (pop / "population.json").write_text(json.dumps(
                {"population": "live", "rail": None, "nodes": [{"name": "alice", "node_id": node_id}]}))
            out = tmp / "findings" / "03-advertise"
            self.assertEqual(step.main(["--population", str(pop), "--out", str(out), "--carrier", str(repo)]), 0)
            f = json.loads((out / "finding.json").read_text())
            node = json.loads((out / "verification.json").read_text())["nodes"][0]
            self.assertEqual(finding.check(out.parent), [])
        return f, node


@unittest.skipUnless(OPENSSL, NO_OPENSSL)
class Cleared(unittest.TestCase):
    """A clear starts a new stream: the fresh card checks clean and reads as no history, with its start date."""

    def test_the_cleared_fixture_checks_clean_and_reads_no_history(self):
        doc = json.loads((CLEARED / "commitment.json").read_text())
        self.assertEqual(cc.verify(doc, cc.file_fetcher(CLEARED)), ([], []))
        [card] = hc.history_cards(doc)
        self.assertEqual((card["stream"], card["community"], card["has_history"]), ("alice-book-2", "mesh-llm", False))
        self.assertRegex(card["stream_started"], r"^\d{4}-\d{2}-\d{2}$")

    def test_no_cleared_flag_anywhere(self):
        for path in [CLEARED / "commitment.json", ROOT / "scripts" / "history_cards.py",
                     ROOT / "scripts" / "check_commitment.py"]:
            with self.subTest(path=path.name):
                text = path.read_text().lower()
                self.assertNotIn('"cleared"', text)
                self.assertNotIn("is_cleared", text)

    def test_a_fresh_stream_and_a_cleared_one_read_the_same(self):
        [cleared] = hc.history_cards(json.loads((CLEARED / "commitment.json").read_text()))
        self.assertEqual(set(cleared), {"stream", "community", "derivation", "sha256", "witnesses",
                                        "stream_started", "has_history"})


@unittest.skipUnless(OPENSSL, NO_OPENSSL)
class NostrRoute(TwoBooks):
    """/1's optional ask.nostr {mailbox_key, relays}: verifies when well formed, each refusal named."""

    def setUp(self):
        self.doc = json.loads((NOSTR / "commitment.json").read_text())

    def assertRefused(self, doc, code):
        errs, _ = self.verify(doc)
        self.assertTrue([e for e in errs if e.startswith(code + ":")], errs)
        self.assertNotIn("signature does not verify under spki", errs)
        return errs

    def change(self, f):
        return self.resigned(lambda d: f(d["ask"]["nostr"]))

    def test_the_fixture_verifies_and_one_without_the_route_still_does(self):
        self.assertEqual(set(self.doc["ask"]["nostr"]), {"mailbox_key", "relays"})
        self.assertEqual(self.verify(self.doc), ([], []))
        self.assertEqual(self.verify(self.resigned(lambda d: d["ask"].pop("nostr"))), ([], []))
        self.assertNotIn("nostr", json.loads((TWO / "commitment.json").read_text())["ask"])

    def test_it_is_signed_over(self):
        doc = copy.deepcopy(self.doc)
        doc["ask"]["nostr"]["relays"].append("wss://attacker.example")
        self.assertIn("signature does not verify under spki", self.verify(doc)[0])

    def test_the_key_and_the_relays_travel_together(self):
        for member in ("mailbox_key", "relays"):
            with self.subTest(missing=member):
                errs = self.assertRefused(self.change(lambda n, m=member: n.pop(m)), cc.NOSTR_MISSING)
                self.assertIn(f"missing ask.nostr.{member}", " ".join(errs))

    def test_a_bad_mailbox_key_is_refused_by_name(self):
        bad = ("F9308A019258C31049344F85F89D5229B531C845836F99B08601F113BCE036F9",  # uppercase
               "f9308a019258c31049344f85f89d5229b531c845836f99b08601f113bce036",  # short
               "02" + "f9308a019258c31049344f85f89d5229b531c845836f99b08601f113bce036f9",  # compressed, 66 hex
               "ff" * 32,  # not below the field prime
               "00" * 31 + "05",  # x^3 + 7 is not a square: no point
               "npub1lycg5qvjtrp3qjf5f7zl382j9x6nrjz9sdhenvyxq8c3808qxmus6unfqa")  # bech32, not hex
        for key in bad:
            with self.subTest(key=key):
                self.assertRefused(self.change(lambda n, k=key: n.__setitem__("mailbox_key", k)), cc.NOSTR_KEY)

    def test_a_malformed_relay_is_refused_by_name(self):
        for url in ("ws://relay.example", "https://relay.example", "WSS://relay.example", "wss://", "relay.example",
                    "wss://user:pw@relay.example", "wss://relay.example/?a=b", "wss://relay.example/#x",
                    "wss://relay.example:99999", "wss://relay .example", "wss://relä.example", ""):
            with self.subTest(url=url):
                errs = self.assertRefused(self.change(lambda n, u=url: n["relays"].__setitem__(1, u)), cc.NOSTR_RELAY)
                self.assertIn("ask.nostr.relays[1]", " ".join(errs))
        self.assertRefused(self.change(lambda n: n["relays"].__setitem__(0, ["wss://relay.example"])), cc.NOSTR_RELAY)

    def test_relays_are_non_empty_and_distinct(self):
        self.assertRefused(self.change(lambda n: n.__setitem__("relays", [])), cc.NOSTR_EMPTY)
        errs = self.assertRefused(self.change(lambda n: n["relays"].__setitem__(1, n["relays"][0])),
                                  cc.NOSTR_DUPLICATE)
        self.assertIn("ask.nostr.relays[1] repeats ask.nostr.relays[0]", " ".join(errs))

    def test_a_relay_with_a_path_or_port_is_accepted(self):
        for url in ("wss://relay.example:7447", "wss://relay.example/nostr", "wss://[2001:db8::1]"):
            with self.subTest(url=url):
                self.assertEqual(self.verify(self.change(lambda n, u=url: n["relays"].__setitem__(1, u))), ([], []))

    def test_unknown_members_are_still_refused(self):
        errs = self.assertRefused(self.change(lambda n: n.__setitem__("announce_key", n["mailbox_key"])),
                                  cc.NOSTR_SHAPE)
        self.assertIn("unknown field ask.nostr.announce_key", " ".join(errs))
        errs, _ = self.verify(self.resigned(lambda d: d["ask"].__setitem__("relays", ["wss://relay.example"])))
        self.assertIn("unknown field ask.relays", errs)
        errs, _ = self.verify(self.resigned(lambda d: d.__setitem__("nostr", d["ask"].pop("nostr"))))
        self.assertIn("unknown field nostr", errs)

    def test_a_route_that_is_not_an_object_is_refused(self):
        errs, _ = self.verify(self.resigned(lambda d: d["ask"].__setitem__("nostr", "wss://relay.example")))
        self.assertIn("ask.nostr must be a dict", errs)
        errs = self.assertRefused(self.change(lambda n: n.__setitem__("relays", "wss://relay.example")), cc.NOSTR_SHAPE)
        self.assertIn("ask.nostr.relays must be a list", " ".join(errs))

    def test_profile_zero_has_no_route(self):
        path = ZERO[-1]
        doc = json.loads(path.read_text())
        doc["ask"]["nostr"] = dict(json.loads((NOSTR / "commitment.json").read_text())["ask"]["nostr"])
        doc = cc.sign(doc, KEY)
        self.assertIn("unknown field ask.nostr", cc.verify(doc, cc.file_fetcher(path.parent))[0])

    def test_the_route_carries_no_count(self):
        self.assertEqual(cc.count_errors(self.doc), [])


class Bindings(unittest.TestCase):
    """binding-verdict/0: only a verified verdict from a pinned verifier groups; revoked and not_checked never do."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        [self.verdict_path] = (FIX / "bound" / "bindings").glob("*.json")
        self.verdict = json.loads(self.verdict_path.read_text())
        self.lock = FIX / "bound" / "versions.lock"
        self.commitments = [json.loads((FIX / "bound" / d / "commitment.json").read_text()) for d in ("mesh", "muse")]

    def write(self, change, lock=None):
        """A bindings dir holding the bound verdict, changed; returns (dir, lock)."""
        d = self.tmp / "bindings"
        d.mkdir(exist_ok=True)
        v = copy.deepcopy(self.verdict)
        change(v)
        (d / self.verdict_path.name).write_text(json.dumps(v))
        return d, lock or self.lock

    def errors(self, change, lock=None):
        d, lock = self.write(change, lock)
        return cb.check_file(d / self.verdict_path.name, cb.read_lock(lock))[1]

    def test_the_bound_fixture_groups_its_mesh_and_muse_subjects(self):
        mesh = json.loads((FIX / "bound" / "mesh" / "commitment.json").read_text())
        muse = json.loads((FIX / "bound" / "muse" / "commitment.json").read_text())
        self.assertEqual([c["community"] for c in hc.history_cards(mesh)], ["mesh-llm"])
        self.assertEqual([c["community"] for c in hc.history_cards(muse)], ["muse"])
        if OPENSSL:
            for doc, d in ((mesh, "mesh"), (muse, "muse")):
                self.assertEqual(cc.verify(doc, cc.file_fetcher(FIX / "bound" / d)), ([], []))
        self.assertEqual(hc.bound_groups(FIX / "bound" / "bindings", self.lock, self.commitments), [
            {"controller_key_id": self.verdict["controller_key_id"],
             "subjects": sorted([mesh["node_id"], muse["node_id"]]), "verifier": self.verdict["verifier"]}])

    def test_the_verdict_is_named_by_the_binding_digest_both_commitments_carry(self):
        record = json.loads((FIX / "bound" / "binding.json").read_text())
        digest = hashlib.sha256(cc.canonical({k: v for k, v in record.items() if k != "sigs"})).hexdigest()
        self.assertEqual(self.verdict["controller_key_id"], digest)
        self.assertEqual(self.verdict_path.name, f"{digest}.json")
        self.assertEqual([c["controller_binding"] for c in self.commitments], [{"digest": digest}] * 2)
        self.assertEqual(sorted(record["keys"]), self.verdict["subjects"])

    def test_a_verified_verdict_groups_only_subjects_whose_commitments_carry_its_digest(self):
        mesh, muse = self.commitments
        other = dict(muse, controller_binding={"digest": "ab" * 32})
        bare = {k: v for k, v in muse.items() if k != "controller_binding"}
        for commitments in ([mesh, other], [mesh, bare], [mesh], []):
            with self.subTest(n=len(commitments)):
                self.assertEqual(hc.bound_groups(FIX / "bound" / "bindings", self.lock, commitments), [])

    def test_only_the_binding_verifier_is_a_verifier_whatever_else_the_lock_pins(self):
        name, repo, pin = ("reporter", "evidence-network/loop", "4d00c42c9856fc892f7309ee9b434dca1bb69202")
        lock = self.tmp / "versions.lock"
        lock.write_text(f"{name} {repo} {pin}\n")
        errs = self.errors(lambda v: v.__setitem__("verifier", f"{name}@{pin}"), lock)
        self.assertEqual([e.split(":")[0] for e in errs], [cb.UNPINNED])
        d, _ = self.write(lambda v: v.__setitem__("verifier", f"{name}@{pin}"))
        self.assertEqual(hc.bound_groups(d, lock, self.commitments), [])

    def test_the_revoked_fixture_does_not_group(self):
        [path] = (FIX / "revoked" / "bindings").glob("*.json")
        self.assertEqual(cb.check_file(path, cb.read_lock(FIX / "revoked" / "versions.lock"))[1], [])
        self.assertEqual(json.loads(path.read_text())["status"], "revoked")
        revoked = [json.loads((FIX / "revoked" / d / "commitment.json").read_text()) for d in ("mesh", "muse")]
        self.assertEqual(hc.bound_groups(FIX / "revoked" / "bindings", FIX / "revoked" / "versions.lock", revoked), [])

    def test_revoked_and_not_checked_never_group_even_from_a_pinned_verifier(self):
        for status, cites in (("revoked", None), ("not_checked", "action-state-group/agent-action-capsule#457")):
            with self.subTest(status=status):
                d, lock = self.write(lambda v: v.update(status=status, cites=cites))
                self.assertEqual(cb.check_file(d / self.verdict_path.name, cb.read_lock(lock))[1], [])
                self.assertEqual(hc.bound_groups(d, lock, self.commitments), [])

    def test_verified_with_no_pinned_verifier_is_refused_on_main(self):
        lock = ROOT / "versions.lock"  # this repository's: it pins no binding verifier today
        errs = self.errors(lambda v: None, lock)
        self.assertEqual([e.split(":")[0] for e in errs], [cb.UNPINNED])
        self.assertEqual(hc.bound_groups(FIX / "bound" / "bindings", lock, self.commitments), [])
        r = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "check_binding.py"),
                            str(FIX / "bound" / "bindings")], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 1)
        self.assertIn(cb.UNPINNED, r.stderr)

    def test_verified_needs_the_exact_pin(self):
        name, pin = self.verdict["verifier"].split("@")
        for verifier in (None, f"{name}@{'0' * 40}", f"other@{pin}"):
            with self.subTest(verifier=verifier):
                self.assertIn(cb.UNPINNED, " ".join(self.errors(lambda v: v.__setitem__("verifier", verifier))))

    def test_not_checked_cites_its_open_issue(self):
        errs = self.errors(lambda v: v.update(status="not_checked", verifier=None, cites=None))
        self.assertEqual([e.split(":")[0] for e in errs], [cb.NO_CITE])

    def test_shape_refusals(self):
        s1, s2 = self.verdict["subjects"]
        cases = [lambda v: v.__setitem__("subjects", [s2, s1]), lambda v: v.__setitem__("subjects", [s1]),
                 lambda v: v.__setitem__("subjects", [s1, s1]), lambda v: v.__setitem__("status", "bound"),
                 lambda v: v.__setitem__("checked_at", "2026-10-28"), lambda v: v.__setitem__("cites", "issue 432"),
                 lambda v: v.__setitem__("profile", "binding-verdict/1"), lambda v: v.pop("cites"),
                 lambda v: v.__setitem__("extra", "x"), lambda v: v.__setitem__("controller_key_id", "AB" * 32)]
        for i, change in enumerate(cases):
            with self.subTest(case=i):
                errs = self.errors(change)
                self.assertTrue(errs and all(e.startswith((cb.SHAPE, cb.FILE_NAME)) for e in errs), errs)

    def test_the_file_is_named_by_its_controller(self):
        d, lock = self.write(lambda v: None)
        moved = d / ("ab" * 32 + ".json")
        (d / self.verdict_path.name).rename(moved)
        self.assertEqual([e.split(":")[0] for e in cb.check_file(moved, cb.read_lock(lock))[1]], [cb.FILE_NAME])

    def test_this_repository_has_no_bindings_to_refuse(self):
        r = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "check_binding.py"), "bindings"],
                           capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
