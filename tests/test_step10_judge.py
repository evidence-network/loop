"""steps/10-judge: a deterministic pack over a window of whole closed months, its Results sealed and verified offline.

The base run is tests/fixtures/step10/pass-run: four stand-in step findings (05, 06, 07, 09) dated in September
2026 and step 08's manifest showing each reproduced, with 08 finished in October 2026. Sealing and verifying need
the pinned capsulectl and capsulectl-book (steps/10-judge.setup.sh); without them those tests SKIP, saying so,
and LOOP_REQUIRE_TOOLS=1 makes that an error. Each planted failure runs on a copy of the base run, and its mutant
check shows which part of 10 catches it: with that part of 10 switched off, the plant goes through.
"""
import datetime as dt
import importlib.machinery
import importlib.util
import json
import os
import shutil
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
STEPS = ROOT / "steps"
POPULATION = ROOT / "population" / "rehearsal"
BASE = ROOT / "tests" / "fixtures" / "step10" / "pass-run"

sys.path.insert(0, str(STEPS))
import finding  # noqa: E402

_loader = importlib.machinery.SourceFileLoader("step10_judge", str(STEPS / "10-judge"))
_spec = importlib.util.spec_from_loader("step10_judge", _loader)
step = importlib.util.module_from_spec(_spec)
_loader.exec_module(step)

HAS_TOOLS = all(p.is_file() for p in step.tools())
NO_TOOLS = "SKIPPED LOUDLY: capsulectl and capsulectl-book are not installed; run steps/10-judge.setup.sh"
if not HAS_TOOLS:
    if os.environ.get("LOOP_REQUIRE_TOOLS") == "1":
        raise RuntimeError(f"LOOP_REQUIRE_TOOLS=1: {NO_TOOLS}")
    print(f"\ntest_step10_judge: {NO_TOOLS}", file=sys.stderr)

WINDOW_STEPS = ("05-deal", "06-settle", "07-ask", "09-dispute")


def load(path):
    return json.loads(Path(path).read_text())


def dump(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")


def utc(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="test-step10-")
        self.run = Path(self.tmp.name) / "run"
        shutil.copytree(BASE, self.run)

    def tearDown(self):
        self.tmp.cleanup()

    def judge(self):
        """Run 10 in this process on the run; its finding."""
        out = self.run / "10-judge"
        step.main(["--population", str(POPULATION), "--out", str(out)])
        f = load(out / "finding.json")
        self.assertEqual(finding.check_record(out, f), [], f["detail"])
        return f

    def manifest(self):
        return load(self.run / "08-verify" / "verified.json")

    def reseal_manifest(self, m):
        """Write 08's manifest and its finding's proof digest again, as 08 would have."""
        v = self.run / "08-verify"
        dump(v / "verified.json", m)
        f = load(v / "finding.json")
        f["proof"] = [{"path": "verified.json", "sha256": finding.sha256(v / "verified.json")}]
        dump(v / "finding.json", f)

    def edit_step(self, base, **fields):
        """Change a step's finding and give 08's manifest its new digest."""
        f = load(self.run / base / "finding.json")
        f.update(fields)
        dump(self.run / base / "finding.json", f)
        m = self.manifest()
        for e in m["steps"]:
            if e["dir"] == base:
                e["files"][0]["sha256"] = finding.sha256(self.run / base / "finding.json")
        self.reseal_manifest(m)

    def window_records(self):
        """{dir/path: sha256} of every file 08 lists for the base run's four steps."""
        return {f"{e['dir']}/{x['path']}": x["sha256"] for e in self.manifest()["steps"] for x in e["files"]}

    def cited(self):
        """{dir/path: sha256} the finer bundles cite, read from their disclosed record capsules."""
        out = {}
        for path in (self.run / "10-judge" / "bundles").glob("*.bundle.json"):
            if path.name == "rollup.bundle.json":
                continue
            b = load(path)
            statement = b["disclosures"][b["root"]]["agent_input"]["statement"]
            for c in statement["claims"]:
                for e in c["evidence"]:
                    p = b["disclosures"][e["digest"]]["agent_input"]
                    out[f"{p['step']}/{p['path']}"] = p["sha256"]
        return out


class WindowTest(unittest.TestCase):
    def test_twelve_whole_closed_months_before_the_as_of_month(self):
        self.assertEqual(step.window_of(utc("2026-10-02T09:01:00Z")),
                         (utc("2025-10-01T00:00:00Z"), utc("2026-10-01T00:00:00Z")))
        self.assertEqual(step.window_of(utc("2027-01-31T23:59:59Z")),
                         (utc("2026-01-01T00:00:00Z"), utc("2027-01-01T00:00:00Z")))

    def test_never_rolling(self):
        # Any instant in one month gives the same window: it moves only when a month closes.
        first = step.window_of(utc("2026-10-01T00:00:00Z"))
        self.assertEqual(first, step.window_of(utc("2026-10-31T23:59:59Z")))
        self.assertNotEqual(first, step.window_of(utc("2026-11-01T00:00:00Z")))


class ManifestTest(Base):
    """What 10 reads before it seals anything; none of this needs capsulectl."""

    def test_no_manifest_is_not_checked(self):
        shutil.rmtree(self.run / "08-verify")
        f = self.judge()
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertIn("08-verify/verified.json", f["not_checked"][0]["why"])

    def test_a_manifest_edited_after_08_sealed_it_fails(self):
        m = self.manifest()
        m["steps"][0]["rederived"]["result"] = "FAIL"
        dump(self.run / "08-verify" / "verified.json", m)
        f = self.judge()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("does not match its sha256", f["detail"])

    def test_a_record_changed_after_08_fails(self):
        (self.run / "05-deal" / "deal.json").write_text('{"deal": "another"}\n')
        f = self.judge()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("05-deal/deal.json does not match the sha256 08's manifest gives it", f["detail"])

    def test_a_manifest_of_another_population_fails(self):
        m = self.manifest()
        m["population"] = "live"
        self.reseal_manifest(m)
        self.assertEqual(self.judge()["result"], "FAIL")

    def test_todays_records_are_outside_the_window(self):
        # 08 ran in the month the records were made: no record is in a closed month yet.
        f = load(self.run / "08-verify" / "finding.json")
        f["finished"] = "2026-09-30T23:00:00Z"
        dump(self.run / "08-verify" / "finding.json", f)
        f = self.judge()
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertEqual(json.loads((self.run / "10-judge" / "window.json").read_text())["records"], [])
        for base in WINDOW_STEPS:
            self.assertIn(f"{base}: ", f["detail"])
        self.assertIn("outside the window", f["detail"])
        self.assertFalse((self.run / "10-judge" / "bundles").exists())


@unittest.skipUnless(HAS_TOOLS, NO_TOOLS)
class JudgeTest(Base):
    def test_passes_offline_citing_every_record_in_the_window(self):
        def no_network(*a, **k):
            raise OSError("network disabled by the test")
        with mock.patch.object(socket, "socket", side_effect=no_network), \
                mock.patch.object(socket, "create_connection", side_effect=no_network), \
                mock.patch.object(socket, "getaddrinfo", side_effect=no_network):
            f = self.judge()
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertEqual(f["not_checked"], [])
        self.assertEqual(sorted(f["pinned"]), ["capsulectl", "capsulectl-book"])
        self.assertEqual(self.cited(), self.window_records())
        out = self.run / "10-judge"
        names = sorted(p.name for p in (out / "bundles").iterdir())
        self.assertEqual(names, [f"{b}.bundle.json" for b in WINDOW_STEPS] + ["rollup.bundle.json"])
        for p in (out / "verify").iterdir():
            self.assertEqual(load(p)["verdict"], "VALID", p.name)
        # The roll-up cites exactly the four finer Results.
        rollup = load(out / "bundles" / "rollup.bundle.json")
        cites = {e["digest"] for c in rollup["disclosures"][rollup["root"]]["agent_input"]["statement"]["claims"]
                 for e in c["evidence"]}
        self.assertEqual(cites, {load(out / "bundles" / f"{b}.bundle.json")["root"] for b in WINDOW_STEPS})
        self.assertEqual(finding.gaps(f["detail"])[0][1], step.PACK_ISSUE)

    def test_term_3_reads_provider_states_without_model_attestation(self):
        self.judge()
        b = load(self.run / "10-judge" / "bundles" / "05-deal.bundle.json")
        claims = {c["requirement_ref"]: c for c in b["disclosures"][b["root"]]["agent_input"]["statement"]["claims"]}
        self.assertEqual(claims["term-2"]["verdict"], "met")
        self.assertEqual(claims["term-3"]["verdict"], "not_evaluable")
        self.assertEqual(claims["term-3"]["grade"], "self-attested")
        self.assertIn("provider states", claims["term-3"]["presentation"]["narrative"])

    def test_term_3_with_model_attestation_is_not_checked(self):
        d = self.run / "05-deal" / "deal.json"
        dump(d, {**load(d), "model_attestation": {"model": "stand-in"}})
        m = self.manifest()
        for e in m["steps"]:
            for x in e["files"]:
                if e["dir"] == "05-deal" and x["path"] == "deal.json":
                    x["sha256"] = finding.sha256(d)
        self.reseal_manifest(m)
        f = self.judge()
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertEqual([x["issue"] for x in f["not_checked"]], [step.PACK_ISSUE])
        self.assertIn("term-3", f["not_checked"][0]["what"])

    def test_a_verdict_comes_from_the_result_08_rederived(self):
        m = self.manifest()
        next(e for e in m["steps"] if e["dir"] == "06-settle")["rederived"]["result"] = "FAIL"
        self.reseal_manifest(m)
        f = self.judge()
        self.assertEqual(f["result"], "PASS", f["detail"])  # a not_met deal is a judged outcome, not a broken judge
        self.assertIn("06-settle: 1 claim(s), 3 records cited, term-1 not_met", f["detail"])
        rollup = load(self.run / "10-judge" / "bundles" / "rollup.bundle.json")
        claims = rollup["disclosures"][rollup["root"]]["agent_input"]["statement"]["claims"]
        self.assertEqual({c["id"]: c["verdict"] for c in claims}["window:06-settle"], "not_met")

    def test_a_step_08_did_not_reproduce_is_outside_the_window(self):
        d = self.run / "12-join"
        d.mkdir()
        (d / "finding.json").write_text("{}\n")
        m = self.manifest()
        m["steps"].append({"step": "12", "name": "join", "dir": "12-join", "status": "not_reproduced",
                           "files": [{"path": "finding.json", "sha256": finding.sha256(d / "finding.json")}],
                           "recorded": {"result": "PASS", "not_checked": []}, "rederived": None, "problems": [],
                           "not_checked": {"what": "step 12", "why": "not reproduced", "issue": step.ISSUE}})
        self.reseal_manifest(m)
        f = self.judge()
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertEqual(len(f["not_checked"]), 1)
        self.assertIn("step 12", f["not_checked"][0]["what"])
        self.assertIn("12-join: 1 files, not_reproduced by 08", f["detail"])
        # The records that are in the window are still sealed, cited and VALID.
        self.assertEqual(self.cited(), {k: v for k, v in self.window_records().items()
                                        if not k.startswith("12-join/")})
        for p in (self.run / "10-judge" / "verify").iterdir():
            self.assertEqual(load(p)["verdict"], "VALID", p.name)

    def test_a_step_outside_the_window_is_refused_not_read(self):
        self.edit_step("07-ask", finished="2026-10-01T00:00:00Z")
        with mock.patch.object(step, "attested", wraps=step.attested) as read:
            f = self.judge()
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertIn("07-ask: 3 files, finished 2026-10-01T00:00:00Z, outside the window", f["detail"])
        self.assertFalse(any(k.startswith("07-ask/") for k in self.cited()))
        self.assertFalse(any("07-ask" in str(c.args[0]) for c in read.call_args_list))
        self.assertFalse((self.run / "10-judge" / "bundles" / "07-ask.bundle.json").exists())

    def test_a_file_not_in_the_manifest_is_refused_not_read(self):
        (self.run / "06-settle" / "extra.json").write_text('{"not": "listed"}\n')
        f = self.judge()
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertIn("06-settle/extra.json: not in 08's manifest", f["detail"])
        self.assertNotIn("06-settle/extra.json", self.cited())

    # --- Planted failures, each with its mutant ---------------------------------------------------
    def omit_one(self):
        real = step.judge_records

        def omit(*a, **k):
            docs = real(*a, **k)
            for c in docs["06-settle"]["claims"]:
                c["evidence"] = [e for e in c["evidence"] if e != "06-settle/ledger.jsonl"]
            return docs
        return mock.patch.object(step, "judge_records", side_effect=omit)

    def test_planted_a_result_that_omits_one_record_fails(self):
        with self.omit_one():
            f = self.judge()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("no Result cites 06-settle/ledger.jsonl", f["detail"])

    def test_mutant_without_the_complete_check_the_omission_passes(self):
        with self.omit_one(), mock.patch.object(step, "check_complete", return_value=[]):
            self.assertEqual(self.judge()["result"], "PASS")

    def cite_outside(self):
        # 07 finished outside the window; the planted pack cites its finding.json anyway.
        self.edit_step("07-ask", finished="2026-10-01T00:00:00Z")
        outside = {"dir": "07-ask", "step": "07", "path": "finding.json", "kind": "ask", "result": "PASS",
                   "sha256": finding.sha256(self.run / "07-ask" / "finding.json"), "finished": "2026-10-01T00:00:00Z"}
        real_judge, real_seal = step.judge_records, step.seal

        def extra(*a, **k):
            docs = real_judge(*a, **k)
            docs["06-settle"]["claims"][0]["evidence"].append("07-ask/finding.json")
            return docs

        def seal(book, records, *a, **k):
            return real_seal(book, records + [outside], *a, **k)
        return mock.patch.multiple(step, judge_records=mock.DEFAULT, seal=mock.DEFAULT), extra, seal

    def test_planted_a_result_that_cites_a_record_outside_the_window_fails(self):
        patch, extra, seal = self.cite_outside()
        with patch as m:
            m["judge_records"].side_effect, m["seal"].side_effect = extra, seal
            f = self.judge()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("a Result cites 07-ask/finding.json", f["detail"])
        self.assertIn("outside the window", f["detail"])

    def test_mutant_without_the_exact_check_the_outside_record_passes(self):
        patch, extra, seal = self.cite_outside()
        with patch as m, mock.patch.object(step, "check_exact", return_value=[]):
            m["judge_records"].side_effect, m["seal"].side_effect = extra, seal
            self.assertEqual(self.judge()["result"], "PASS")

    def stray_result(self):
        """The roll-up also cites a Result sealed in the book that no VALID bundle is rooted on."""
        real = step.Book.result

        def result(book, doc, name):
            if name == "rollup":
                stray = json.loads(json.dumps(doc["claims"][0]))
                twin = step.result_doc([{**stray, "id": "stray", "evidence": stray["evidence"]}],
                                       step.load(step.PACK), *step.window_of(utc("2026-10-02T09:01:00Z")))
                twin["generated_at"] = "2026-10-02T00:00:00Z"
                rid = real(book, twin, "stray")
                doc["claims"].append({**doc["claims"][0], "id": "window:stray",
                                      "evidence": [{"digest_alg": "SHA-256", "digest": rid}]})
                doc["claims"][-1]["presentation"] = {**doc["claims"][0]["presentation"],
                                                     "evidence": doc["claims"][-1]["evidence"]}
                doc["aggregate"]["coverage"]["evaluated_population"] += 1
                doc["aggregate"]["buckets"][doc["claims"][-1]["verdict"]].append("window:stray")
            return real(book, doc, name)
        return mock.patch.object(step.Book, "result", result)

    def test_planted_a_rollup_that_cites_a_result_that_does_not_verify_fails(self):
        with self.stray_result():
            f = self.judge()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("a Result that does not verify", f["detail"])
        self.assertNotIn("no Result cites", f["detail"])

    def test_planted_a_rollup_over_a_finer_result_altered_after_sealing_fails(self):
        real = step.seal

        def tamper(book, records, docs, pack, start, end, out):
            sealed = real(book, records, docs, pack, start, end, out)
            p = out / "bundles" / "09-dispute.bundle.json"
            b = load(p)
            b["disclosures"][b["root"]]["agent_input"]["statement"]["claims"][0]["verdict"] = "not_met"
            dump(p, b)
            return sealed
        with mock.patch.object(step, "seal", side_effect=tamper):
            f = self.judge()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("09-dispute.bundle.json does not verify", f["detail"])
        self.assertIn("the roll-up cites", f["detail"])
        self.assertIn("a Result that does not verify", f["detail"])

    def test_mutant_without_the_rollup_check_the_stray_result_passes(self):
        with self.stray_result(), mock.patch.object(step, "check_rollup", return_value=[]):
            self.assertEqual(self.judge()["result"], "PASS")

    def alter_rollup(self):
        real = step.seal

        def tamper(book, records, docs, pack, start, end, out):
            sealed = real(book, records, docs, pack, start, end, out)
            p = out / "bundles" / "rollup.bundle.json"
            b = load(p)
            b["disclosures"][b["root"]]["agent_input"]["statement"]["claims"][0]["verdict"] = "not_met"
            dump(p, b)
            return sealed
        return mock.patch.object(step, "seal", side_effect=tamper)

    def test_planted_a_result_altered_after_sealing_fails(self):
        with self.alter_rollup():
            f = self.judge()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("rollup.bundle.json does not verify: INVALID", f["detail"])
        self.assertIn("disclosure_mismatch", f["detail"])

    def test_mutant_without_the_verifier_the_altered_result_passes(self):
        with self.alter_rollup(), mock.patch.object(step, "verify_bundle", return_value={"verdict": "VALID"}):
            self.assertEqual(self.judge()["result"], "PASS")

    def test_a_bundle_under_another_key_fails(self):
        # The Results are sealed under the judge key; the judge is told the repository's key is alice's.
        real_judge = step.judge
        alice = step.pub_hex(POPULATION / "keys" / "alice" / "node-key.pub.pem")
        with mock.patch.object(step, "judge", side_effect=lambda out, records, cap, key, tmp:
                               real_judge(out, records, cap, alice, tmp)):
            f = self.judge()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("is not under the repository's judge key", f["detail"])


if __name__ == "__main__":
    unittest.main()
