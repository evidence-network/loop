"""The growth report (site/growth.py): stapled history cards from the committed commitments, week by week.

  (a) each week is judged against the commitments as committed at the end of that week, not today's files;
  (b) a week's volume equals what the cards say, summed independently of the report;
  (c) a failing or missing commitment is a node without a card, with its reason, and stays in the denominator;
  (d) network totals and a community split only: no per-node volume in the page or in data.json;
  (e) no score, rank or rating, "deals" appears once (to say what the count is not), every count names what it is out of;
  (f) no script, no external URL; same inputs give the same bytes; the build writes only under growth/;
  (g) on the real repository, the committed commitments all verify.

Fixtures: tests/fixtures/commitment1/two-books/ (a signed evidence-commitment/1 with two cards, TEST key),
committed into a scratch git repository with fixed dates.
Run: python3 -B -m unittest tests.test_growth -v
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "site"))
import growth  # noqa: E402
import history_cards as hc  # noqa: E402

SIGNED = ROOT / "tests" / "fixtures" / "commitment1" / "two-books"
OPENSSL = shutil.which("openssl")
NODE_A = json.loads((SIGNED / "commitment.json").read_text())["node_id"]
NODE_B = "b" * 64


def git(repo, *args, when=None):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.com")
    if when:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def commit(repo, message, when):
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-gpg-sign", "-m", message, when=when)


@unittest.skipUnless(OPENSSL, "SKIPPED LOUDLY: the checker verifies signatures with the openssl command, which is missing")
class FixtureRepo(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        repo = Path(cls.tmp.name) / "repo"
        (repo / "nodes" / "commitments").mkdir(parents=True)
        git(repo, "init", "-q", "-b", "main")
        listing = repo / "nodes" / f"{NODE_A}.json"
        listing.write_text("{}\n")
        commit(repo, "list A", "2026-10-27T10:00:00Z")
        doc = json.loads((SIGNED / "commitment.json").read_text())
        cdir = repo / "nodes" / "commitments"
        (cdir / f"{NODE_A}.json").write_text(json.dumps(doc))
        shutil.copy(SIGNED / "disclosure-policy.json", cdir / "disclosure-policy.json")
        commit(repo, "A publishes", "2026-10-30T10:00:00Z")
        doc["issued_at"] = "2026-11-03T00:00:00Z"  # edited after signing: the signature no longer holds
        (cdir / f"{NODE_A}.json").write_text(json.dumps(doc))
        (repo / "nodes" / f"{NODE_B}.json").write_text("{}\n")
        commit(repo, "B listed, A edited", "2026-11-03T10:00:00Z")
        (repo / "unrelated.txt").write_text("x\n")
        commit(repo, "head", "2026-11-04T12:00:00Z")
        cls.repo = repo
        cls.data = growth.export(growth.build_model(repo, "HEAD"))
        cls.out = Path(cls.tmp.name) / "out"
        growth.main(["--repo", str(repo), "--out", str(cls.out)])
        cls.page = (cls.out / "growth" / "index.html").read_text()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_each_week_is_judged_at_its_own_end_not_against_todays_files(self):
        first, second = self.data["weeks"]
        self.assertEqual((first["week"], first["partial"]), ("2026-10-26", False))
        self.assertEqual((first["to"], second["week"], second["to"], second["partial"]),
                         ("2026-11-01", "2026-11-02", "2026-11-04", True))  # a full week reads Monday to Sunday
        self.assertEqual((first["listed"], first["verifying"]), (1, 1))
        self.assertEqual((second["listed"], second["verifying"]), (2, 0))  # A's edited file fails; B has none

    def test_a_weeks_volume_is_what_the_cards_say(self):
        doc = json.loads((SIGNED / "commitment.json").read_text())
        sizes = [c["log_size"] - c["prev_size"] for e in hc.entries(doc)
                 for c in hc.card_checkpoints(hc.card_answer(e))]
        mesh = self.data["weeks"][0]["communities"]["mesh-llm"]
        self.assertEqual((mesh["cards"], mesh["checkpoints"], mesh["records"], mesh["with_receipt"]),
                         (2, len(sizes), sum(sizes), 0))
        self.assertEqual(self.data["weeks"][1]["communities"], {})

    def test_a_failing_or_missing_commitment_has_no_card_and_a_reason(self):
        by_node = {c["node_id"]: c for c in self.data["cards"]}
        self.assertFalse(by_node[NODE_A]["verifies"])
        self.assertIn("signature", by_node[NODE_A]["reason"])
        self.assertEqual(by_node[NODE_B]["reason"], "no commitment published")
        self.assertIn("0 of 2 listed nodes have a stapled card that verifies", self.page)

    def test_no_per_node_volume(self):
        blob = json.dumps(self.data)
        self.assertNotIn("records", json.dumps(self.data["cards"]))
        self.assertNotIn("checkpoints\"", json.dumps(self.data["cards"]))
        self.assertNotIn("records_sealed", blob)
        for row in re.findall(r"<tr><th scope=\"row\"><code>.*?</tr>", self.page):
            self.assertNotRegex(row, r"\d+ from ")

    def test_wording(self):
        text = re.sub(r"<[^>]+>", " ", self.page).lower()
        for word in ("score", "rank", "rating"):
            self.assertNotRegex(text, rf"\b{word}\b")
        self.assertEqual(text.count("deals"), 1)
        self.assertIn("not deals", text)
        self.assertIn("log entries covered by a signed checkpoint", text)
        self.assertNotRegex(self.page, r"<script|https?://|src=|<img")
        for cell in re.findall(r"<td class=\"num\">(.*?)</td>", self.page):
            self.assertRegex(cell, r"\d+ (of|from) ")

    def test_first_checkpoint_is_stated_unless_witnessed(self):
        out = growth.render({"weeks": [], "cards": [
            {"node_id": "a" * 64, "verifies": True, "stream": "s", "community": "muse",
             "first_checkpoint": "2026-10-28", "first_checkpoint_receipt": True},
            {"node_id": "b" * 64, "verifies": True, "stream": "t", "community": "muse",
             "first_checkpoint": "2026-10-29", "first_checkpoint_receipt": False}]})
        self.assertIn("2026-10-28 (carries a witness receipt, not verified)", out)
        self.assertIn("2026-10-29 (stated, not proven)", out)

    def test_same_inputs_same_bytes_and_only_growth_is_written(self):
        again = Path(self.tmp.name) / "again"
        growth.main(["--repo", str(self.repo), "--out", str(again)])
        for name in ("index.html", "data.json"):
            self.assertEqual((again / "growth" / name).read_bytes(), (self.out / "growth" / name).read_bytes())
        self.assertEqual([p.name for p in self.out.iterdir()], ["growth"])


class Synthetic(unittest.TestCase):
    """week_numbers and the policy fetcher on hand-made input: boundaries, malformed counts, receipts, late checkpoints."""

    def node(self, checkpoints, ok=True):
        return {"listed": ["n"], "nodes": {"n": {"ok": ok, "reason": "", "cards": [
            {"stream": "s", "community": "muse", "checkpoints": checkpoints}]}}}

    def ck(self, issued, prev, size, carries=False):
        return ({"issued_at": issued, "prev_size": prev, "log_size": size}, carries)

    def week(self, judgeds, last=False, start="2026-10-26", end="2026-11-02"):
        t = growth.parse_time
        return growth.week_numbers(judgeds, t(start + "T00:00:00Z"), t(end + "T00:00:00Z"), last)["communities"].get("muse", {"cards": 0, "checkpoints": 0, "records": 0, "with_receipt": 0})

    def test_boundaries_malformed_counts_and_receipts(self):
        judged = self.node([
            self.ck("2026-10-26T00:00:00Z", 0, 10, True),      # the start belongs to this week
            self.ck("2026-11-02T00:00:00Z", 10, 20),           # the end belongs to the next
            self.ck("2026-10-27T00:00:00Z", 30, 20),           # size < prev: unreadable, not counted
            self.ck("2026-10-28T00:00:00Z", None, 20),         # no prev_size
            self.ck("2026-10-29T00:00:00", 0, 5),              # no zone: the machine's, refused
            self.ck("2026-10-30T12:00:00.5+00:00", 10, 13)])   # an explicit offset reads as UTC
        c = self.week([judged])
        self.assertEqual((c["checkpoints"], c["records"], c["with_receipt"]), (2, 13, 1))

    def test_the_cut_short_last_week_includes_its_end_instant(self):
        judged = self.node([self.ck("2026-11-02T00:00:00Z", 0, 4)])
        self.assertEqual(self.week([judged], last=True)["checkpoints"], 1)
        self.assertEqual(self.week([judged], last=False)["checkpoints"], 0)

    def test_a_checkpoint_published_late_counts_once_in_its_own_week(self):
        week_end = self.node([])
        later = self.node([self.ck("2026-10-31T23:00:00Z", 0, 7)])
        c = self.week([week_end, later, later])
        self.assertEqual((c["cards"], c["checkpoints"], c["records"]), (1, 1, 7))  # the card is the week-end tree's; the checkpoint is counted once

    def test_a_failing_tree_adds_nothing(self):
        failing = self.node([self.ck("2026-10-31T23:00:00Z", 0, 7)], ok=False)
        self.assertEqual(growth.week_numbers([failing], growth.parse_time("2026-10-26T00:00:00Z"),
                                             growth.parse_time("2026-11-02T00:00:00Z"), False)["communities"], {})

    def test_policy_is_read_inside_the_tree_only(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d) / "tree" / "commitments"
            base.mkdir(parents=True)
            (base / "p.json").write_bytes(b"policy")
            (Path(d) / "tree" / "outside.json").write_bytes(b"secret")
            fetch = growth.tree_fetcher(base)
            self.assertEqual(fetch("p.json"), b"policy")
            self.assertIsNone(fetch("../outside.json"))
            self.assertIsNone(fetch("file:///etc/hosts"))
            self.assertIsNone(fetch("https://example.com/p.json"))
            self.assertIsNone(fetch("/etc/hosts"))
            self.assertEqual(fetch("missing.json"), b"")  # not there: the digest check then fails it


@unittest.skipUnless(OPENSSL, "SKIPPED LOUDLY: the checker verifies signatures with the openssl command, which is missing")
class History(unittest.TestCase):
    def repo(self, tmp):
        repo = Path(tmp) / "repo"
        (repo / "nodes" / "commitments").mkdir(parents=True)
        git(repo, "init", "-q", "-b", "main")
        (repo / "nodes" / f"{NODE_A}.json").write_text("{}\n")
        commit(repo, "list A", "2026-10-27T10:00:00Z")
        return repo

    def publish(self, repo):
        cdir = repo / "nodes" / "commitments"
        shutil.copy(SIGNED / "commitment.json", cdir / f"{NODE_A}.json")
        shutil.copy(SIGNED / "disclosure-policy.json", cdir / "disclosure-policy.json")

    def test_checkpoints_cut_in_a_week_but_committed_the_next_still_count_in_their_week(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp)
            self.publish(repo)
            commit(repo, "A publishes late", "2026-11-03T10:00:00Z")  # its checkpoints are from 2026-10-29
            git(repo, "commit", "-q", "--allow-empty", "--no-gpg-sign", "-m", "head", when="2026-11-04T12:00:00Z")
            first, second = growth.export(growth.build_model(repo, "HEAD"))["weeks"]
            self.assertEqual((first["listed"], first["verifying"]), (1, 0))  # nothing published by the end of week 1
            self.assertEqual(first["communities"]["mesh-llm"]["checkpoints"], 2)  # but they were cut in it
            self.assertEqual((first["volume_nodes"], first["communities"]["mesh-llm"]["cards"]), (1, 2))
            page = growth.render(growth.export(growth.build_model(repo, "HEAD")))
            self.assertIn("0 of 1 listed nodes", page)  # the week-end tree had no verifying card
            self.assertIn("8 from 1 of 1 listed nodes", page)  # the volume names who contributed it
            self.assertEqual(second["communities"], {})

    def test_a_week_is_judged_along_first_parent_history_not_an_unmerged_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self.repo(tmp)
            git(repo, "checkout", "-q", "-b", "side")
            self.publish(repo)
            commit(repo, "published on a branch", "2026-11-01T10:00:00Z")  # Sunday, merged only on Tuesday
            git(repo, "checkout", "-q", "main")
            git(repo, "merge", "-q", "--no-ff", "--no-gpg-sign", "-m", "merge", "side", when="2026-11-03T10:00:00Z")
            git(repo, "commit", "-q", "--allow-empty", "--no-gpg-sign", "-m", "head", when="2026-11-04T12:00:00Z")
            first = growth.export(growth.build_model(repo, "HEAD"))["weeks"][0]
            self.assertEqual(first["verifying"], 0)  # on main, week 1 had no commitment yet


class Policy(unittest.TestCase):
    @unittest.skipUnless(OPENSSL, "openssl missing")
    def test_a_commitment_whose_policy_was_not_read_does_not_verify(self):
        with tempfile.TemporaryDirectory() as d:
            cdir = Path(d) / "nodes" / "commitments"
            cdir.mkdir(parents=True)
            shutil.copy(SIGNED / "commitment.json", cdir / f"{NODE_A}.json")  # its policy file is absent
            self.assertIn("policy", growth.check_commitment_in(cdir / f"{NODE_A}.json"))
            shutil.copy(SIGNED / "disclosure-policy.json", cdir / "disclosure-policy.json")
            self.assertEqual(growth.check_commitment_in(cdir / f"{NODE_A}.json"), "")


@unittest.skipUnless(OPENSSL, "SKIPPED LOUDLY: openssl missing")
class RealRepository(unittest.TestCase):
    def test_the_committed_commitments_verify_and_stats_is_not_read(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(growth.main(["--repo", str(ROOT), "--out", d]), 0)
            data = json.loads((Path(d) / "growth" / "data.json").read_text())
        last = data["weeks"][-1]
        listed = len(list((ROOT / "nodes").glob("*.json")))
        self.assertEqual(last["listed"], listed)
        self.assertEqual(last["verifying"], last["listed"])  # every committed commitment verifies
        source = (ROOT / "site" / "growth.py").read_text()
        self.assertNotRegex(source, r"stats/data|findings|status_page|stats_model")


if __name__ == "__main__":
    unittest.main()
