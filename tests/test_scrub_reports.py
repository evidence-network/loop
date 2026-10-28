"""The scrubbed set: raw reports in, one public file per day out, and no answer bytes in it.

The raw days are made by the reporter itself against the fake console in
test_reporter.py, under a fake clock. Run: python3 -B -m unittest discover -s tests -v
"""
import base64
import contextlib
import datetime as dt
import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import daily_report  # noqa: E402
import scrub_reports  # noqa: E402
import test_reporter  # noqa: E402
from test_reporter import FakeConsole, entry  # noqa: E402

REPORTER = {"node_id": "e" * 64, "key_id": "f" * 64, "retry_after_s": [3600]}
# Node ids unlike any stand-in value in the reply fixtures (runs of 2, 5, a, d).
N1, N2, N3 = (hashlib.sha256(f"synthetic node {n}".encode()).hexdigest() for n in (1, 2, 3))
K1, K2, K3 = (hashlib.sha256(f"synthetic key {n}".encode()).hexdigest() for n in (1, 2, 3))
CHECKPOINT = {"v": 1, "kind": "checkpoint", "log_id": "c" * 64, "mmr_size": 41, "root": "b" * 64,
              "prev_size": 19, "prev_root": "9" * 64, "key_id": "8" * 64, "timestamp": "2026-10-19T23:55:00Z",
              "signature": "7" * 128}
# The bytes the scrubbed set must never hold, beyond each raw file's own.
RAW_FILES = (".response.json", ".request.json", ".absence.txt", ".verification", "peer-key.json")
WINDOW = 32


def verified_with_checkpoints():
    """A verified checkpoints reply whose artifact carries two checkpoints (stand-in values)."""
    reply = json.loads((test_reporter.FIXTURES / "verified.json").read_text())
    artifact = {"checkpoints": [dict(CHECKPOINT, mmr_size=19, timestamp="2026-10-19T12:00:00Z"), CHECKPOINT]}
    reply["answer"]["artifact"] = base64.urlsafe_b64encode(json.dumps(artifact, separators=(",", ":")).encode()).decode().rstrip("=")
    return json.dumps(reply, separators=(",", ":")).encode()


class Clock:
    def __init__(self, at):
        self.at = at
        self.on_sleep = None

    def now(self):
        self.at += dt.timedelta(seconds=1)
        return self.at

    def sleep(self, s):
        self.at += dt.timedelta(seconds=s)
        if self.on_sleep:
            self.on_sleep()


def make_raw(base):
    """Four days of raw reports under base/reports/daily, and a leave mark for N2.

    10-20: N1 verified, N2 refused, N3 absent after a retry.
    10-21: N1 verified on the retry, N2 no checkpoint yet, N3 absent again.
    10-22: missed, the reporter could not reach its own node.
    10-23: N3 absent in the run, then answers when asked again.
    """
    fixtures = base / "fixtures"
    shutil.copytree(test_reporter.FIXTURES, fixtures)
    (fixtures / "verified_checkpoints.json").write_bytes(verified_with_checkpoints())
    nodes, out, left = base / "nodes", base / "reports" / "daily", base / "left"
    nodes.mkdir()
    left.mkdir()
    for nid, kid in ((N1, K1), (N2, K2), (N3, K3)):
        (nodes / f"{nid}.json").write_text(json.dumps(entry(nid, kid)))
    (left / f"{N2}.json").write_text(json.dumps({"node_id": N2, "left": "2026-10-24"}))
    reporter = base / "reporter.json"
    reporter.write_text(json.dumps(REPORTER))
    both = lambda what: {"checkpoints": what, "history_card": what}  # noqa: E731
    behaviour = {N1: {"checkpoints": "verified_checkpoints", "history_card": "verified_history_card"},
                 N2: both("refusal"), N3: both("absence")}
    saved, test_reporter.FIXTURES = test_reporter.FIXTURES, fixtures
    console = FakeConsole(behaviour, {N1: K1, N2: K2, N3: K3}, signing=REPORTER["key_id"])
    try:
        def call(cmd, date, clock, url=console.url, *extra):
            argv = ["daily_report.py", cmd, *extra, "--date", date, "--console", url, "--nodes", str(nodes),
                    "--out", str(out), "--reporter", str(reporter), "--timeout", "2"]
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                return daily_report.main(argv, now=clock.now, sleep=clock.sleep)

        clock = Clock(dt.datetime(2026, 10, 20, 0, 30, tzinfo=dt.timezone.utc))
        assert call("run", "2026-10-20", clock) == 0

        clock = Clock(dt.datetime(2026, 10, 21, 0, 30, tzinfo=dt.timezone.utc))
        behaviour[N1] = both("absence")
        behaviour[N2] = both("no_checkpoint_yet")
        clock.on_sleep = lambda: behaviour.update({N1: {"checkpoints": "verified_checkpoints",
                                                        "history_card": "verified_history_card"}})
        assert call("run", "2026-10-21", clock) == 0

        clock = Clock(dt.datetime(2026, 10, 22, 0, 30, tzinfo=dt.timezone.utc))
        assert call("run", "2026-10-22", clock, "http://127.0.0.1:9") == 2

        clock = Clock(dt.datetime(2026, 10, 23, 0, 30, tzinfo=dt.timezone.utc))
        behaviour[N2] = both("refusal")
        assert call("run", "2026-10-23", clock) == 0
        behaviour[N3] = {"checkpoints": "verified_checkpoints", "history_card": "verified_history_card"}
        clock.at = dt.datetime(2026, 10, 23, 12, 0, tzinfo=dt.timezone.utc)
        assert call("ask", "2026-10-23", clock, console.url, "--absent") == 0
    finally:
        test_reporter.FIXTURES = saved
        console.close()
    return out, left


def raw_secrets(reports):
    """Every byte string from the raw store that must not reach the scrubbed set."""
    found = []
    for p in sorted(reports.rglob("*")):
        if p.is_file() and p.name.endswith(RAW_FILES):
            body = p.read_bytes()
            found.append((p, body))
            if p.name.endswith(".response.json"):  # and the artifact's own bytes, decoded
                try:
                    answer = json.loads(body).get("answer") or {}
                    blob = answer.get("artifact") or (answer.get("envelope") or {}).get("artifact")
                    found.append((p, base64.urlsafe_b64decode(blob + "=" * (-len(blob) % 4))))
                except (ValueError, TypeError, AttributeError):
                    pass
    return found


def leaks(reports, out_bytes):
    """(file, what) for each raw file whose bytes, or any 32-byte window of them, show up in out_bytes."""
    hits = []
    for p, body in raw_secrets(reports):
        if len(body) >= 8 and body.strip() in out_bytes:
            hits.append((p, "whole file"))
            continue
        for i in range(0, max(len(body) - WINDOW + 1, 0)):
            if body[i:i + WINDOW] in out_bytes:
                hits.append((p, body[i:i + WINDOW]))
                break
    return hits


class ScrubTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.tmp.name)
        cls.reports, cls.left = make_raw(cls.base)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.out = Path(tempfile.mkdtemp(dir=self.base))

    def scrub(self, out=None):
        return scrub_reports.scrub(self.reports, out or self.out, self.left)

    def day(self, date):
        return json.loads((self.out / f"{date}.json").read_text())

    def node(self, date, nid):
        return next(n for n in self.day(date)["nodes"] if n["node_id"] == nid)

    def test_one_file_per_day_in_the_agreed_format(self):
        self.assertEqual(self.scrub(), [f"2026-10-2{d}.json" for d in range(4)])
        self.assertEqual(scrub_reports.check(self.out), [])
        day = self.day("2026-10-20")
        self.assertEqual(set(day), scrub_reports.DAY_KEYS)
        self.assertEqual((day["run"], day["missed_reason"], day["listed"]), ("settled", None, 3))
        self.assertEqual(day["reporter_key_id"], REPORTER["key_id"])
        self.assertEqual(day["versions_lock_sha256"], daily_report.versions_lock_sha256())
        self.assertEqual([n["node_id"] for n in day["nodes"]], sorted((N1, N2, N3)))
        n1 = self.node("2026-10-20", N1)
        self.assertEqual(n1["node_line"], "discloses: checkpoints, history_card/1")
        self.assertEqual(n1["questions"]["checkpoints"]["log_size_at_checkpoint"],
                         {"mmr_size": 41, "timestamp": "2026-10-19T23:55:00Z"})
        self.assertIsNone(n1["questions"]["history_card"]["log_size_at_checkpoint"])  # halves, no checkpoints
        settled = self.reports / "2026-10-20" / N1 / "checkpoints.response.json"
        self.assertEqual(n1["questions"]["checkpoints"]["answer_sha256"], hashlib.sha256(settled.read_bytes()).hexdigest())
        n2 = self.node("2026-10-20", N2)
        self.assertEqual(n2["questions"]["history_card"]["state"], "refusal")
        self.assertIsNone(n2["questions"]["history_card"]["log_size_at_checkpoint"])
        self.assertEqual(n2["left_on"], "2026-10-24")
        self.assertIsNone(n1["left_on"])
        n3 = self.node("2026-10-20", N3)
        self.assertEqual(n3["questions"]["checkpoints"]["state"], "absence")
        self.assertEqual([a["state"] for a in n3["questions"]["checkpoints"]["attempts"]], ["unreachable"] * 2)
        self.assertIsNone(n3["questions"]["checkpoints"]["answer_sha256"])
        self.assertEqual(n3["absent_days_running"], 1)

    def test_a_retry_an_absence_a_missed_day_and_an_ask_again(self):
        self.scrub()
        retried = self.node("2026-10-21", N1)["questions"]["checkpoints"]
        self.assertEqual([a["state"] for a in retried["attempts"]], ["unreachable", "verified"])
        self.assertEqual(retried["state"], "verified")
        self.assertEqual(self.node("2026-10-21", N2)["node_line"], "joined, no checkpoint yet")
        self.assertEqual(self.node("2026-10-21", N3)["absent_days_running"], 2)
        missed = self.day("2026-10-22")
        self.assertEqual(missed, {"date": "2026-10-22", "run": "missed",
                                  "missed_reason": "the reporter could not reach its own node",
                                  "versions_lock_sha256": None, "reporter_key_id": None, "listed": 0, "nodes": []})
        asked = self.node("2026-10-23", N3)
        self.assertEqual(asked["node_line"], "joined, discloses nothing")  # NODE as the run settled it
        self.assertEqual(asked["questions"]["checkpoints"]["state"], "verified")  # the later ask in it
        self.assertEqual([a["at"] for a in asked["questions"]["checkpoints"]["attempts"]][-1], "2026-10-23T12:00:01Z")
        self.assertEqual(asked["absent_days_running"], 0)

    def test_no_answer_request_peer_key_or_verification_bytes_reach_the_scrubbed_set(self):
        self.scrub()
        out_bytes = b"".join(p.read_bytes() for p in sorted(self.out.iterdir()))
        secrets = raw_secrets(self.reports)
        self.assertGreater(len([p for p, _ in secrets if p.name.endswith(".response.json")]), 10)
        self.assertEqual(leaks(self.reports, out_bytes), [])
        # The probe finds a leak when there is one.
        planted = out_bytes + secrets[0][1][5:5 + WINDOW]
        self.assertIn(secrets[0][0], [p for p, _ in leaks(self.reports, planted)])

    def test_the_same_input_gives_the_same_bytes(self):
        self.scrub()
        other = Path(tempfile.mkdtemp(dir=self.base))
        self.scrub(other)
        names = sorted(p.name for p in self.out.iterdir())
        self.assertEqual(names, sorted(p.name for p in other.iterdir()))
        for name in names:
            self.assertEqual((self.out / name).read_bytes(), (other / name).read_bytes())
        before = {n: (self.out / n).stat().st_mtime_ns for n in names}
        (self.out / "2026-09-30.json").write_text("{}\n")  # a day no longer in the raw store
        (self.out / ".gitkeep").write_text("")
        self.scrub()
        self.assertEqual(sorted(p.name for p in self.out.iterdir()), sorted(names + [".gitkeep"]))
        self.assertEqual({n: (self.out / n).stat().st_mtime_ns for n in names}, before)  # untouched
        text = (self.out / "2026-10-20.json").read_text()
        self.assertTrue(text.endswith("}\n") and text.startswith('{\n  "date"'))

    def test_a_day_still_under_way_is_refused(self):
        reports = Path(tempfile.mkdtemp(dir=self.base))
        shutil.copytree(self.reports / "2026-10-20", reports / "2026-10-20")
        (reports / "2026-10-20" / "RUN.json").unlink()
        with self.assertRaisesRegex(ValueError, "neither RUN.json nor MISSED"):
            scrub_reports.scrub(reports, self.out, self.left)
        self.assertEqual(list(self.out.iterdir()), [])

    def test_the_cli(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(scrub_reports.main(["scrub_reports.py", "scrub", str(self.reports), str(self.out),
                                                 "--left", str(self.left)]), 0)
            self.assertEqual(scrub_reports.main(["scrub_reports.py", "check", str(self.out)]), 0)
            (self.out / "2026-10-20.json").write_text("{}\n")
            self.assertEqual(scrub_reports.main(["scrub_reports.py", "check", str(self.out)]), 1)


class CheckTest(unittest.TestCase):
    """check refuses a file holding anything beyond the format."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        self.good = {
            "date": "2026-10-20", "run": "settled", "missed_reason": None,
            "versions_lock_sha256": "1" * 64, "reporter_key_id": "f" * 64, "listed": 1,
            "nodes": [{"node_id": N1, "node_line": "discloses: checkpoints", "absent_days_running": 0,
                       "left_on": None, "questions": {
                           "checkpoints": {"state": "verified", "attempts": [{"at": "2026-10-20T00:30:01Z", "state": "verified"}],
                                           "log_size_at_checkpoint": {"mmr_size": 41, "timestamp": "2026-10-19T23:55:00Z"},
                                           "answer_sha256": "3" * 64},
                           "history_card": {"state": "absence", "attempts": [{"at": "2026-10-20T00:30:01Z", "state": "timeout"}],
                                            "log_size_at_checkpoint": None, "answer_sha256": None}}}]}

    def tearDown(self):
        self.tmp.cleanup()

    def errs(self, day, name="2026-10-20.json", text=None):
        (self.out / name).write_text(text if text is not None else scrub_reports.dump(day))
        errs = scrub_reports.check(self.out)
        (self.out / name).unlink()
        return errs

    def mutate(self, fn):
        day = json.loads(json.dumps(self.good))
        fn(day)
        return self.errs(day)

    def test_the_good_day_is_clean(self):
        self.assertEqual(self.errs(self.good), [])

    def test_unknown_keys_are_refused(self):
        self.assertIn("unknown key(s) score", "\n".join(self.mutate(lambda d: d.update(score=1))))
        self.assertIn("unknown key(s) answer", "\n".join(self.mutate(lambda d: d["nodes"][0].update(answer="x"))))
        self.assertIn("unknown key(s) body",
                      "\n".join(self.mutate(lambda d: d["nodes"][0]["questions"]["checkpoints"].update(body="x"))))
        self.assertIn("unknown key(s) why",
                      "\n".join(self.mutate(lambda d: d["nodes"][0]["questions"]["checkpoints"]["attempts"][0].update(why="x"))))
        self.assertTrue(self.mutate(lambda d: d["nodes"][0]["questions"].update(records={})))

    def test_base64_blobs_are_refused(self):
        blob = base64.b64encode(b'{"checkpoints": [{"mmr_size": 41}]}').decode()
        self.assertIn("base64-looking", "\n".join(self.mutate(lambda d: d.update(
            run="missed", nodes=[], listed=0, versions_lock_sha256=None, reporter_key_id=None,
            missed_reason=f"could not finish {blob}"))))
        # node_line takes only the reporter's own lines, so a blob there is refused too.
        self.assertTrue(self.mutate(lambda d: d["nodes"][0].update(node_line=blob)))
        # A hex field holds 64 hex, not a blob.
        self.assertTrue(self.mutate(lambda d: d["nodes"][0]["questions"]["checkpoints"].update(answer_sha256=blob)))

    def test_long_strings_are_refused(self):
        long = "the reporter did not run " * 9
        errs = self.mutate(lambda d: d.update(run="missed", nodes=[], listed=0, versions_lock_sha256=None,
                                              reporter_key_id=None, missed_reason=long))
        self.assertIn("longer than 200", "\n".join(errs))

    def test_states_dates_counts_and_order(self):
        self.assertTrue(self.mutate(lambda d: d["nodes"][0]["questions"]["checkpoints"].update(state="checked")))
        self.assertTrue(self.mutate(lambda d: d["nodes"][0]["questions"]["checkpoints"]["attempts"][0].update(state="answered")))
        self.assertTrue(self.mutate(lambda d: d["nodes"][0].update(absent_days_running=-1)))
        self.assertTrue(self.mutate(lambda d: d["nodes"][0].update(left_on="soon")))
        self.assertTrue(self.mutate(lambda d: d.update(listed=2)))
        self.assertTrue(self.mutate(lambda d: d.update(run="partial")))
        self.assertTrue(self.mutate(lambda d: d["nodes"].append(dict(d["nodes"][0]))) )
        self.assertIn("not the file's", "\n".join(self.errs(self.good, name="2026-10-21.json")))

    def test_only_the_canonical_bytes(self):
        text = json.dumps(self.good, sort_keys=True)
        self.assertIn("canonical", "\n".join(self.errs(None, text=text)))
        self.assertTrue(self.errs(None, name="notes.txt", text="hello\n"))


if __name__ == "__main__":
    unittest.main()
