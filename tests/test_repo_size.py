"""Tests for the repository size measurement (scripts/repo_size.py).

Run: python3 -m unittest discover -s tests -v
"""
import base64
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_reports  # noqa: E402
import repo_size  # noqa: E402


def b64(text):
    return base64.urlsafe_b64encode(text.encode()).rstrip(b"=").decode()


def unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)).decode()


@unittest.skipUnless(shutil.which("git"), "git is needed to measure history")
class MeasureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.repo = self.dir / "repo"
        self.repo.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def measure(self, bodies=None, **kw):
        return repo_size.measure(bodies or repo_size.Bodies(), self.repo, **kw)

    def node_dir(self, day, i, where="daily"):
        return self.repo / "reports" / where / day / repo_size.node_id(i)

    def test_a_day_is_the_reporters_own_output_and_is_counted_whole(self):
        measured = self.measure(nodes=2, days=1, start=3)
        daily = self.repo / "reports" / "daily"
        self.assertEqual(check_reports.check(daily)[0], [])
        self.assertEqual(measured[1]["tree"], sum(p.stat().st_size for p in daily.rglob("*") if p.is_file()))
        for i in range(2):
            d = self.node_dir("2027-01-01", i)
            self.assertEqual((d / "NODE").read_text(), "discloses: checkpoints, history_card/1\n")
            reply = json.loads((d / "checkpoints.response.json").read_bytes())
            self.assertEqual(reply["verification"]["checkpoints"], 3)
            self.assertEqual(len(json.loads(unb64(reply["answer"]["artifact"]))["checkpoints"]), 3)
        self.assertGreater(measured[1]["pack"], 0)

    def test_nodes_share_no_bytes(self):
        self.measure(nodes=2, days=1, start=3)
        a, b = (self.node_dir("2027-01-01", i) / "history_card.response.json" for i in range(2))
        self.assertNotEqual(a.read_bytes(), b.read_bytes())

    def test_an_answer_over_the_cap_is_a_small_signed_refusal(self):
        over = repo_size.MAX_ANSWER_BYTES // (2 * repo_size.SYNTHETIC_STATEMENT) + 1
        measured = self.measure(nodes=1, days=1, start=over)
        d = self.node_dir("2027-01-01", 0)
        line = (d / "checkpoints.verification").read_text()
        self.assertTrue(line.startswith("refusal reason=policy_declined "), line)
        self.assertLess(measured[1]["tree"], 8 * 1024)

    def test_a_settled_answer_is_in_the_tree_twice_and_in_git_once(self):
        measured = self.measure(nodes=1, days=1, start=4)[1]
        d = self.node_dir("2027-01-01", 0)
        (attempt,) = (d / "attempts").iterdir()
        self.assertEqual(attempt.name, "20270101T060000Z")
        settled = (d / "checkpoints.response.json").read_bytes()
        self.assertEqual((attempt / "checkpoints.response.json").read_bytes(), settled)
        answers = sum(len((p / f"{s}.response.json").read_bytes()) for p in (d, attempt) for s in repo_size.STEMS)
        self.assertGreater(answers, measured["tree"] * 0.8)
        self.assertLess(measured["pack_nodelta"], answers / 2)

    def test_a_retry_adds_only_the_failed_attempts_absence_lines(self):
        one = self.measure(nodes=1, days=1, start=4)[1]["tree"]
        shutil.rmtree(self.repo)
        self.repo.mkdir()
        three = self.measure(nodes=1, days=1, start=4, attempts=3)[1]["tree"]
        daily = self.repo / "reports" / "daily"
        self.assertEqual(check_reports.check(daily)[0], [])
        d = self.node_dir("2027-01-01", 0)
        attempts = sorted((d / "attempts").iterdir())
        self.assertEqual([a.name for a in attempts], ["20270101T060000Z", "20270101T061000Z", "20270101T062000Z"])
        for a in attempts[:2]:
            self.assertEqual(sorted(p.name for p in a.glob("*.response.json")), [])
            self.assertTrue((a / "checkpoints.verification").read_text().startswith("unreachable "))
        self.assertEqual(len(list(attempts[2].glob("*.response.json"))), 2)
        self.assertTrue((d / "history_card.verification").read_text().startswith("verified "))
        self.assertLess(three - one, 4 * 1024)

    def test_a_list_that_only_grows_costs_history_little(self):
        m = self.measure(nodes=1, days=4, start=8, per_day=8)
        grew = {k: m[4][k] - m[3][k] for k in m[4]}
        self.assertLess(grew["pack"], grew["pack_nodelta"] / 2)
        self.assertLess(grew["pack_nodelta"], grew["tree"])


class BodiesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        for n in (2, 4):
            statements = [f"{k:02x}" * 40 for k in range(n)]
            artifact = json.dumps({"anchor": "a" * 64, "checkpoints": statements, "subject": {"checkpoints": None}})
            wire = {"anchor": {"digest": "a" * 64, "freshness": n}, "artifact": b64(artifact),
                    "issued_at": "2026-09-29T00:00:00Z", "request_digest": "d" * 64, "sig": "5" * 128}
            (self.dir / f"{n}.checkpoints.json").write_text(json.dumps(wire))
            (self.dir / f"{n}.history_card.json").write_text(json.dumps(wire))
        (self.dir / "9.checkpoints.json").write_text(json.dumps(
            {"issued_at": "2026-09-29T00:00:00Z", "reason": "policy_declined", "request_digest": "d" * 64,
             "key_id": "8" * 64, "sig": "5" * 128}))
        self.bodies = repo_size.Bodies(self.dir)

    def tearDown(self):
        self.tmp.cleanup()

    def reply(self, i, n, stem="checkpoints"):
        return json.loads(self.bodies.reply(i, stem, n, "2027-01-01", 1))

    def test_the_largest_answer_at_or_below_the_count_is_used(self):
        self.assertEqual(self.reply(0, 3)["verification"]["checkpoints"], 2)
        self.assertEqual(self.reply(0, 8)["verification"]["checkpoints"], 4)
        self.assertEqual(self.reply(0, 1)["verification"]["checkpoints"], 2)
        self.assertEqual(self.reply(0, 9)["verification"], {"reason": "policy_declined", "state": "refusal"})

    def test_each_node_gets_bytes_of_the_same_size_and_shape(self):
        zero, one = self.reply(0, 4), self.reply(1, 4)
        cps = lambda r: json.loads(unb64(r["answer"]["artifact"]))["checkpoints"]  # noqa: E731
        self.assertEqual(cps(zero), [f"{k:02x}" * 40 for k in range(4)])
        self.assertNotEqual(cps(one), cps(zero))
        self.assertEqual([len(c) for c in cps(one)], [len(c) for c in cps(zero)])
        self.assertEqual(len(json.dumps(one)), len(json.dumps(zero)))
        self.assertEqual(json.loads(unb64(one["answer"]["artifact"]))["subject"], {"checkpoints": None})

    def test_a_dir_without_answers_is_refused(self):
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaises(ValueError):
                repo_size.Bodies(empty)


class ProjectTest(unittest.TestCase):
    def test_a_year_is_the_measured_days_plus_the_last_days_growth(self):
        measured = {9: {"tree": 900, "pack": 90}, 10: {"tree": 1000, "pack": 95}}
        year = repo_size.project(measured, 10, nodes=2, target=10)
        self.assertEqual(year, {"tree": (1000 + 355 * 100) * 5, "pack": (95 + 355 * 5) * 5})

    def test_one_measured_day_repeats(self):
        self.assertEqual(repo_size.project({1: {"tree": 7}}, 1, nodes=1, target=100), {"tree": 7 * 365 * 100})


if __name__ == "__main__":
    unittest.main()
