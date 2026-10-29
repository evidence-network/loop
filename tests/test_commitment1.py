"""evidence-commitment/1: one stapled history card per book; /0 still reads, as one card.

Fixtures: tests/fixtures/commitment1/two-books/ (made by its make_fixtures.py with join.sh and
the pinned capsulectl), signed by alice's TEST key from tests/fixtures/step12/population.
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
KEY = ROOT / "tests" / "fixtures" / "step12" / "population" / "keys" / "alice" / "node-key.pem"
ZERO = sorted((ROOT / "lab" / "runs" / "2026-10-28" / "join").glob("*/commitment.json")) + \
    [ROOT / "tests" / "fixtures" / "step12" / "joined" / "alice" / "commitment.json"]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "steps"))
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


def entry(stream, card):
    raw = json.dumps(card).encode()
    return {"stream": stream, "derivation": "history_card/1", "sha256": hashlib.sha256(raw).hexdigest(),
            "bytes": base64.b64encode(raw).decode()}


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
                self.assertEqual(hc.history_cards(doc), [{"stream": book, "derivation": "history_card/1",
                                                          "sha256": doc["history_card"]["sha256"],
                                                          "witnesses": []}])


class HelperTest(unittest.TestCase):
    """history_cards(doc): witnesses come from the card's receipts; [] when unwitnessed."""

    def test_the_two_book_fixture_reads_two_unwitnessed_cards(self):
        doc = json.loads((TWO / "commitment.json").read_text())
        cards = hc.history_cards(doc)
        self.assertEqual([(c["stream"], c["witnesses"]) for c in cards], [("alice-book", []), ("alice-ledger", [])])
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
        self.assertEqual(hc.history_cards(doc), [{"stream": "s-a", "derivation": "history_card/1",
                                                  "sha256": hashlib.sha256(raw).hexdigest(),
                                                  "witnesses": ["w-one"]}])

    def test_undecodable_cards_read_without_stream_or_witnesses(self):
        doc = {"profile": "evidence-commitment/0", "history_card": {"derivation": "history_card/1",
                                                                    "sha256": "x", "bytes": "!!"}}
        self.assertEqual(hc.history_cards(doc)[0]["stream"], None)
        self.assertEqual(hc.history_cards(doc)[0]["witnesses"], [])
        self.assertEqual(hc.history_cards({}), [])


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

    @needs_capsulectl
    def test_step12_judges_the_two_book_join_and_names_the_second_cards_gap(self):
        d = self.node(TWO)
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
        step = load("step03_advertise_c1", ROOT / "steps" / "03-advertise")
        doc = json.loads((TWO / "commitment.json").read_text())
        node_id = doc["node_id"]
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo, pop = tmp / "carrier", tmp / "population"
            (repo / "nodes" / "commitments").mkdir(parents=True)
            pop.mkdir()
            shutil.copy(TWO / "commitment.json", repo / "nodes" / "commitments" / f"{node_id}.json")
            shutil.copy(TWO / "disclosure-policy.json", repo / "nodes" / "commitments" / "disclosure-policy.json")
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
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertEqual([c["stream"] for c in node["history_cards"]], ["alice-book", "alice-ledger"])


if __name__ == "__main__":
    unittest.main()
