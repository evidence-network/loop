"""steps/08-verify: every finding of a run reproduced offline from its bundle, or said not to be.

The base run is steps 03, 06 and 09 on the rehearsal population (python3, git and openssl only); without them
the tests SKIP, saying so, and LOOP_REQUIRE_TOOLS=1 makes that an error. Each planted failure edits a copy of
the base run, and its mutant check shows which part of 08 catches it: with that part of 08 switched off, the
plant goes through.
"""
import importlib.machinery
import importlib.util
import datetime as dt
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
STEPS = ROOT / "steps"
POPULATION = ROOT / "population" / "rehearsal"
FIXTURES = ROOT / "tests" / "fixtures" / "step08"
TOOLS_DIR = Path(os.environ.get("LOOP_TOOLS") or Path.home() / ".cache" / "evidence-network-loop")

sys.path.insert(0, str(STEPS))
import finding  # noqa: E402

_loader = importlib.machinery.SourceFileLoader("step08_verify", str(STEPS / "08-verify"))
_spec = importlib.util.spec_from_loader("step08_verify", _loader)
step = importlib.util.module_from_spec(_spec)
_loader.exec_module(step)

BASE_STEPS = ("03-advertise", "06-settle", "09-dispute")
HAS_BASE = bool(shutil.which("git") and step.openssl_ed25519())
NO_BASE = "SKIPPED LOUDLY: git and OpenSSL 3+ (Ed25519) are needed to run steps 03, 06 and 09 for the base run"
HAS_GO = all((TOOLS_DIR / "01-seal" / p).is_file() for p in ("bin/cross_language", "venv/bin/python")) \
    and bool(shutil.which("go"))
NO_GO = "SKIPPED LOUDLY: step 01's sealer and Go verifier are not installed; run steps/01-seal.setup.sh"
ALL = [TOOLS_DIR / "01-seal" / "bin" / "cross_language", TOOLS_DIR / "02-book" / "bin" / "loop-step02-book",
       TOOLS_DIR / "05-deal" / "bin" / "loop-step05-deal", TOOLS_DIR / "bin" / "capsulectl",
       TOOLS_DIR / "bin" / "capsulectl-book"]
HAS_ALL = HAS_BASE and HAS_GO and all(p.is_file() for p in ALL)
NO_ALL = "SKIPPED LOUDLY: the full rehearsal run needs every step's tools; run steps/*.setup.sh"
for ok, why in ((HAS_BASE, NO_BASE), (HAS_GO, NO_GO), (HAS_ALL, NO_ALL)):
    if not ok:
        if os.environ.get("LOOP_REQUIRE_TOOLS") == "1":
            raise RuntimeError(f"LOOP_REQUIRE_TOOLS=1: {why}")
        print(f"\ntest_step08_verify: {why}", file=sys.stderr)


def run_step(base, run, *args):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    r = subprocess.run([str(STEPS / base), "--population", str(POPULATION), "--out", str(run / base), *args],
                       capture_output=True, text=True, env=env, timeout=900, cwd=ROOT)
    if r.returncode != 0:
        raise RuntimeError(f"{base} exited {r.returncode}: {r.stderr[-2000:]}")


def load(path):
    return json.loads(Path(path).read_text())


def dump(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")


def rehash(step_dir):
    """Recompute each proof sha256 in the finding, as a forger who edits proof bytes would."""
    f = load(step_dir / "finding.json")
    for p in f["proof"]:
        p["sha256"] = finding.sha256(step_dir / p["path"])
    dump(step_dir / "finding.json", f)


def write_run_json(run, steps, population="rehearsal", stage=None):
    """run.json for a test run; its stage is one of its own (listing exactly these steps) unless stage is given."""
    Path(run).mkdir(parents=True, exist_ok=True)
    if stage is None:
        stage = "t-" + "".join(steps)
        step.STAGES[stage] = list(steps)
    dump(Path(run) / "run.json", {"population": population, "stage": stage, "started": "2026-10-30T00:00:00Z",
                                   "steps": steps})


def verify(run):
    """Run 08 in this process on run; (finding, manifest entries by dir)."""
    out = run / "08-verify"
    step.main(["--population", str(POPULATION), "--out", str(out)])
    manifest = load(out / "verified.json")
    return load(out / "finding.json"), {e["dir"]: e for e in manifest["steps"]}


@unittest.skipUnless(HAS_BASE, NO_BASE)
class Step08Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base_tmp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.base_tmp.name) / "base"
        for base in BASE_STEPS:
            run_step(base, cls.base)
        write_run_json(cls.base, ["03", "06", "09", "08"])

    @classmethod
    def tearDownClass(cls):
        cls.base_tmp.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = Path(self.tmp.name) / "run"
        shutil.copytree(self.base, self.run)

    def tearDown(self):
        self.tmp.cleanup()

    def assertFailsNaming(self, base, entries, f):
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertEqual(entries[base]["status"], "failed")
        self.assertIn(base, f["detail"])
        others = [e for d, e in entries.items() if d != base]
        self.assertTrue(all(e["status"] == "reproduced" for e in others), others)
        self.assertEqual(finding.check_record(self.run / "08-verify", f), [])

    # --- Offline ---------------------------------------------------------------------------------
    def test_passes_offline_with_every_network_call_failing(self):
        def no_network(*a, **k):
            raise OSError("network disabled by the test")
        with mock.patch.object(socket, "socket", side_effect=no_network), \
                mock.patch.object(socket, "create_connection", side_effect=no_network), \
                mock.patch.object(socket, "getaddrinfo", side_effect=no_network):
            f, entries = verify(self.run)
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertEqual(f["not_checked"], [])
        self.assertEqual(sorted(entries), list(BASE_STEPS))
        for base, e in entries.items():
            self.assertEqual(e["status"], "reproduced")
            self.assertEqual(e["rederived"], e["recorded"])
            rec = load(self.run / base / "finding.json")
            self.assertEqual({x["path"]: x["sha256"] for x in e["files"]},
                             {"finding.json": finding.sha256(self.run / base / "finding.json"),
                              **{p["path"]: p["sha256"] for p in rec["proof"]}})
        self.assertEqual(finding.check(self.run), [])

    def test_the_bundle_is_never_written(self):
        before = {p: p.read_bytes() for p in self.run.rglob("*") if p.is_file()}
        verify(self.run)
        after = {p: p.read_bytes() for p in self.run.rglob("*") if p.is_file() and "08-verify" not in p.parts}
        self.assertEqual(before, after)

    # --- Planted failures ------------------------------------------------------------------------
    def test_a_proof_file_changed_by_one_byte_fails(self):
        f = self.run / "06-settle" / "controllers.json"
        text = f.read_text()
        f.write_text(text.replace("  ", " \t", 1))  # one space becomes a tab: the same JSON, other bytes
        verdict, entries = verify(self.run)
        self.assertFailsNaming("06-settle", entries, verdict)
        self.assertIn("does not match its sha256", verdict["detail"])
        # Mutant: without the digest check, the judge decides the same on these bytes and 08 would pass.
        with mock.patch.object(step.finding, "check_record", return_value=[]):
            verdict, _ = verify(self.run)
        self.assertEqual(verdict["result"], "PASS")

    def test_a_changed_proof_byte_under_a_forged_digest_fails_on_reproduction(self):
        f = self.run / "06-settle" / "ledger.jsonl"
        raw = bytearray(f.read_bytes())
        i = raw.index(b'"amount_atomic":') + len(b'"amount_atomic":')
        raw[i] = ord("9") if raw[i] != ord("9") else ord("8")
        f.write_bytes(bytes(raw))
        rehash(self.run / "06-settle")
        verdict, entries = verify(self.run)
        self.assertFailsNaming("06-settle", entries, verdict)
        self.assertIn("recorded PASS, re-derived FAIL", verdict["detail"])
        with mock.patch.object(step, "mismatches", return_value=[]):
            verdict, _ = verify(self.run)
        self.assertEqual(verdict["result"], "PASS")

    def test_a_result_flipped_pass_to_fail_over_unchanged_proof_fails_naming_the_step(self):
        f = load(self.run / "09-dispute" / "finding.json")
        f["result"] = "FAIL"
        dump(self.run / "09-dispute" / "finding.json", f)
        verdict, entries = verify(self.run)
        self.assertFailsNaming("09-dispute", entries, verdict)
        self.assertIn("09-dispute: did not hold: recorded FAIL, re-derived PASS", verdict["detail"])
        with mock.patch.object(step, "mismatches", return_value=[]):
            verdict, _ = verify(self.run)
        self.assertEqual(verdict["result"], "PASS")

    def test_a_result_flipped_fail_to_pass_over_unchanged_proof_fails_naming_the_step(self):
        # A real FAIL first: bob's disclosure counts one settlement too many, so step 06 decides FAIL on it.
        sd = self.run / "06-settle"
        shutil.rmtree(sd)
        pop = Path(self.tmp.name) / "population"
        shutil.copytree(POPULATION, pop)
        d = load(pop / "step06" / "disclosure-bob.json")
        d["settlements"] += 1
        dump(pop / "step06" / "disclosure-bob.json", d)
        subprocess.run([str(STEPS / "06-settle"), "--population", str(pop), "--out", str(sd)], check=True,
                       capture_output=True, cwd=ROOT, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual(load(sd / "finding.json")["result"], "FAIL")
        verdict, entries = verify(self.run)
        self.assertEqual(entries["06-settle"]["status"], "reproduced")  # a FAIL reproduced is a FAIL reproduced
        f = load(sd / "finding.json")
        f["result"], f["detail"] = "PASS", "flipped"
        dump(sd / "finding.json", f)
        verdict, entries = verify(self.run)
        self.assertFailsNaming("06-settle", entries, verdict)
        self.assertIn("recorded PASS, re-derived FAIL", verdict["detail"])

    def test_a_missing_proof_file_fails(self):
        (self.run / "09-dispute" / "checks.json").unlink()
        verdict, entries = verify(self.run)
        self.assertEqual(verdict["result"], "FAIL")
        self.assertEqual(entries["09-dispute"]["status"], "failed")
        self.assertIn("proof checks.json is not a file", verdict["detail"])

    def test_a_proof_key_that_is_not_the_repositorys_fails(self):
        sd = self.run / "09-dispute"
        a = load(sd / "announced.json")
        node = next(iter(a["announced_keys"]))
        a["announced_keys"][node] = "00" * 32
        dump(sd / "announced.json", a)
        rehash(sd)
        verdict, entries = verify(self.run)
        self.assertFailsNaming("09-dispute", entries, verdict)
        self.assertIn("the repository's is", verdict["detail"])
        # Mutant: without the key check the judge still catches it here (the key signs nothing it accepts).
        with mock.patch.dict(step.STEPS, {"09": (step.rederive_09, lambda c, m: [])}):
            verdict, _ = verify(self.run)
        self.assertEqual(verdict["result"], "FAIL")
        self.assertIn("re-derived FAIL", verdict["detail"])

    def test_a_rail_key_swapped_in_the_bundle_fails_on_the_key_check(self):
        sd = self.run / "06-settle"
        shutil.copyfile(POPULATION / "keys" / "alice" / "node-key.pub.pem", sd / "rail-key.pub.pem")
        rehash(sd)
        verdict, entries = verify(self.run)
        self.assertFailsNaming("06-settle", entries, verdict)
        self.assertIn("rail-key.pub.pem is not the repository's rail key", verdict["detail"])

    def test_a_status_page_number_changed_under_a_forged_digest_fails(self):
        sd = self.run / "06-settle"
        v = load(sd / "volume.json")
        v["filtered"] += 1
        dump(sd / "volume.json", v)
        rehash(sd)
        verdict, entries = verify(self.run)
        self.assertFailsNaming("06-settle", entries, verdict)
        self.assertIn("volume.json re-derived is not the recorded volume.json", verdict["detail"])
        with mock.patch.dict(step.NUMBERS, {"06": []}):
            verdict, _ = verify(self.run)
        self.assertEqual(verdict["result"], "PASS")

    def test_another_populations_finding_fails(self):
        f = load(self.run / "03-advertise" / "finding.json")
        f["population"] = "live"
        dump(self.run / "03-advertise" / "finding.json", f)
        verdict, entries = verify(self.run)
        self.assertEqual(verdict["result"], "FAIL")
        self.assertIn("population is live, this run is rehearsal", verdict["detail"])

    # --- Not reproduced is never a pass ----------------------------------------------------------
    def test_a_step_that_cannot_be_reproduced_is_not_checked_not_pass(self):
        sd = self.run / "11-report"  # 08 has no re-derivation for step 11
        sd.mkdir()
        (sd / "alice").mkdir()
        shutil.copyfile(self.run / "03-advertise" / "alice.commitment.json", sd / "alice" / "commitment.json")
        finding.write(sd, result="PASS", population="rehearsal", asserts="a report", pinned=[],
                      proof=["alice/commitment.json"], detail="", started=finding.now())
        verdict, entries = verify(self.run)
        self.assertEqual(verdict["result"], "NOT_CHECKED", verdict["detail"])
        self.assertEqual(entries["11-report"]["status"], "not_reproduced")
        self.assertIsNone(entries["11-report"]["rederived"])
        [nc] = verdict["not_checked"]
        self.assertEqual(sorted(nc), ["issue", "what", "why"])
        self.assertEqual(nc["issue"], step.STAGE1_ISSUE)
        self.assertIn("no re-derivation", nc["why"])
        self.assertIn("11-report: recorded PASS, not reproduced", verdict["detail"])
        self.assertEqual(finding.check(self.run), [])

    def test_a_not_checked_step_that_judged_nothing_is_not_reproduced_not_failed(self):
        # Step 01 with a pinned tool missing writes NOT_CHECKED, no proof and no checks.json (loop#204).
        sd = self.run / "01-seal"
        sd.mkdir()
        finding.write(sd, result="NOT_CHECKED", population="rehearsal", asserts="a seal", pinned=[], proof=[],
                      detail="node alice: a pinned tool is missing or not the pin", started=finding.now(),
                      not_checked=[{"what": "capsule-emit", "why": "not installed", "issue": "x/y#1"},
                                   {"what": "aac-go", "why": "not the pin", "issue": "x/y#1"}])
        verdict, entries = verify(self.run)
        self.assertEqual(entries["01-seal"]["status"], "not_reproduced", entries["01-seal"]["problems"])
        why = entries["01-seal"]["not_checked"]["why"]
        self.assertIn("the step says: capsule-emit: not installed; aac-go: not the pin", why)
        self.assertEqual(entries["01-seal"]["not_checked"]["issue"], step.EMPTY_ISSUE)
        self.assertEqual(verdict["result"], "NOT_CHECKED", verdict["detail"])

    def test_another_step_emptied_to_a_not_checked_still_fails_its_key_check(self):
        # 06 never writes an empty proof: emptying it and rewriting it NOT_CHECKED does not skip its trust anchor.
        sd = self.run / "06-settle"
        rec = load(sd / "finding.json")
        rec.update(result="NOT_CHECKED", proof=[], not_checked=[{"what": "w", "why": "y", "issue": "x/y#1"}])
        dump(sd / "finding.json", rec)
        f, entries = verify(self.run)
        self.assertFailsNaming("06-settle", entries, f)

    def test_step_12_without_capsulectl_is_not_checked_not_failed(self):
        class NoTools:
            @staticmethod
            def tools():
                return Path(self.tmp.name) / "none"
        with self.assertRaises(step.Unreproducible):
            step.rederive_12(NoTools, None)

    def test_a_step_whose_tool_is_missing_is_not_checked(self):
        sd = self.run / "05-deal"
        sd.mkdir()
        (sd / "deal.json").write_text("{}\n")
        (sd / "declarations.json").write_text("[]\n")
        finding.write(sd, result="PASS", population="rehearsal", asserts="a deal", pinned=[],
                      proof=["deal.json", "declarations.json"], detail="", started=finding.now())
        with mock.patch.dict(os.environ, {"LOOP_STEP05_DEAL_BIN": str(Path(self.tmp.name) / "none")}), \
                mock.patch.dict(step.STEPS, {"05": (step.rederive_05, lambda c: [])}):  # a stub fixture has no keys
            verdict, entries = verify(self.run)
        self.assertEqual(verdict["result"], "NOT_CHECKED")
        self.assertEqual(entries["05-deal"]["status"], "not_reproduced")
        self.assertIn("loop-step05-deal driver is not installed", entries["05-deal"]["not_checked"]["why"])

    def test_a_step_loop_sh_recorded_as_broken_is_not_checked(self):
        shutil.copytree(FIXTURES / "broken" / "02-book", self.run / "02-book")
        verdict, entries = verify(self.run)
        self.assertEqual(verdict["result"], "NOT_CHECKED")
        self.assertEqual(entries["02-book"]["recorded"]["result"], "FAIL")
        self.assertEqual(entries["02-book"]["status"], "not_reproduced")
        self.assertIn("loop.sh wrote this FAIL", entries["02-book"]["not_checked"]["why"])

    def test_a_live_run_is_not_checked(self):
        live = Path(self.tmp.name) / "live"
        sd = live / "10-judge"
        sd.mkdir(parents=True)
        write_run_json(live, ["10", "08"], "live")
        (sd / "recorded.json").write_text("{}\n")
        finding.write(sd, result="NOT_CHECKED", population="live", asserts="a judgment", pinned=[],
                      proof=["recorded.json"], detail="", started=finding.now(),
                      not_checked=[{"what": "x", "why": "y", "issue": "evidence-network/loop#188"}])
        out = live / "08-verify"
        step.main(["--population", str(ROOT / "population" / "live"), "--out", str(out)])
        f = load(out / "finding.json")
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertEqual(f["population"], "live")
        self.assertIn("live result", f["not_checked"][0]["what"])
        self.assertEqual(f["not_checked"][0]["issue"], step.LIVE_ISSUE)

    def test_a_listed_step_renamed_to_an_invalid_dir_fails_naming_it(self):
        (self.run / "06-settle").rename(self.run / "06-INVALID")
        f, entries = verify(self.run)
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("run.json lists step 06 but the run holds no 06-*/finding.json", f["detail"])

    def test_a_dispute_that_names_its_own_trusted_key_dir_fails(self):
        d = self.run / "09-dispute"
        doc = load(d / "dispute.json")
        pop = load(POPULATION / "population.json")["nodes"]
        doc["nodes"]["alice"]["key_dir"] = next(n["key_dir"] for n in pop if n["name"] == "bob")
        dump(d / "dispute.json", doc)
        rehash(d)
        f, entries = verify(self.run)
        self.assertFailsNaming("09-dispute", entries, f)
        self.assertIn("dispute.json is not the repository's step09/dispute.json", f["detail"])

    def test_a_referee_swapped_for_the_requester_fails(self):
        d = self.run / "09-dispute"
        doc = load(d / "dispute.json")
        doc["nodes"]["referee"]["key_dir"] = doc["nodes"]["requester"]["key_dir"]
        dump(d / "dispute.json", doc)
        rehash(d)
        f, entries = verify(self.run)
        self.assertFailsNaming("09-dispute", entries, f)

    def test_a_run_json_cut_to_fewer_steps_than_its_stage_is_not_a_pass(self):
        write_run_json(self.run, ["03", "06", "09", "08"], stage="1")  # stage 1 is loop.sh's 01 02 03 12 08
        f, _ = verify(self.run)
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        self.assertTrue(any(x["what"] == "step 01: its finding reproduced" for x in f["not_checked"]), f["not_checked"])
        manifest = load(self.run / "08-verify" / "verified.json")
        self.assertEqual(manifest["run_json"]["steps"], ["03", "06", "09", "08"])
        self.assertEqual(manifest["run_json"]["sha256"], finding.sha256(self.run / "run.json"))

    def test_a_forged_pass_that_claims_loop_sh_broke_it_is_not_excused(self):
        d = self.run / "06-settle"
        f6 = load(d / "finding.json")
        f6["asserts"] = step.BROKE
        dump(d / "finding.json", f6)
        f, entries = verify(self.run)
        self.assertEqual(entries["06-settle"]["status"], "reproduced", f["detail"])  # the claim excuses nothing

    def test_a_non_string_stage_is_an_unknown_stage_not_a_crash(self):
        for bad in ([], {}, 1):
            write_run_json(self.run, ["03", "06", "09", "08"], stage=bad)
            f, _ = verify(self.run)
            self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
            self.assertTrue((self.run / "08-verify" / "verified.json").is_file())

    def test_a_repeated_controller_id_in_05_is_refused(self):
        c = step.Ctx(self.run, self.run, self.run, POPULATION, {"population": "rehearsal", "started": "x"})
        want = {}
        for ctl in load(POPULATION / "step05" / "controllers.json")["controllers"]:
            key = step.pub_key(step.repo_path(ctl["key_dir"]) / "controller-key.pub.pem")
            nodes = step.node_pubs(POPULATION)
            want[ctl["id"]] = {"controller_declaration": 1, "controller_id": ctl["id"], "controller_key": key,
                               "evidence_keys": sorted(nodes[n] for n in ctl["nodes"])}
        with tempfile.TemporaryDirectory() as tmp:
            c.work = Path(tmp)
            dump(c.work / "declarations.json", list(want.values()))
            self.assertEqual(step.keys_05(c), [])
            first = dict(next(iter(want.values())), controller_key="ff" * 32)
            dump(c.work / "declarations.json", [first, *want.values()])
            self.assertEqual(step.keys_05(c), ["declarations.json repeats a controller id"])

    def test_a_malformed_finding_does_not_crash_08(self):
        d = self.run / "06-settle"
        f6 = load(d / "finding.json")
        f6["proof"] = [5]
        dump(d / "finding.json", f6)
        f, entries = verify(self.run)
        self.assertEqual(entries["06-settle"]["status"], "failed")
        self.assertTrue((self.run / "08-verify" / "verified.json").is_file())

    def test_a_corrupted_cited_commitment_the_judge_would_replace_fails(self):
        d = self.run / "03-advertise"
        doc = load(d / "alice.commitment.json")
        v = doc["sig"]["value"]
        doc["sig"]["value"] = ("A" if v[0] != "A" else "B") + v[1:]
        dump(d / "alice.commitment.json", doc)
        rehash(d)
        f, entries = verify(self.run)
        self.assertFailsNaming("03-advertise", entries, f)
        self.assertIn("differs from the cited bytes", f["detail"])

    def test_a_step_run_json_lists_but_the_run_lacks_fails_naming_it(self):
        shutil.rmtree(self.run / "06-settle")
        f, entries = verify(self.run)
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("run.json lists step 06 but the run holds no 06-*/finding.json", f["detail"])
        # Mutant: with the run.json list ignored, the missing step goes unnoticed.
        with mock.patch.object(step, "expected_steps", return_value=["03", "09", "08"]):
            f, _ = verify(self.run)
        self.assertNotIn("run.json lists step 06", f["detail"])

    def test_a_missing_live_step_does_not_turn_not_checked_into_pass(self):
        live = Path(self.tmp.name) / "live"
        for nn, name in (("06", "settle"), ("12", "join")):
            sd = live / f"{nn}-{name}"
            sd.mkdir(parents=True)
            (sd / "recorded.json").write_text("{}\n")
            finding.write(sd, result="NOT_CHECKED", population="live", asserts="x", pinned=[],
                          proof=["recorded.json"], detail="", started=finding.now(),
                          not_checked=[{"what": "x", "why": "y", "issue": "evidence-network/loop#188"}])
        write_run_json(live, ["06", "12", "08"], "live")
        shutil.rmtree(live / "12-join")
        out = live / "08-verify"
        step.main(["--population", str(ROOT / "population" / "live"), "--out", str(out)])
        self.assertEqual(load(out / "finding.json")["result"], "FAIL")

    def test_a_run_json_without_steps_is_not_checked(self):
        (self.run / "run.json").unlink()
        f, _ = verify(self.run)
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        self.assertEqual(f["not_checked"][0]["what"], "which steps the run holds")

    def test_an_empty_run_is_not_checked(self):
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        step.main(["--population", str(POPULATION), "--out", str(empty / "08-verify")])
        f = load(empty / "08-verify" / "finding.json")
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertEqual(finding.check(empty), [])


@unittest.skipUnless(HAS_BASE and HAS_GO, NO_GO)
class Step08NotCheckedListTest(unittest.TestCase):
    """A recorded not_checked list is reproduced entry for entry. Step 01's FAIL carries none since the producer
    signature is verified in a second language (#137), so the plant adds an entry the judge does not derive."""

    def test_a_not_checked_entry_the_judge_does_not_derive_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            run_step("01-seal", Path(tmp) / "sealed")
            capsule = load(Path(tmp) / "sealed" / "01-seal" / "capsule.json")
            capsule["effect"]["effect_attestation"] = "gate_executed"  # a class an in-process seal cannot have
            dump(Path(tmp) / "planted.json", capsule)
            run_step("01-seal", run, "--sealed", str(Path(tmp) / "planted.json"))
            write_run_json(run, ["01", "08"])
            verdict, entries = verify(run)
            self.assertEqual(entries["01-seal"]["status"], "reproduced", verdict["detail"])
            f = load(run / "01-seal" / "finding.json")
            f["not_checked"] = [{"what": "x", "why": "y", "issue": "evidence-network/loop#188"}]
            dump(run / "01-seal" / "finding.json", f)
            verdict, entries = verify(run)
            self.assertEqual(verdict["result"], "FAIL", verdict["detail"])
            self.assertIn("01-seal: did not hold: recorded not_checked [", verdict["detail"])
            # Mutant: with the not_checked comparison off, the planted entry would go through.
            with mock.patch.object(step, "canon", return_value=[]):
                verdict, _ = verify(run)
            self.assertEqual(verdict["result"], "PASS")


@unittest.skipUnless(HAS_ALL, NO_ALL)
class Step08RehearsalTest(unittest.TestCase):
    """08 on a rehearsal run of every existing step: as steps/08-verify.md's table says."""

    def test_every_step_is_reproduced(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "2026-10-30-rehearsal"
            for base in ("01-seal", "02-book", "03-advertise", "04-admit", "05-deal", "06-settle", "07-ask",
                         "09-dispute", "12-join"):
                run_step(base, run)
            # Stage 1, since 08 runs in its own process and knows only loop.sh's stages.
            write_run_json(run, ["01", "02", "03", "04", "05", "06", "07", "09", "12", "08"], stage="1")
            run_step("08-verify", run)
            f = load(run / "08-verify" / "finding.json")
            entries = {e["dir"]: e for e in load(run / "08-verify" / "verified.json")["steps"]}
            self.assertEqual(f["result"], "PASS", f["detail"])
            self.assertEqual({d: e["status"] for d, e in entries.items()}, {d: "reproduced" for d in entries})
            self.assertEqual(len(entries), 9)
            self.assertEqual(subprocess.run([sys.executable, "-B", str(STEPS / "finding.py"), "check", str(run)]
                                            ).returncode, 0)

    def test_a_07_proof_cut_to_its_number_file_fails(self):
        """Nothing for the judge to check must not read PASS: 07 with no book and no ask in its proof."""
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "2026-10-30-rehearsal"
            run_step("03-advertise", run)
            run_step("07-ask", run)
            write_run_json(run, ["07", "08"])
            d = run / "07-ask"
            dump(d / "first-answered.json", {"answered_at": None, "ask": None, "sealed": True})
            f = load(d / "finding.json")
            f["proof"] = [p for p in f["proof"] if p["path"] == "first-answered.json"]
            dump(d / "finding.json", f)
            rehash(d)
            out = run / "08-verify"
            step.main(["--population", str(POPULATION), "--out", str(out)])
            v = load(out / "finding.json")
            self.assertEqual(v["result"], "FAIL", v["detail"])
            self.assertIn("the proof holds no book of node", v["detail"])
            self.assertIn("the proof holds no alice/asks/bob/exchange/request.json", v["detail"])

    def test_a_scenario_dropped_from_04s_proof_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "2026-10-30-rehearsal"
            run_step("04-admit", run)
            write_run_json(run, ["04", "08"])
            f4 = load(run / "04-admit" / "finding.json")
            gone = sorted({Path(p["path"]).parts[1] for p in f4["proof"] if p["path"].startswith("scenarios/")})[0]
            f4["proof"] = [p for p in f4["proof"] if not p["path"].startswith(f"scenarios/{gone}/")]
            dump(run / "04-admit" / "finding.json", f4)
            out = run / "08-verify"
            step.main(["--population", str(POPULATION), "--out", str(out)])
            f = load(out / "finding.json")
            self.assertEqual(f["result"], "FAIL", f["detail"])
            self.assertIn(f"scenario {gone} is in the repository but not in the finding's proof", f["detail"])


LIVE5 = ROOT / "findings" / "2026-10-30-live-5"
LIVE_POPULATION = ROOT / "population" / "live"


def live5_07(dst):
    """Step 07 of the real live run 2026-10-30-live-5, in the proof shape step 07 now keeps.

    That run predates ask.json, exchange-subject.json and the ledger extract, so they are written here from
    what the old proof itself holds: each node was asked (stevenmih-3 through a second console, as its detail
    says), the exchange half is the one its exchange request names, and each old <stem>.ledger.json, which kept
    the one sealed request and the outcome citing it, becomes an extract of those two, read at the ask's back_at.
    The result, not_checked, first-answered.json and everything else are the live run's own."""
    src = LIVE5 / "07-ask"
    rec = load(src / "finding.json")
    for p in rec["proof"]:
        (dst / p["path"]).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src / p["path"], dst / p["path"])
    nodes = {}
    for node in load(LIVE_POPULATION / "population.json")["nodes"]:
        nd = dst / node["name"]
        nodes[node["name"]] = {"asked": True, "via": "a second node's console" if node["name"] == "stevenmih-3"
                               else "the reporter's console"}
        half = None
        for a in sorted((nd / "attempts").iterdir()):
            for led in a.glob("*.ledger.json"):
                old, timing = load(led), load(a / "timing.json")
                dump(led, {"ledger": "read", "read_at": timing["back_at"],
                           "requests": [{"index": 0, "record": old["request_record"], "header": old["request"]}],
                           "outcomes": [{"index": 1, "record": old["outcome"]["record"],
                                         "header": old["outcome"]["header"]}]})
            if (a / "exchange.request.json").is_file():
                half = load(a / "exchange.request.json")["subject"]["exchange"]
        dump(nd / "exchange-subject.json", {"half": half, "why": None})
        # The commitment each ask's deadline came from (its timing.json names nodes/commitments/<node_id>.json):
        # the bytes the same run's step 03 published, so a later re-sign in the repository changes nothing here.
        cd = nd / "commitment"
        (cd / "policies").mkdir(parents=True)
        shutil.copyfile(LIVE5 / "03-advertise" / f"{node['name']}.commitment.json", cd / "commitment.json")
        shutil.copyfile(LIVE5 / "03-advertise" / f"{node['name']}.policy.json",
                        cd / "policies" / f"{node['node_id']}.json")
        dump(cd / "choice.json", {"commitment": f"nodes/commitments/{node['node_id']}.json", "not_checked": []})
    dump(dst / "ask.json", {"self_node": "stevenmih-3", "second_console": True, "nodes": nodes})
    proof = ["first-answered.json", "ask.json"] + sorted(
        str(f.relative_to(dst)) for f in dst.rglob("*") if f.is_file() and f.parent != dst)
    finding.write(dst, result=rec["result"], population="live", asserts=rec["asserts"], pinned=sorted(rec["pinned"]),
                  proof=proof, detail=rec["detail"], not_checked=rec["not_checked"], started=rec["started"])
    return rec


@unittest.skipUnless(step.openssl_ed25519(), "SKIPPED LOUDLY: live 07's kept commitments verify with OpenSSL 3+ "
                                            "(Ed25519 pkeyutl -rawin), not installed here")
class Step08Live07Test(unittest.TestCase):
    """08 re-derives live step 07 from its proof alone, on the live run's real bytes (no console, no data dir)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = Path(self.tmp.name) / "2026-10-30-live-5"
        self.recorded = live5_07(self.run / "07-ask")
        write_run_json(self.run, ["07", "08"], "live", stage="1")

    def tearDown(self):
        self.tmp.cleanup()

    def verify(self):
        out = self.run / "08-verify"
        shutil.rmtree(out, ignore_errors=True)
        step.main(["--population", str(LIVE_POPULATION), "--out", str(out)])
        return load(out / "finding.json"), {e["dir"]: e for e in load(out / "verified.json")["steps"]}

    def test_live_07_is_reproduced_from_its_proof(self):
        f, entries = self.verify()
        e = entries["07-ask"]
        self.assertEqual(e["status"], "reproduced", e["problems"])
        self.assertEqual(e["rederived"]["result"], self.recorded["result"])
        canon = lambda xs: sorted(json.dumps(x, sort_keys=True) for x in xs)  # noqa: E731
        self.assertEqual(canon(e["rederived"]["not_checked"]), canon(self.recorded["not_checked"]))
        self.assertIn("07-ask: reproduced NOT_CHECKED", f["detail"])
        # The run left out the rest of stage 1: each step named, on loop.sh's whole-stage run.
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertEqual({nc["issue"] for nc in f["not_checked"]},
                         {step.RUN_ISSUE, step.TRUST_ISSUE, step.LEDGER_ISSUE, "evidence-network/loop#135"})
        # The run is on no merge on main, so its keys are read at this checkout and 08 says so (loop#280).
        unanchored = [nc for nc in f["not_checked"] if nc["what"] == f"step 07: {step.ANCHOR_WHAT}"]
        self.assertEqual([nc["issue"] for nc in unanchored], [step.TRUST_ISSUE])
        self.assertEqual([nc for nc in f["not_checked"] if nc["issue"] != step.RUN_ISSUE and nc not in unanchored][
                         :len(step.LIVE_RELIES_ON["07"])],
                         step.LIVE_RELIES_ON["07"])
        self.assertEqual(finding.check(self.run), [])

    def test_live_10_and_11_then_read_not_checked_on_the_judge_key(self):
        self.verify()
        env = {k: v for k, v in os.environ.items() if k not in ("LOOP_JUDGE_KEY", "LAB_DIR")}  # no lab key: 10 stops on it
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        for base in ("10-judge", "11-report"):
            r = subprocess.run([str(STEPS / base), "--population", str(LIVE_POPULATION), "--out", str(self.run / base)],
                               capture_output=True, text=True, env=env, timeout=300, cwd=ROOT)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        judge10 = step.step_module("10-judge")
        f10, f11 = load(self.run / "10-judge" / "finding.json"), load(self.run / "11-report" / "finding.json")
        for f in (f10, f11):
            self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        window = load(self.run / "10-judge" / "window.json") if (self.run / "10-judge" / "window.json").is_file() else None
        if window is None:  # the pinned pack is not fetched here: 10 stops before its window, on its setup
            self.assertEqual({nc["issue"] for nc in f10["not_checked"]}, {judge10.SETUP_ISSUE})
            return
        # 07 is reproduced and its asks finished in the as-of month, which the window now includes (Ethan, 2026-10-31).
        self.assertFalse(any("07-ask" in x and "outside the window" in x for x in window["refused"]), window["refused"])
        self.assertTrue(any(r["dir"] == "07-ask" for r in window["records"]), window["records"])
        # What is left to check is only the lab host's judge key, not the window.
        self.assertEqual({nc["issue"] for nc in f10["not_checked"]}, {judge10.JUDGE_KEY_ISSUE})
        self.assertEqual({nc["issue"] for nc in f11["not_checked"]}, {judge10.JUDGE_KEY_ISSUE})
        self.assertEqual(finding.check(self.run), [])

    def plant(self, edit):
        """Edit 07's proof as a forger would, rehash it, and run 08: the entry for 07."""
        edit(self.run / "07-ask")
        rehash(self.run / "07-ask")
        return self.verify()

    def test_a_verification_line_its_reply_does_not_give_fails(self):
        def forge(d):
            line = next(d.glob("stevenmih-1/attempts/*/exchange.verification"))
            line.write_text(line.read_text().replace("refusal reason=not_authorized", "verified checkpoints=1"))
        f, entries = self.plant(forge)
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("is not the one its reply gives", f["detail"])

    def test_a_recorded_pass_the_proof_does_not_give_fails(self):
        def forge(d):
            rec = load(d / "finding.json")
            rec["result"], rec["not_checked"] = "PASS", []
            dump(d / "finding.json", rec)
        f, entries = self.plant(forge)
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("recorded PASS, re-derived NOT_CHECKED", f["detail"])

    def test_an_ask_cut_from_the_proof_fails(self):
        def cut(d):
            gone = next(d.glob("stevenmih-2/attempts/*/history_card.request.json")).parent
            rec = load(d / "finding.json")
            rec["proof"] = [p for p in rec["proof"] if not (d / p["path"]).is_relative_to(gone)]
            dump(d / "finding.json", rec)
            shutil.rmtree(gone)
        f, entries = self.plant(cut)
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("stevenmih-2: history_card: 0 attempts in the proof, not one", f["detail"])

    def test_a_node_marked_unasked_fails(self):
        def skip(d):
            ask = load(d / "ask.json")
            ask["nodes"]["stevenmih-1"] = {"asked": False, "why": "no nodes/ entry"}
            dump(d / "ask.json", ask)
        f, entries = self.plant(skip)
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("stevenmih-1: not asked", f["detail"])

    def test_an_outcome_for_another_request_in_the_extract_fails(self):
        def forge(d):
            led = next(d.glob("stevenmih-1/attempts/*/checkpoints.ledger.json"))
            x = load(led)
            x["outcomes"][0]["header"]["statement"]["request_digest"] = "0" * 64
            dump(led, x)
        f, entries = self.plant(forge)
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("names another request than the one sealed", f["detail"])

    def test_an_exchange_subject_that_is_not_a_capsule_id_fails(self):
        def blank(d):  # "" would ask nothing about the exchange and add no not_checked entry
            dump(d / "stevenmih-1" / "exchange-subject.json", {"half": "", "why": None})
        f, entries = self.plant(blank)
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("neither null nor a capsule id", f["detail"])

    def test_a_kept_commitment_altered_after_signing_fails(self):
        def forge(d):
            c = load(d / "stevenmih-1" / "commitment" / "commitment.json")
            c["answers_within"] = "PT1H"
            dump(d / "stevenmih-1" / "commitment" / "commitment.json", c)
        f, entries = self.plant(forge)
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("commitment does not verify", f["detail"])

    def test_an_openssl_without_ed25519_leaves_live_07_not_reproduced_not_failed(self):
        with mock.patch.object(step, "openssl_ed25519", return_value=False):
            f, entries = self.verify()
        self.assertEqual(entries["07-ask"]["status"], "not_reproduced")
        self.assertIn("openssl 3.0 or later", entries["07-ask"]["not_checked"]["why"])
        self.assertEqual(f["result"], "NOT_CHECKED")

    def test_a_commitment_re_signed_later_in_the_repository_changes_nothing(self):
        live07 = step.step_module("07-ask")
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(live07, "PUBLISHED", Path(tmp)), mock.patch.object(live07, "LAB_RUNS", Path(tmp)), \
                mock.patch.object(step, "step_module", lambda base: live07):
            f, entries = self.verify()
        self.assertEqual(entries["07-ask"]["status"], "reproduced", entries["07-ask"]["problems"])

    def test_a_commitment_choice_with_no_commitment_and_no_reason_fails(self):
        def drop(d):  # with no commitment there is no deadline: a choice that says nothing about why can't stand
            shutil.rmtree(d / "stevenmih-1" / "commitment")
            (d / "stevenmih-1" / "commitment").mkdir()
            dump(d / "stevenmih-1" / "commitment" / "choice.json", {"commitment": None, "not_checked": []})
            rec = load(d / "finding.json")
            rec["proof"] = [p for p in rec["proof"] if not p["path"].startswith("stevenmih-1/commitment/")
                            or p["path"].endswith("choice.json")]
            dump(d / "finding.json", rec)
        f, entries = self.plant(drop)
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("names no commitment and no reason why", f["detail"])

    def test_a_ledger_extract_holding_a_record_that_is_not_the_asks_fails(self):
        # The extract holds only the asker's own request for this ask: another node's, another question's or one
        # outside the ask's time bracket is refused, so unrelated subjects can't be carried in it.
        for change in ({"responder_ref": "0" * 64, "audience": "0" * 64}, {"subject": {"exchange": "e" * 64}},
                       {"committed_at": "2026-10-30T20:00:00Z"}):
            with self.subTest(change=change):
                self.tearDown()  # a fresh copy of the live run for each plant
                self.setUp()

                def forge(d):
                    led = next(d.glob("stevenmih-1/attempts/*/checkpoints.ledger.json"))
                    x = load(led)
                    other = json.loads(json.dumps(x["requests"][0]))
                    other["record"] = "9" * 64
                    h = other["header"]
                    if "committed_at" in change:
                        h["committed_at"] = change["committed_at"]
                    if "subject" in change:
                        h["statement"]["request"]["subject"] = change["subject"]
                    if "responder_ref" in change:
                        h["statement"]["responder_ref"] = change["responder_ref"]
                        h["statement"]["request"]["audience"] = change["audience"]
                    x["requests"].append(other)
                    dump(led, x)
                f, entries = self.plant(forge)
                self.assertEqual(f["result"], "FAIL", f["detail"])
                self.assertIn("holds a request record that is not this ask's", f["detail"])

    def test_first_answered_the_proof_does_not_give_fails(self):
        def forge(d):
            fa = load(d / "first-answered.json")
            fa["answered_at"] = "2026-10-30T23:01:20.000000Z"
            dump(d / "first-answered.json", fa)
        f, entries = self.plant(forge)
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("first-answered.json re-derived is not the recorded first-answered.json", f["detail"])


LIVE2 = ROOT / "findings" / "2026-10-31-live-2"


def live2_04(dst):
    """Step 04 of the real live run 2026-10-31-live-2, in the proof shape step 04 now keeps.

    That run predates nodes.json, so it is written here from what the finding itself says: each node was read,
    with the ledger record count its detail gives. A decision with rows takes the record number its rows.json key
    gives; one without takes the next free number before the following one (the numbers name records only in a
    problem, and the run had none). The decision lines, rows.json and the result are the run's own."""
    import re
    src = LIVE2 / "04-admit"
    rec = load(src / "finding.json")
    for p in rec["proof"]:
        (dst / p["path"]).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src / p["path"], dst / p["path"])
    nodes = {}
    for name, n, total in re.findall(r"node (\S+): (\d+) of (\d+) ledger records", rec["detail"]):
        kept, d = [], dst / name
        if int(n):
            keys = iter(sorted(int(k) for k in load(d / "rows.json"))) if (d / "rows.json").is_file() else iter(())
            for ln in (d / "decisions.jsonl").read_text().splitlines():
                rows = "rows" in json.loads(ln)["model_attestation"]["compute_attestation"]["admission_decision"]
                kept.append(next(keys) if rows else (kept[-1] + 1 if kept else 1))
        nodes[name] = {"read": "ok", "records": int(total), "kept": kept}
    dump(dst / "nodes.json", nodes)
    finding.write(dst, result=rec["result"], population="live", asserts=rec["asserts"], pinned=sorted(rec["pinned"]),
                  proof=["nodes.json"] + [p["path"] for p in rec["proof"]], detail=rec["detail"],
                  not_checked=rec["not_checked"], started=rec["started"])
    return rec


@unittest.skipUnless((TOOLS_DIR / "01-seal" / "bin" / "cross_language").is_file(),
                     "SKIPPED LOUDLY: step 01's Go verifier is not installed (live 04 checks each decision record in "
                     "it, loop#135); run steps/01-seal.setup.sh")
class Step08Live04Test(unittest.TestCase):
    """08 re-derives live step 04 from its proof alone, on the live run's real decision lines (loop#188)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = Path(self.tmp.name) / "2026-10-31-live-2"
        self.recorded = live2_04(self.run / "04-admit")
        write_run_json(self.run, ["04", "08"], "live", stage="1")

    def tearDown(self):
        self.tmp.cleanup()

    def verify(self):
        out = self.run / "08-verify"
        shutil.rmtree(out, ignore_errors=True)
        step.main(["--population", str(LIVE_POPULATION), "--out", str(out)])
        return load(out / "finding.json"), {e["dir"]: e for e in load(out / "verified.json")["steps"]}

    def test_live_04_is_reproduced_from_its_proof(self):
        f, entries = self.verify()
        e = entries["04-admit"]
        self.assertEqual(e["status"], "reproduced", e["problems"])
        self.assertEqual(e["rederived"]["result"], "PASS")
        self.assertIn(step.LIVE_RELIES_ON["04"][0], f["not_checked"])
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertEqual(finding.check(self.run), [])

    def plant(self, edit):
        edit(self.run / "04-admit")
        rehash(self.run / "04-admit")
        return self.verify()

    def test_a_decision_line_altered_fails(self):
        def forge(d):
            p = d / "stevenmih-2" / "decisions.jsonl"
            lines = p.read_text().splitlines()
            lines[0] = lines[0].replace('"verdict":"GO"', '"verdict":"MAYBE"').replace('"verdict": "GO"', '"verdict": "MAYBE"')
            p.write_text("\n".join(lines) + "\n")
        f, entries = self.plant(forge)
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("recorded PASS, re-derived FAIL", f["detail"])

    def test_a_rows_file_its_decisions_do_not_give_fails(self):
        def forge(d):
            p = d / "stevenmih-2" / "rows.json"
            rows = load(p)
            first = next(iter(rows))
            rows[first] = rows[first][:1]
            dump(p, rows)
        f, entries = self.plant(forge)
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("stevenmih-2/rows.json re-derived is not the recorded stevenmih-2/rows.json", f["detail"])

    def test_a_rows_file_the_judge_does_not_write_again_fails(self):
        def plant(d):  # stevenmih-1 has no decision, so no rows: a rows.json cited for it is not the judge's
            (d / "stevenmih-1").mkdir(exist_ok=True)
            dump(d / "stevenmih-1" / "rows.json", {"1": []})
            rec = load(d / "finding.json")
            rec["proof"].append({"path": "stevenmih-1/rows.json", "sha256": ""})
            dump(d / "finding.json", rec)
        f, entries = self.plant(plant)
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("stevenmih-1/rows.json re-derived is not the recorded", f["detail"])

    def test_an_unupgraded_live_bundle_is_not_reproduced_not_failed(self):
        # A live 04 or 07 finding from before its re-derivable proof shape (live-2's 04, live-5's 07, as found).
        run = Path(self.tmp.name) / "old"
        shutil.copytree(LIVE2 / "04-admit", run / "04-admit")
        shutil.copytree(LIVE5 / "07-ask", run / "07-ask")
        # A finding pins what versions.lock held when it ran. These ran under an earlier lock; this
        # test is about their proof's shape, so their pins are set to today's lock (pinned is not proof).
        lock = finding.pins()
        for d in ("04-admit", "07-ask"):
            rec = load(run / d / "finding.json")
            rec["pinned"] = {n: {"repository": lock[n][0], "ref": lock[n][1]} for n in rec["pinned"]}
            dump(run / d / "finding.json", rec)
        write_run_json(run, ["04", "07", "08"], "live", stage="1")
        step.main(["--population", str(LIVE_POPULATION), "--out", str(run / "08-verify")])
        entries = {e["dir"]: e for e in load(run / "08-verify" / "verified.json")["steps"]}
        for d, shape in (("04-admit", "nodes.json"), ("07-ask", "ask.json")):
            self.assertEqual(entries[d]["status"], "not_reproduced", entries[d]["problems"])
            self.assertIn(f"its proof holds no {shape}", entries[d]["not_checked"]["why"])
        self.assertEqual(load(run / "08-verify" / "finding.json")["result"], "NOT_CHECKED")

    def test_a_node_dropped_from_nodes_json_fails(self):
        def drop(d):
            n = load(d / "nodes.json")
            n.pop("stevenmih-1")
            dump(d / "nodes.json", n)
        f, entries = self.plant(drop)
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("nodes.json names nodes", f["detail"])

    def test_a_node_marked_unread_does_not_pass(self):
        def unread(d):
            n = load(d / "nodes.json")
            n["stevenmih-1"] = {"read": "data_dir"}
            dump(d / "nodes.json", n)
        f, entries = self.plant(unread)
        self.assertEqual(f["result"], "FAIL", f["detail"])  # recorded PASS, re-derived NOT_CHECKED
        self.assertIn("recorded PASS, re-derived NOT_CHECKED", f["detail"])


@unittest.skipUnless(step.openssl_ed25519() and shutil.which("git"), "SKIPPED LOUDLY: live 03 verifies with OpenSSL 3+ "
                                                                  "and reads this repository's HEAD with git")
class Step08Live03Test(unittest.TestCase):
    """08 re-derives live step 03 from its proof alone: step 03 run live on this repository's HEAD, which carries
    the lab nodes' published commitments (loop#188)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = Path(self.tmp.name) / "2026-10-31-live"
        r = subprocess.run([str(STEPS / "03-advertise"), "--population", str(LIVE_POPULATION),
                            "--out", str(self.run / "03-advertise")], capture_output=True, text=True, cwd=ROOT,
                           env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"), timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr)
        write_run_json(self.run, ["03", "08"], "live", stage="1")

    def tearDown(self):
        if getattr(self, "sealed", None):  # a subtest's planted bound ends with it
            self.sealed.stop()
            self.sealed = None
        self.tmp.cleanup()

    def verify(self):
        out = self.run / "08-verify"
        shutil.rmtree(out, ignore_errors=True)
        step.main(["--population", str(LIVE_POPULATION), "--out", str(out)])
        return load(out / "finding.json"), {e["dir"]: e for e in load(out / "verified.json")["steps"]}

    def plant(self, edit):
        edit(self.run / "03-advertise")
        rehash(self.run / "03-advertise")
        return self.verify()

    def test_live_03_is_reproduced_from_its_proof(self):
        recorded = load(self.run / "03-advertise" / "finding.json")
        f, entries = self.verify()
        e = entries["03-advertise"]
        self.assertEqual(e["status"], "reproduced", e["problems"])
        self.assertEqual(e["rederived"]["result"], recorded["result"])
        self.assertIn(load(self.run / "03-advertise" / "carrier.json")["head"],
                      subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True,
                                     text=True).stdout)
        self.assertEqual(finding.check(self.run), [])

    def test_a_commitment_not_what_the_carrier_held_fails(self):
        def forge(d):
            f = next(d.glob("*.commitment.json"))
            doc = load(f)
            doc["answers_within"] = "PT9H"
            dump(f, doc)
        f, entries = self.plant(forge)
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("is not what the carrier's commit", f["detail"])

    def test_a_listed_key_the_repository_does_not_list_is_not_reproduced(self):
        def forge(d):
            doc = load(d / "carrier.json")
            next(iter(doc["nodes"].values()))["key_id"] = "ab" * 32
            dump(d / "carrier.json", doc)
        f, entries = self.plant(forge)
        self.assertEqual(entries["03-advertise"]["status"], "not_reproduced")
        self.assertEqual(entries["03-advertise"]["not_checked"]["issue"], step.TRUST_ISSUE)

    def test_a_node_dropped_from_carrier_json_fails(self):
        def drop(d):
            doc = load(d / "carrier.json")
            doc["nodes"].pop(next(iter(doc["nodes"])))
            dump(d / "carrier.json", doc)
        f, entries = self.plant(drop)
        self.assertEqual(f["result"], "FAIL", f["detail"])

    def test_a_carrier_commit_not_in_this_checkout_is_named(self):
        def other(d):
            doc = load(d / "carrier.json")
            doc["head"] = "0" * 40
            dump(d / "carrier.json", doc)
        f, entries = self.plant(other)
        self.assertEqual(entries["03-advertise"]["status"], "reproduced", entries["03-advertise"]["problems"])
        self.assertIn("is not in the checkout 08 runs in", " ".join(x["why"] for x in f["not_checked"]))

    def first(self, d):
        doc = load(d / "carrier.json")
        name = sorted(doc["nodes"])[0]
        return doc, name, doc["nodes"][name]

    def test_a_path_dropped_from_carrier_json_and_its_file_deleted_fails(self):
        # 08 works out the paths from the commit itself, so a commitment the commit holds can't be hidden.
        def drop(d):
            doc, name, entry = self.first(d)
            entry["paths"] = {p: v for p, v in entry["paths"].items() if not p.endswith(f"{entry['node_id']}.json")}
            dump(d / "carrier.json", doc)
            rec = load(d / "finding.json")
            rec["proof"] = [p for p in rec["proof"] if p["path"] != f"{name}.commitment.json"]
            dump(d / "finding.json", rec)
            (d / f"{name}.commitment.json").unlink()
        f, entries = self.plant(drop)
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("is not what the carrier's commit", f["detail"])

    def test_a_policy_not_what_the_carrier_held_fails(self):
        def forge(d):
            p = next(d.glob("*.policy.json"))
            p.write_bytes(p.read_bytes() + b" ")
        f, entries = self.plant(forge)
        self.assertEqual(f["result"], "FAIL", f["detail"])

    def test_a_path_03_never_reads_fails(self):
        def extra(d):
            doc, name, entry = self.first(d)
            entry["paths"]["nodes/other.json"] = False
            dump(d / "carrier.json", doc)
        f, entries = self.plant(extra)
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("which 03 never reads", f["detail"])

    def test_a_head_that_is_not_a_full_commit_id_is_named_not_compared(self):
        def ref(d):
            doc = load(d / "carrier.json")
            doc["head"] = "HEAD"
            dump(d / "carrier.json", doc)
        f, entries = self.plant(ref)
        self.assertIn("names no full commit id", " ".join(x["why"] for x in f["not_checked"]))

    def test_a_listed_key_the_carrier_commit_did_not_list_fails(self):
        # The key matches this checkout's listing but not the listing at carrier.json's commit.
        repo = Path(self.tmp.name) / "repo"
        (repo / "nodes").mkdir(parents=True)
        shutil.copytree(ROOT / "nodes" / "commitments", repo / "nodes" / "commitments")
        for f in (ROOT / "nodes").glob("*.json"):
            shutil.copyfile(f, repo / "nodes" / f.name)
        doc = load(self.run / "03-advertise" / "carrier.json")
        node_id = next(iter(doc["nodes"].values()))["node_id"]
        git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t.invalid", "-c", "commit.gpgsign=false"]
        listing = repo / "nodes" / f"{node_id}.json"
        real = listing.read_bytes()
        changed = load(listing)
        changed["key_id"] = "ab" * 32
        dump(listing, changed)
        subprocess.run([*git, "init", "-q"], check=True)
        subprocess.run([*git, "add", "nodes"], check=True)
        subprocess.run([*git, "commit", "-qm", "then"], check=True)
        then = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        listing.write_bytes(real)
        subprocess.run([*git, "commit", "-qam", "now"], check=True)

        def point(d):
            doc = load(d / "carrier.json")
            doc["head"] = then
            dump(d / "carrier.json", doc)
        with mock.patch.object(step, "ROOT", repo):
            f, entries = self.plant(point)
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn(f"is not what nodes/{node_id}.json held at", f["detail"])

    def published(self, merge=True, reissue=False, rotate=None):
        """A scratch repository holding this checkout's nodes/ ("then", carrier.json's head), whose main then takes
        the run under findings/ by a pull request merge (a single-parent commit when not merge), with the first
        carried node's commitment re-issued in the same merge when reissue. rotate ("before" or "after" the merge)
        lists another key for that node on main. origin is evidence-network/loop and origin/main is main. Returns
        (repo, the run inside it)."""
        repo = Path(self.tmp.name) / "repo"
        (repo / "nodes").mkdir(parents=True)
        shutil.copytree(ROOT / "nodes" / "commitments", repo / "nodes" / "commitments")
        for f in (ROOT / "nodes").glob("*.json"):
            shutil.copyfile(f, repo / "nodes" / f.name)
        node_id = next(iter(load(self.run / "03-advertise" / "carrier.json")["nodes"].values()))["node_id"]
        git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t.invalid", "-c", "commit.gpgsign=false"]

        def rotated():
            listing = repo / "nodes" / f"{node_id}.json"
            dump(listing, dict(load(listing), key_id="ee" * 32))
            subprocess.run([*git, "commit", "-qam", "rotate"], check=True)
        subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
        subprocess.run([*git, "add", "nodes"], check=True)
        subprocess.run([*git, "commit", "-qm", "then"], check=True)
        then = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        doc = load(self.run / "03-advertise" / "carrier.json")
        doc["head"] = then
        dump(self.run / "03-advertise" / "carrier.json", doc)
        rehash(self.run / "03-advertise")
        run = repo / "findings" / self.run.name
        shutil.copytree(self.run, run)
        if rotate == "before":
            rotated()
        subprocess.run([*git, "checkout", "-qb", "pr"], check=True)
        if reissue:
            commitment = repo / "nodes" / "commitments" / f"{node_id}.json"
            commitment.write_bytes(commitment.read_bytes() + b"\n")
        subprocess.run([*git, "add", "findings", "nodes"], check=True)
        subprocess.run([*git, "commit", "-qm", "findings"], check=True)
        subprocess.run([*git, "checkout", "-q", "main"], check=True)
        subprocess.run([*git, "merge", "-q", "--no-ff" if merge else "--ff-only", "-m", "Merge pull request", "pr"],
                       check=True)
        if rotate == "after":
            rotated()
        subprocess.run([*git, "remote", "add", "origin", "https://github.com/evidence-network/loop.git"], check=True)
        subprocess.run([*git, "update-ref", "refs/remotes/origin/main", "main"], check=True)
        return repo, run

    def head_relies(self, f):
        return [nc for nc in f["not_checked"] if nc["what"].startswith("step 03: carrier.json's commit")]

    def unanchored(self, f):
        return [nc for nc in f["not_checked"] if nc["what"] == f"step 03: {step.ANCHOR_WHAT}"]

    def verify_in(self, repo, run):
        out = run / "08-verify"
        with mock.patch.object(step, "ROOT", repo):
            step.main(["--population", str(LIVE_POPULATION), "--out", str(out)])
        return load(out / "finding.json"), {e["dir"]: e for e in load(out / "verified.json")["steps"]}

    def test_the_carrier_commit_the_publishing_merge_holds_is_borne_out(self):
        repo, run = self.published()
        f, entries = self.verify_in(repo, run)
        self.assertEqual(entries["03-advertise"]["status"], "reproduced", entries["03-advertise"]["problems"])
        self.assertEqual(self.head_relies(f), [], f["not_checked"])
        self.assertEqual(self.unanchored(f), [], f["not_checked"])

    def test_a_key_rotated_after_publication_still_holds_and_one_rotated_before_does_not(self):
        # loop#280 end to end: the run is held to the listing of the merge that published it, never to a later
        # one and never to an earlier one its own signed times might point at.
        for rotate, status in (("after", "reproduced"), ("before", "not_reproduced")):
            with self.subTest(rotate):
                self.tearDown()
                self.setUp()
                repo, run = self.published(rotate=rotate)
                f, entries = self.verify_in(repo, run)
                e = entries["03-advertise"]
                self.assertEqual(e["status"], status, e)
                if status == "not_reproduced":
                    self.assertIn("not the key the nodes/ listings 03 is held to give", e["not_checked"]["why"])

    def test_commitments_re_issued_in_the_publishing_merge_are_named(self):
        # The merge that published 03 holds another commitment than carrier.json's commit: 03 rests on its record.
        repo, run = self.published(reissue=True)
        f, entries = self.verify_in(repo, run)
        self.assertEqual(entries["03-advertise"]["status"], "reproduced", entries["03-advertise"]["problems"])
        self.assertEqual([nc["issue"] for nc in self.head_relies(f)], [step.TRUST_ISSUE], f["not_checked"])
        self.assertEqual(self.unanchored(f), [], f["not_checked"])

    def test_a_run_no_merge_published_has_no_anchor_and_says_so(self):
        for case in ("not in the repository", "a single-parent commit only"):
            with self.subTest(case):
                self.tearDown()
                self.setUp()
                repo, run = self.published(merge=case == "not in the repository")
                if case == "not in the repository":
                    run = self.run
                f, entries = self.verify_in(repo, run)
                self.assertEqual(len(self.head_relies(f)), 1, f["not_checked"])
                self.assertEqual([nc["issue"] for nc in self.unanchored(f)], [step.TRUST_ISSUE], f["not_checked"])

    def test_carrier_json_unreadable_or_naming_another_node_fails(self):
        for change in ("unreadable", "outside"):
            with self.subTest(change):
                self.tearDown()
                self.setUp()

                def edit(d):
                    if change == "unreadable":
                        (d / "carrier.json").write_text("{")
                    else:
                        doc = load(d / "carrier.json")
                        doc["nodes"]["stranger"] = {"node_id": None, "key_id": None, "paths": {}}
                        dump(d / "carrier.json", doc)
                f, entries = self.plant(edit)
                self.assertEqual(f["result"], "FAIL", f["detail"])

    def test_an_old_shape_live_03_bundle_is_not_reproduced(self):
        shutil.rmtree(self.run / "03-advertise")
        shutil.copytree(ROOT / "findings" / "2026-10-31-live-4" / "03-advertise", self.run / "03-advertise")
        f, entries = self.verify()
        self.assertEqual(entries["03-advertise"]["status"], "not_reproduced")
        self.assertIn("its proof holds no carrier.json", entries["03-advertise"]["not_checked"]["why"])


ONE_SEAL = TOOLS_DIR / "01-seal"


class Step08StateReliesTest(unittest.TestCase):
    """A live step's record of its own run is named on #193 only when the proof does not bear it out (loop#193)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.pop, self.work, self.listings = t / "pop", t / "run" / "nn-step", t / "nodes"
        for d in (self.pop, self.work, self.listings):
            d.mkdir(parents=True)
        dump(self.pop / "population.json", {"population": "live", "nodes": [
            {"name": "n1", "node_id": "id1"}, {"name": "n2", "node_id": "id2"}]})
        for n, key in (("id1", "aa" * 32), ("id2", "bb" * 32)):
            dump(self.listings / f"{n}.json", {"node_id": n, "key_id": key})
        env = mock.patch.dict(os.environ, {"LOOP_NODES_DIR": str(self.listings)})
        env.start()
        self.addCleanup(env.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def ctx(self, started="2026-10-31T05:00:00Z"):
        record = {"population": "live", "started": started, "proof": [{"path": "nodes.json"}]}
        return step.Ctx(self.work, self.work, Path(self.tmp.name), self.pop, record)

    def named(self, c, nn):
        return [x["what"] for x in c.relies if x["what"].startswith(f"step {nn}") and x["issue"] == step.TRUST_ISSUE]

    def test_04_names_its_record_unless_every_node_was_read(self):
        for states, want in ((("ok", "ok"), 0), (("ok", "unreadable"), 1)):
            with self.subTest(states):
                dump(self.work / "nodes.json", {"n1": {"read": states[0]}, "n2": {"read": states[1]}})
                c = self.ctx()
                step.keys_04_live(c)
                self.assertEqual(len(self.named(c, "04")), want)

    def test_05_names_its_record_unless_every_node_was_read_and_always_its_cutoff(self):
        for read, missing, since, want in ((True, [], None, 0), (False, [], None, 1), (True, ["pin"], None, 1),
                                           (True, [], "2026-10-30T00:00:00Z", 1)):
            with self.subTest(read=read, missing=missing, since=since):
                dump(self.work / "nodes.json", {"read": {"n1": True, "n2": read}, "missing": missing,
                                                "sealed_since": since})
                c = self.ctx()
                self.assertEqual(step.keys_05_live(c), [])
                self.assertEqual(len(self.named(c, "05")), want, c.relies)
                if since:
                    self.assertIn(since, self.named(c, "05")[0])

    def test_09_names_its_record_unless_every_node_it_ran_was_read_with_no_key_error(self):
        nodes = [{"name": "n1", "node_id": "id1"}, {"name": "n2", "node_id": "id2"}]
        for extra, read, errors, want in ((False, True, [], 0), (False, False, [], 1), (False, True, ["e"], 1),
                                          (True, True, [], 1)):
            with self.subTest(extra=extra, read=read, errors=errors):
                ran = nodes + ([{"name": "n4", "node_id": None}] if extra else [])  # the twins run's unread node
                dump(self.work / "nodes.json", {"nodes": ran, "read": {"n1": True, "n2": read}, "announced": [],
                                                "key_errors": errors})
                c = self.ctx()
                self.assertEqual(step.keys_09_live(c, None), [])
                self.assertEqual(len(self.named(c, "09")), want, c.relies)

    def test_02_holds_each_book_to_the_nodes_join_commitment_and_always_names_its_bound_keys(self):
        m = step.step_module("02-book")
        dump(self.work / "nodes.json", {"missing": [], "nodes": {"n1": {"read": "ok"}, "n2": {"read": "ok"}}})
        both = ["aa" * 32, "bb" * 32]
        for n, key in (("n1", "aa" * 32), ("n2", "bb" * 32)):
            (self.work / n).mkdir(exist_ok=True)
            dump(self.work / n / "inputs.json", {"node_pub": key, "book_id": f"{n}-book", "book_key": "cc" * 32,
                                                 "checkpoint_pub": "cc" * 32, "node_keys": both, "bound": [key]})
        books = {"aa" * 32: {("n1-book", "cc" * 32)}, "bb" * 32: {("n2-book", "cc" * 32)}}
        with mock.patch.object(step, "join_commitment_books", return_value=books):
            c = self.ctx()
            self.assertEqual(step.keys_02_live(c, m), [])
            self.assertEqual(self.named(c, "02"), [f"step 02: node {n}'s trusted keys (`bound`, read from the book's "
                                                   "profile on the node)" for n in ("n1", "n2")])
            # A book no join commitment of n2's names, and n1's receipt held apart from a trimmed key set.
            doc = load(self.work / "n2" / "inputs.json")
            doc["book_id"] = "other-book"
            dump(self.work / "n2" / "inputs.json", doc)
            doc = load(self.work / "n1" / "inputs.json")
            doc["node_keys"] = ["aa" * 32]
            dump(self.work / "n1" / "inputs.json", doc)
            c = self.ctx()
            step.keys_02_live(c, m)
            got = " ".join(self.named(c, "02"))
            self.assertIn("node n2's book id and checkpoint key", got)
            self.assertIn("node n1's node keys a receipt is held apart from", got)
            self.assertNotIn("node n1's book id", got)
            # An unlisted node: its key, book and trusted keys named, and its trimmed node_keys too.
            (self.listings / "id2.json").unlink()
            c = self.ctx()
            step.keys_02_live(c, m)
            self.assertIn("step 02: node n2's key, book and trusted keys", [x["what"] for x in c.relies])
            self.assertNotIn("node keys a receipt", " ".join(self.named(c, "02")))  # n1 holds the one listed key
            doc = load(self.work / "n2" / "inputs.json")
            doc["node_keys"] = []
            dump(self.work / "n2" / "inputs.json", doc)
            c = self.ctx()
            step.keys_02_live(c, m)
            self.assertIn("node n2's node keys a receipt is held apart from", " ".join(self.named(c, "02")))

    def test_01_names_its_record_unless_every_node_sealed_under_its_key_in_this_run(self):
        m = step.step_module("01-seal")
        for n, key in (("n1", "aa" * 32), ("n2", "bb" * 32)):
            (self.work / n).mkdir(exist_ok=True)
            dump(self.work / n / "capsule.json", {"key_id": key, "timestamp": "2026-10-31T05:00:00.5Z"})
        for case, want in (("fresh", 0), ("older", 1), ("given", 1), ("pin", 1)):
            with self.subTest(case):
                nodes = {n: {"read": "sealed", "own_seal": case != "given"} for n in ("n1", "n2")}
                dump(self.work / "nodes.json", {"pins": {"seen": [], "missing": ["x"] if case == "pin" else []},
                                                "nodes": nodes})
                c = self.ctx(started="2026-10-31T06:00:00Z" if case == "older" else "2026-10-31T05:00:00Z")
                self.assertEqual(step.keys_01_live(c, m), [])
                self.assertEqual(len(self.named(c, "01")), want, c.relies)


@unittest.skipUnless((ONE_SEAL / "bin" / "cross_language").is_file() and (ONE_SEAL / "venv" / "bin" / "python").is_file(),
                     "SKIPPED LOUDLY: step 01's sealer and Go verifier are not installed; run steps/01-seal.setup.sh")
class Step08Live01Test(unittest.TestCase):
    """08 re-derives live step 01 from its proof alone: a live node (rehearsal alice's TEST key in a data dir,
    listed in a scratch nodes/ under that key) seals, and 08 checks its capsule again (loop#188)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        (t / "lab" / "keys").mkdir(parents=True)
        for f in ("node-key.pem", "node-key.pub.pem"):
            shutil.copyfile(POPULATION / "keys" / "alice" / f, t / "lab" / "keys" / f)
        mod = step.step_module("01-seal")
        key = mod.raw_public_hex(POPULATION / "keys" / "alice" / "node-key.pub.pem")
        (t / "nodes").mkdir()
        dump(t / "nodes" / "n1.json", {"node_id": "n1", "key_id": key})
        self.pop = t / "pop"
        self.pop.mkdir()
        dump(self.pop / "population.json", {"population": "live", "rail": None, "nodes": [
            {"name": "lab-1", "node_id": "n1", "data_dir": str(t / "lab"), "book_dir": None}]})
        self.env = mock.patch.dict(os.environ, {"LOOP_NODES_DIR": str(t / "nodes")})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.t = t
        self.run = t / "2026-10-31-live"
        self.seal_live()

    def seal_live(self, **env):
        shutil.rmtree(self.run, ignore_errors=True)
        r = subprocess.run([str(STEPS / "01-seal"), "--population", str(self.pop), "--out", str(self.run / "01-seal")],
                           capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", **env))
        self.assertEqual(r.returncode, 0, r.stderr)
        write_run_json(self.run, ["01", "08"], "live", stage="1")
        return load(self.run / "01-seal" / "finding.json")

    def tearDown(self):
        self.tmp.cleanup()

    def verify(self):
        out = self.run / "08-verify"
        shutil.rmtree(out, ignore_errors=True)
        step.main(["--population", str(self.pop), "--out", str(out)])
        return load(out / "finding.json"), {e["dir"]: e for e in load(out / "verified.json")["steps"]}

    def plant(self, edit):
        edit(self.run / "01-seal")
        rehash(self.run / "01-seal")
        return self.verify()

    def test_live_01_is_reproduced_from_its_proof(self):
        recorded = load(self.run / "01-seal" / "finding.json")
        self.assertEqual(recorded["result"], "PASS", recorded["detail"])
        f, entries = self.verify()
        e = entries["01-seal"]
        self.assertEqual(e["status"], "reproduced", e["problems"])
        self.assertEqual(e["rederived"]["result"], "PASS")
        # Every node sealed under its listed key: nodes.json's per-node state is borne out, so nothing is 01's word.
        # (LOOP_NODES_DIR gives the listings here, which 08 names apart: loop#280.)
        self.assertFalse([nc for nc in f["not_checked"] if nc["what"].startswith("step 01")
                          and nc["what"] != f"step 01: {step.ANCHOR_WHAT}"], f["not_checked"])
        self.assertEqual(finding.check(self.run), [])

    def test_a_node_key_the_listing_does_not_publish_fails(self):
        def swap(d):
            shutil.copyfile(POPULATION / "keys" / "bob" / "node-key.pub.pem", d / "lab-1" / "node-key.pub.pem")
        f, entries = self.plant(swap)
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("is not the key nodes/n1.json lists", f["detail"])

    def test_a_capsule_altered_after_sealing_fails(self):
        def forge(d):
            c = load(d / "lab-1" / "capsule.json")
            c["effect"]["effect_attestation"] = "witnessed"
            dump(d / "lab-1" / "capsule.json", c)
        f, entries = self.plant(forge)
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("recorded PASS, re-derived FAIL", f["detail"])

    def test_a_checks_file_the_capsule_does_not_give_fails(self):
        def forge(d):
            ch = load(d / "lab-1" / "checks.json")
            ch["checks"]["model"]["detail"] = "x"
            dump(d / "lab-1" / "checks.json", ch)
        f, entries = self.plant(forge)
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("lab-1/checks.json re-derived is not the recorded", f["detail"])

    def test_a_node_marked_unread_does_not_pass(self):
        def unread(d):
            n = load(d / "nodes.json")
            n["nodes"]["lab-1"] = {"read": "data_dir"}
            dump(d / "nodes.json", n)
        f, entries = self.plant(unread)
        self.assertEqual(f["result"], "FAIL", f["detail"])

    def test_a_node_that_could_not_seal_reproduces_its_fail(self):
        (self.t / "lab" / "keys" / "node-key.pem").write_text("not a key")
        recorded = self.seal_live()
        self.assertEqual(recorded["result"], "FAIL")
        self.assertIn("capsule-emit could not seal", recorded["detail"])
        self.assertEqual(load(self.run / "01-seal" / "nodes.json")["nodes"]["lab-1"]["read"], "seal_failed")
        f, entries = self.verify()
        self.assertEqual(entries["01-seal"]["status"], "reproduced", entries["01-seal"]["problems"])

    def test_pins_missing_at_the_run_reproduce_its_not_checked(self):
        recorded = self.seal_live(LOOP_TOOLS=str(self.t / "no-tools"))
        self.assertEqual(recorded["result"], "NOT_CHECKED")
        f, entries = self.verify()  # 08 runs where the pins are installed, and judges what the run recorded
        self.assertEqual(entries["01-seal"]["status"], "reproduced", entries["01-seal"]["problems"])

    def test_a_pins_state_with_no_missing_pin_fails(self):
        def forge(d):
            n = load(d / "nodes.json")
            n["nodes"]["lab-1"] = {"read": "pins"}
            dump(d / "nodes.json", n)
        f, entries = self.plant(forge)
        self.assertEqual(f["result"], "FAIL", f["detail"])

    def test_a_node_dropped_or_a_capsule_cut_fails(self):
        for cut in ("node", "capsule"):
            with self.subTest(cut):
                self.seal_live()

                def edit(d):
                    if cut == "node":
                        n = load(d / "nodes.json")
                        n["nodes"].pop("lab-1")
                        dump(d / "nodes.json", n)
                    else:
                        rec = load(d / "finding.json")
                        rec["proof"] = [p for p in rec["proof"] if p["path"] != "lab-1/capsule.json"]
                        dump(d / "finding.json", rec)
                        (d / "lab-1" / "capsule.json").unlink()
                f, entries = self.plant(edit)
                self.assertEqual(f["result"], "FAIL", f["detail"])

    def test_an_unlisted_node_reproduces_its_not_checked(self):
        (self.t / "nodes" / "n1.json").unlink()
        recorded = self.seal_live()
        self.assertEqual(recorded["result"], "NOT_CHECKED", recorded["detail"])
        f, entries = self.verify()
        self.assertEqual(entries["01-seal"]["status"], "reproduced", entries["01-seal"]["problems"])

    def test_a_verifier_not_at_the_pin_is_not_reproduced(self):
        real_module = step.step_module
        mod = real_module("01-seal")
        real = mod.installed_pins
        mod.installed_pins = lambda python, verifier: ({}, [{"what": "aac-go", "why": "aac-go is 0.0.1, not the pin",
                                                              "issue": "x/y#1"}])
        try:
            with mock.patch.object(step, "step_module", lambda base: mod if base == "01-seal" else real_module(base)):
                f, entries = self.verify()
        finally:
            mod.installed_pins = real
        self.assertEqual(entries["01-seal"]["status"], "not_reproduced")
        self.assertIn("not the pin", entries["01-seal"]["not_checked"]["why"])

    def test_an_old_shape_live_01_bundle_is_not_reproduced(self):
        shutil.rmtree(self.run / "01-seal")
        shutil.copytree(ROOT / "findings" / "2026-10-31-live-5" / "01-seal", self.run / "01-seal")
        # The old shape alone, at today's pins: a finding pinned to an older release fails its integrity check first.
        record = load(self.run / "01-seal" / "finding.json")
        record["pinned"] = {n: {"repository": finding.pins()[n][0], "ref": finding.pins()[n][1]}
                            for n in record["pinned"]}
        dump(self.run / "01-seal" / "finding.json", record)
        f, entries = self.verify()
        self.assertEqual(entries["01-seal"]["status"], "not_reproduced")
        self.assertIn("its proof holds no nodes.json", entries["01-seal"]["not_checked"]["why"])


STEP09_LIVE = ROOT / "tests" / "fixtures" / "step09" / "live"


@unittest.skipUnless(step.openssl_ed25519(), "SKIPPED LOUDLY: live 09 verifies Results with OpenSSL 3+ (Ed25519)")
class Step08Live09Test(unittest.TestCase):
    """08 re-derives live step 09 from its proof alone, on step 09's live fixture (four lab nodes, as the twins run
    has); three are listed in a scratch nodes/ under the keys their data dirs hold, the fourth is not (loop#188)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.pop = t / "pop"
        self.pop.mkdir()
        pop = load(STEP09_LIVE / "population.json")
        listed = [n for n in pop["nodes"] if n["name"] != "lab-4"]  # the repository's population: no twins node
        dump(self.pop / "population.json", dict(pop, nodes=listed))
        mod = step.step_module("09-dispute")
        (t / "nodes").mkdir()
        for n in listed:
            key = mod.aac.key_id_of_pem(STEP09_LIVE / "nodes" / n["name"] / "data" / "keys" / "node-key.pub.pem")
            dump(t / "nodes" / f"{n['node_id']}.json", {"node_id": n["node_id"], "key_id": key})
        self.env = mock.patch.dict(os.environ, {"LOOP_NODES_DIR": str(t / "nodes"), "LAB_DIR": str(STEP09_LIVE)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.run = t / "2026-10-31-live"
        r = subprocess.run([str(STEPS / "09-dispute"), "--population", str(STEP09_LIVE), "--out",
                            str(self.run / "09-dispute")], capture_output=True, text=True, timeout=300,
                           env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
        self.assertEqual(r.returncode, 0, r.stderr)
        write_run_json(self.run, ["09", "08"], "live", stage="1")

    def tearDown(self):
        self.tmp.cleanup()

    def verify(self):
        out = self.run / "08-verify"
        shutil.rmtree(out, ignore_errors=True)
        step.main(["--population", str(self.pop), "--out", str(out)])
        return load(out / "finding.json"), {e["dir"]: e for e in load(out / "verified.json")["steps"]}

    def plant(self, edit):
        edit(self.run / "09-dispute")
        rehash(self.run / "09-dispute")
        return self.verify()

    def test_live_09_is_reproduced_and_names_the_unlisted_twins_node(self):
        recorded = load(self.run / "09-dispute" / "finding.json")
        self.assertEqual(recorded["result"], "PASS", recorded["detail"])
        f, entries = self.verify()
        self.assertEqual(entries["09-dispute"]["status"], "reproduced", entries["09-dispute"]["problems"])
        self.assertIn(step.LIVE_RELIES_ON["09"][0], f["not_checked"])
        self.assertIn("step 09: node lab-4's announced key", [x["what"] for x in f["not_checked"]])
        self.assertEqual({x["issue"] for x in f["not_checked"] if "lab-4" in x["what"]}, {step.TWINS_ISSUE})

    def twins_listing(self, key=None):
        mod = step.step_module("09-dispute")
        twin = load(STEP09_LIVE / "population.json")["nodes"][3]
        key = key or mod.aac.key_id_of_pem(STEP09_LIVE / "nodes" / twin["name"] / "data" / "keys" / "node-key.pub.pem")
        d = Path(os.environ["LOOP_NODES_DIR"]) / "twins"
        d.mkdir(exist_ok=True)
        dump(d / f"{twin['node_id']}.json", {"node_id": twin["node_id"], "key_id": key})

    def test_a_twins_node_listed_under_nodes_twins_has_its_key_held_and_is_no_longer_named(self):
        self.twins_listing()  # loop#209
        f, entries = self.verify()
        self.assertEqual(entries["09-dispute"]["status"], "reproduced", entries["09-dispute"]["problems"])
        self.assertFalse([x for x in f["not_checked"] if x["issue"] == step.TWINS_ISSUE], f["not_checked"])

    def test_a_twins_key_the_nodes_twins_listing_does_not_publish_fails(self):
        self.twins_listing(key="ab" * 32)
        f, entries = self.verify()
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("nodes/twins/", f["detail"])

    def test_a_held_result_altered_fails(self):
        def forge(d):
            p = next(d.glob("*/ledger/received-adjudications.jsonl"))
            held = json.loads(p.read_text().splitlines()[0])
            held["verdict_capsule"]["big"] = 2
            p.write_text(json.dumps(held) + "\n")
        f, entries = self.plant(forge)
        self.assertEqual(f["result"], "FAIL", f["detail"])

    def test_an_announced_key_the_listing_does_not_publish_fails(self):
        def forge(d):
            n = load(d / "nodes.json")
            twins = load(STEP09_LIVE / "population.json")["nodes"][3]["node_id"]
            pair = sorted(p for p in n["announced"] if p[0] != twins)[0]
            pair[1] = "ab" * 32
            dump(d / "nodes.json", n)
        f, entries = self.plant(forge)
        self.assertEqual(f["result"], "FAIL", f["detail"])

    def test_a_key_announced_for_a_node_the_run_did_not_read_fails(self):
        def forge(d):
            n = load(d / "nodes.json")
            n["announced"].append(["f" * 64, "ab" * 32])
            dump(d / "nodes.json", n)
        f, entries = self.plant(forge)
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("which is no node the run read", f["detail"])

    def test_a_node_name_outside_the_proof_fails(self):
        def forge(d):
            n = load(d / "nodes.json")
            n["nodes"].append({"name": "/tmp", "node_id": "e" * 64})
            n["read"]["/tmp"] = True
            dump(d / "nodes.json", n)
        f, entries = self.plant(forge)
        self.assertEqual(f["result"], "FAIL", f["detail"])

    def test_a_cut_ledger_stays_named_as_09s_reading(self):
        # Cutting a party's received file is 09's word about what it copied: 08 can't see it, so the #193 entry
        # that the ledgers are copied whole must stay in 08's not_checked.
        def cut(d):  # a consistent forger: cut a party's received file, then judge the cut bundle again
            next(d.glob("*/ledger/received-adjudications.jsonl")).unlink()
            started = load(d / "finding.json")["started"]
            with mock.patch.dict(os.environ, {"LAB_DIR": ""}):
                step.step_module("09-dispute").judge_live({"population": "live"}, d, started)
        f, entries = self.plant(cut)
        self.assertIn(step.LIVE_RELIES_ON["09"][0], f["not_checked"])

    def test_a_node_the_population_lists_dropped_from_nodes_json_fails(self):
        def drop(d):
            n = load(d / "nodes.json")
            n["nodes"] = [x for x in n["nodes"] if x["name"] != "lab-1"]
            dump(d / "nodes.json", n)
        f, entries = self.plant(drop)
        self.assertEqual(f["result"], "FAIL", f["detail"])

    def test_an_old_shape_live_09_bundle_is_not_reproduced(self):
        shutil.rmtree(self.run / "09-dispute")
        shutil.copytree(ROOT / "findings" / "2026-10-31-live-6" / "09-dispute", self.run / "09-dispute")
        # It ran under an earlier versions.lock; this test is about its proof's shape, so its pins
        # are set to today's lock (pinned is not proof).
        lock = finding.pins()
        rec = load(self.run / "09-dispute" / "finding.json")
        rec["pinned"] = {n: {"repository": lock[n][0], "ref": lock[n][1]} for n in rec["pinned"]}
        dump(self.run / "09-dispute" / "finding.json", rec)
        f, entries = self.verify()
        self.assertEqual(entries["09-dispute"]["status"], "not_reproduced")
        self.assertIn("its proof holds no nodes.json", entries["09-dispute"]["not_checked"]["why"])


def lightning_ledgers(lab, n=2):
    """lab/lab-1..3 ledgers: n payments lab-2 -> lab-1, each with the payer's preimage. The preimages exist only in
    this temp dir, as step 06 reads them; step 06's proof keeps their outcome, never the value."""
    import hashlib

    def obs(role, cid, h, pre, **more):
        block = {"v": 1, "observed_by": role, "channel": "payment.lifecycle.v1", "exchange_id": f"{role}-ex-{cid}",
                 "phase": "output_settlement_observed", "source": "wallet_reported", "settlement": "terminal",
                 "segment": 1, "payment_hash": h, "amount_msat": 1000000000, **more}
        if pre:
            block["preimage"] = pre
        return {"capsule_id": cid, "model_attestation": {"compute_attestation": {"x-mesh-settlement-v1": block}}}
    rows = {1: [], 2: [], 3: []}
    for k in range(n):
        pre = f"{k + 1:02x}" * 32
        h = hashlib.sha256(bytes.fromhex(pre)).hexdigest()
        rows[2].append(obs("payer", f"p{k}", h, pre))
        rows[1].append(obs("provider", f"s{k}", h, None, credited_msat=999999000, fee_msat=1000))
    for i, lines in rows.items():
        (lab / f"lab-{i}" / "ledger").mkdir(parents=True)
        (lab / f"lab-{i}" / "ledger" / "capsules.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))


def live06(dst, tmp, n=2, status="PASS", paired=None, drop_node=None, tamper_bundle=False, rows_only_peer=False):
    """Step 06 run on a synthetic live Lightning population (ledgers and signed-bundle checks made here), its
    out dir being dst: the proof 08 re-derives. Returns the finding."""
    import hashlib
    lab = Path(tmp) / "lab"
    lightning_ledgers(lab, n)
    if drop_node:
        shutil.rmtree(lab / f"lab-{drop_node}")
    doc = load(LIVE_POPULATION / "population.json")
    for i, node in enumerate(doc["nodes"], 1):
        node["data_dir"] = f"$STEP06_LAB/lab-{i}"
    bundles = Path(tmp) / "bundles"
    bundles.mkdir()
    ids = {node["name"]: node["node_id"] for node in doc["nodes"]}
    for me, peer in (("stevenmih-1", "stevenmih-2"), ("stevenmih-2", "stevenmih-1")):
        bundle = bundles / f"{peer}.for.{me}.json"
        bundle.write_text(json.dumps({"state": "bundle", "bundle": {"records": []}}))
        dump(bundles / f"check.{ids[me]}.{ids[peer]}.json", {
            "node_id": ids[me], "peer_node_id": ids[peer], "bundle_file": bundle.name,
            "bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
            "report": {"counterparty_count": {"status": status, "method": "pairs", "paired": n if paired is None else paired,
                                                 "unpaired": []},
                       "disclosed_count": {"settlements": n}}})
        if tamper_bundle:
            bundle.write_text("changed")
    doc["settlement_checks"] = str(bundles)
    pop = Path(tmp) / "pop"
    pop.mkdir()
    dump(pop / "population.json", doc)
    r = subprocess.run([str(STEPS / "06-settle"), "--population", str(pop), "--out", str(dst)],
                       capture_output=True, text=True, env=dict(os.environ, STEP06_LAB=str(lab)), timeout=300, cwd=ROOT)
    if r.returncode != 0:
        raise RuntimeError(f"06-settle exited {r.returncode}: {r.stderr[-1000:]}")
    return load(dst / "finding.json")


@unittest.skipUnless(HAS_BASE, NO_BASE)
class Step08Live06Test(unittest.TestCase):
    """08 re-derives live step 06 on the Lightning rail from its proof alone (loop#258)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.run = Path(self.tmp.name) / "run" / "2026-10-31-live-t"
        self.recorded = live06(self.run / "06-settle", self.tmp.name)
        write_run_json(self.run, ["06", "08"], "live", stage="1")

    def tearDown(self):
        self.tmp.cleanup()

    def verify(self, population=LIVE_POPULATION):
        out = self.run / "08-verify"
        shutil.rmtree(out, ignore_errors=True)
        step.main(["--population", str(population), "--out", str(out)])
        return load(out / "finding.json"), {e["dir"]: e for e in load(out / "verified.json")["steps"]}

    def plant(self, edit, population=LIVE_POPULATION):
        edit(self.run / "06-settle")
        rehash(self.run / "06-settle")
        return self.verify(population)

    def test_live_06_is_reproduced_from_its_proof(self):
        self.assertEqual(self.recorded["result"], "PASS")
        f, entries = self.verify()
        e = entries["06-settle"]
        self.assertEqual(e["status"], "reproduced", e["problems"])
        self.assertEqual(e["rederived"], e["recorded"])
        for x in step.LIVE_RELIES_ON["06"]:
            self.assertIn(x, f["not_checked"])
        self.assertEqual(finding.check(self.run), [])

    def test_no_preimage_is_in_the_proof_08_reads(self):
        for p in (self.run / "06-settle").rglob("*.json"):
            for k in range(1, 3):
                self.assertNotIn(f"{k:02x}" * 32, p.read_text())

    def test_an_altered_record_fails(self):
        def forge(d):
            p = d / "stevenmih-1" / "recorded.json"
            rec = load(p)
            for o in rec["settlement_observations"]:
                o["fee_msat"] = 0
            dump(p, rec)
        f, entries = self.plant(forge)
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("recorded PASS, re-derived", entries["06-settle"]["problems"][0])

    def test_a_finality_outcome_edited_to_unproven_fails(self):
        def forge(d):
            p = d / "stevenmih-2" / "recorded.json"
            rec = load(p)
            rec["settlement_observations"][0]["preimage_check"] = None
            dump(p, rec)
        f, entries = self.plant(forge)
        self.assertEqual(entries["06-settle"]["status"], "failed")

    def test_a_forged_counterparty_row_fails(self):
        def forge(d):
            rows = load(d / "counterparty.json")
            rows[0]["status"] = "FAIL"
            dump(d / "counterparty.json", rows)
        f, entries = self.plant(forge)
        self.assertEqual(entries["06-settle"]["status"], "failed")

    def test_a_counterparty_row_dropped_fails(self):
        def forge(d):
            dump(d / "counterparty.json", load(d / "counterparty.json")[:1])
        f, entries = self.plant(forge)
        self.assertEqual(entries["06-settle"]["status"], "failed")

    def test_a_row_that_is_no_check_of_a_peer_fails(self):
        def forge(d):
            rows = load(d / "counterparty.json")
            rows.append(dict(rows[0], peer="stevenmih-9"))
            dump(d / "counterparty.json", rows)
        f, entries = self.plant(forge)
        self.assertEqual(entries["06-settle"]["status"], "failed")
        self.assertIn("not one check of a node's book", " ".join(entries["06-settle"]["problems"]))

    def test_a_node_dropped_from_the_proof_fails(self):
        def forge(d):
            shutil.rmtree(d / "stevenmih-1")
            rec = load(d / "finding.json")
            rec["proof"] = [p for p in rec["proof"] if not p["path"].startswith("stevenmih-1/")]
            dump(d / "finding.json", rec)
        f, entries = self.plant(forge)
        self.assertEqual(entries["06-settle"]["status"], "failed")

    def test_a_recorded_file_of_a_node_outside_the_population_fails(self):
        def forge(d):
            (d / "stevenmih-9").mkdir()
            shutil.copyfile(d / "stevenmih-2" / "recorded.json", d / "stevenmih-9" / "recorded.json")
            rec = load(d / "finding.json")
            rec["proof"].append({"path": "stevenmih-9/recorded.json", "sha256": ""})
            dump(d / "finding.json", rec)
        f, entries = self.plant(forge)
        self.assertEqual(entries["06-settle"]["status"], "failed")
        self.assertIn("stevenmih-9/recorded.json is no node of the population", entries["06-settle"]["problems"])

    def test_another_rail_is_not_reproduced_not_failed(self):
        pop = Path(self.tmp.name) / "other-pop"
        pop.mkdir()
        dump(pop / "population.json", dict(load(LIVE_POPULATION / "population.json"), rail=None))
        f, entries = self.verify(pop)
        self.assertEqual(entries["06-settle"]["status"], "not_reproduced")
        self.assertIn("the live Lightning rail only", entries["06-settle"]["not_checked"]["why"])

    def honest(self, **kw):
        """A fresh honest 06 run with these differences, verified by 08."""
        shutil.rmtree(self.run / "06-settle")
        scratch = Path(self.tmp.name) / "honest"
        scratch.mkdir()
        self.recorded = live06(self.run / "06-settle", scratch, **kw)
        return self.verify()

    def test_an_honest_fail_row_is_reproduced_as_fail(self):
        f, entries = self.honest(status="FAIL")
        self.assertEqual(self.recorded["result"], "FAIL")
        self.assertEqual(entries["06-settle"]["status"], "reproduced", entries["06-settle"]["problems"])
        self.assertEqual(entries["06-settle"]["rederived"]["result"], "FAIL")

    def test_an_honest_check_older_than_the_books_is_reproduced_as_not_checked(self):
        f, entries = self.honest(paired=1)
        self.assertEqual(self.recorded["result"], "NOT_CHECKED")
        self.assertEqual(entries["06-settle"]["status"], "reproduced", entries["06-settle"]["problems"])

    def test_an_honest_changed_bundle_is_reproduced_as_not_checked(self):
        f, entries = self.honest(tamper_bundle=True)
        self.assertEqual(self.recorded["result"], "NOT_CHECKED")
        self.assertIn("missing or changed since", self.recorded["not_checked"][0]["why"])
        self.assertEqual(entries["06-settle"]["status"], "reproduced", entries["06-settle"]["problems"])

    def test_an_honest_unreadable_node_is_reproduced_as_not_checked(self):
        f, entries = self.honest(drop_node=3)
        self.assertEqual(self.recorded["result"], "NOT_CHECKED")
        self.assertEqual(entries["06-settle"]["status"], "reproduced", entries["06-settle"]["problems"])

    def test_a_checks_file_without_a_rows_check_fails_naming_it(self):
        def forge(d):
            checks = load(d / "checks.json")
            del checks["counterparty_count:stevenmih-1:stevenmih-2"]
            dump(d / "checks.json", checks)
        f, entries = self.plant(forge)
        self.assertEqual(entries["06-settle"]["status"], "failed")

    def test_a_bundle_missing_note_carrying_a_pass_fails(self):
        def forge(d):  # the carry-forward takes 06's word that a bundle is gone, never a pass
            checks = load(d / "checks.json")
            k = "counterparty_count:stevenmih-1:stevenmih-2"
            checks[k] = {"ok": True, "detail": "stevenmih-1's check names a bundle that is missing or changed since x"}
            dump(d / "checks.json", checks)
            rows = load(d / "counterparty.json")
            for r in rows:
                if r["node"] == "stevenmih-1":
                    r["paired"] = 5
            dump(d / "counterparty.json", rows)
        f, entries = self.plant(forge)
        self.assertEqual(entries["06-settle"]["status"], "failed")

    def test_an_old_shape_live_06_bundle_is_not_reproduced(self):
        def old(d):  # a live 06 from before checks.json and counterparty.json
            for n in ("checks.json", "counterparty.json"):
                (d / n).unlink()
            rec = load(d / "finding.json")
            rec["proof"] = [p for p in rec["proof"] if p["path"] not in ("checks.json", "counterparty.json")]
            dump(d / "finding.json", rec)
        f, entries = self.plant(old)
        self.assertEqual(entries["06-settle"]["status"], "not_reproduced")
        self.assertIn("its proof holds no checks.json", entries["06-settle"]["not_checked"]["why"])




class Step08PublicationTest(unittest.TestCase):
    """loop#280: a live step is held to the repository inputs of the pull request merge on evidence-network/loop's
    main that first held its files, never to a time the run's own keys signed."""

    NODE = "a" * 64

    def git(self, *a, repo=None):
        return subprocess.run(["git", "-C", str(repo or self.repo), "-c", "user.name=t", "-c", "user.email=t@t.invalid",
                               "-c", "commit.gpgsign=false", *a], capture_output=True, text=True, check=True).stdout.strip()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / "nodes").mkdir(parents=True)
        self.git("init", "-q", "-b", "main")
        self.key("11")
        self.commit("list")
        self.git("remote", "add", "origin", "https://github.com/evidence-network/loop.git")
        self.run = self.repo / "findings" / "2026-10-31-live"
        self.step = self.run / "01-seal"

    def tearDown(self):
        self.tmp.cleanup()

    def key(self, k):
        dump(self.repo / "nodes" / f"{self.NODE}.json", {"key_id": k * 32})

    def commit(self, msg):
        self.git("add", "-A")
        self.git("commit", "-qm", msg)
        return self.git("rev-parse", "HEAD")

    def pr(self, msg, edit, merge=True):
        """A branch off main that makes `edit`, merged into main (a merge commit, else fast-forward); returns main."""
        self.git("checkout", "-qb", msg)
        edit()
        self.commit(msg)
        self.git("checkout", "-q", "main")
        self.git("merge", "-q", "--no-ff" if merge else "--ff-only", "-m", f"Merge {msg}", msg)
        self.sync()
        return self.git("rev-parse", "HEAD")

    def sync(self):
        self.git("update-ref", "refs/remotes/origin/main", "main")

    def write_step(self, d=None, text="capsule"):
        d = d or self.step
        d.mkdir(parents=True, exist_ok=True)
        (d / "capsule.json").write_text(text + "\n")

    def anchor(self, d=None):
        return step.publication(d or self.step, root=self.repo)

    def listed_at(self, commit):
        env = step.inputs_at(commit, Path(self.tmp.name) / commit, root=self.repo)
        return load(Path(env["LOOP_NODES_DIR"]) / f"{self.NODE}.json")["key_id"]

    def test_a_step_published_while_its_key_was_listed_stays_held_to_it_after_a_rotation(self):
        merged = self.pr("findings", self.write_step)
        self.key("22")  # the key is retired after publication
        self.commit("rotate")
        self.sync()
        self.assertEqual(self.anchor(), (merged, None))
        self.assertEqual(self.listed_at(merged), "11" * 32)

    def test_a_step_published_after_the_retirement_is_held_to_the_new_listing(self):
        # A forged run, however old the times its capsules carry, reaches main only after the key was retired.
        self.key("22")
        self.commit("rotate")
        merged = self.pr("findings", self.write_step)
        self.assertEqual(self.listed_at(self.anchor()[0]), "22" * 32)
        self.assertEqual(self.anchor()[0], merged)

    def test_a_retirement_in_the_publishing_merge_holds_the_step_to_the_new_listing(self):
        def both():
            self.write_step()
            self.key("22")
        merged = self.pr("findings", both)
        self.assertEqual(self.anchor(), (merged, None))
        self.assertEqual(self.listed_at(merged), "22" * 32)

    def test_bytes_that_differ_from_main_or_a_step_not_on_main_have_no_anchor(self):
        self.write_step()
        self.assertIsNone(self.anchor()[0])  # not on main yet
        merged = self.pr("findings", self.write_step)
        (self.step / "capsule.json").write_text("forged\n")
        self.assertIsNone(self.anchor()[0])
        self.write_step()
        self.assertEqual(self.anchor()[0], merged)

    def test_a_file_edited_after_publication_moves_the_anchor_later(self):
        self.pr("findings", self.write_step)
        self.key("22")
        self.commit("rotate")
        edited = self.pr("edit", lambda: self.write_step(text="edited"))
        self.assertEqual(self.anchor(), (edited, None))
        self.assertEqual(self.listed_at(edited), "22" * 32)

    def test_single_parent_commits_are_never_anchors(self):
        # A squash, a rebase commit or a direct push: the next merge that holds the bytes is the anchor.
        self.write_step()
        self.commit("squash")
        self.sync()
        self.assertIsNone(self.anchor()[0])
        later = self.pr("other", lambda: (self.repo / "README").write_text("x\n"))
        self.assertEqual(self.anchor(), (later, None))

    def test_an_intermediate_commit_that_listed_the_key_is_not_an_anchor(self):
        # A rebase merge or a direct push: one commit re-lists the retired key beside the forged step, the next
        # restores nodes/; neither is a merge, and the next merge holds nodes/ restored.
        self.key("22")
        self.commit("rotate")
        self.key("11")
        self.write_step()
        self.commit("re-list and forge")
        self.key("22")
        self.commit("restore")
        merged = self.pr("other", lambda: (self.repo / "README").write_text("x\n"))
        self.assertEqual(self.anchor(), (merged, None))
        self.assertEqual(self.listed_at(merged), "22" * 32)

    def test_a_step_added_later_does_not_move_an_earlier_steps_anchor(self):
        first = self.pr("findings", self.write_step)
        self.key("22")
        self.commit("rotate")
        later = self.run / "02-book"
        second = self.pr("more", lambda: self.write_step(later))
        self.assertEqual(self.anchor(), (first, None))
        self.assertEqual(self.anchor(later), (second, None))

    def test_an_excluded_commit_is_never_an_anchor_whatever_its_own_tree_says(self):
        def forged():
            self.write_step()
            dump(self.repo / step.EXCLUSIONS, {"commits": []})  # its own copy excludes nothing
        bad = self.pr("forged", forged)
        later = self.pr("other", lambda: (self.repo / "README").write_text("x\n"))
        self.assertEqual(self.anchor()[0], bad)
        quarantine = self.pr("exclude", lambda: dump(self.repo / step.EXCLUSIONS,
                                                     {"commits": [{"commit": bad, "why": "flagged direct push"}]}))
        self.assertEqual(self.anchor()[0], later)
        self.assertNotEqual(quarantine, bad)
        (self.repo / step.EXCLUSIONS).write_text("not json")
        self.pr("break", lambda: (self.repo / step.EXCLUSIONS).write_text("not json\n"))
        self.assertIsNone(self.anchor()[0])

    def test_an_exclusion_that_is_not_a_full_commit_id_makes_the_file_unreadable(self):
        merged = self.pr("findings", self.write_step)
        self.pr("exclude", lambda: dump(self.repo / step.EXCLUSIONS, {"commits": [{"commit": merged},
                                                                                  {"commit": merged[:12]}]}))
        self.assertEqual(self.anchor(), (None, f"{step.EXCLUSIONS} on origin/main is not readable"))

    def test_a_file_deleted_from_a_published_step_or_added_to_it_loses_the_anchor(self):
        def two():
            self.write_step()
            (self.step / "more.json").write_text("{}\n")
        merged = self.pr("findings", two)
        (self.step / "more.json").unlink()
        self.assertIsNone(self.anchor()[0])
        (self.step / "more.json").write_text("{}\n")
        self.assertEqual(self.anchor()[0], merged)
        (self.step / "extra.json").write_text("{}\n")
        self.assertIsNone(self.anchor()[0])

    def test_a_file_name_git_would_quote_is_still_anchored(self):
        merged = self.pr("findings", lambda: (self.write_step(), (self.step / "café \"x\".json").write_text("{}\n")))
        self.assertEqual(self.anchor(), (merged, None))

    def test_a_foreign_origin_a_local_main_or_a_shallow_clone_gives_no_anchor(self):
        self.pr("findings", self.write_step)
        self.git("remote", "set-url", "origin", "https://github.com/someone/loop.git")
        self.assertIn("origin is not", self.anchor()[1])
        self.git("remote", "set-url", "origin", "git@github.com:evidence-network/loop.git")
        self.git("update-ref", "-d", "refs/remotes/origin/main")
        self.assertIn("no origin/main", self.anchor()[1])  # local main alone never supplies one
        self.sync()
        self.assertIsNotNone(self.anchor()[0])
        shallow = Path(self.tmp.name) / "shallow"
        subprocess.run(["git", "clone", "-q", "--depth", "2", f"file://{self.repo}", str(shallow)], check=True)
        self.git("remote", "set-url", "origin", "https://github.com/evidence-network/loop", repo=shallow)
        why = step.publication(shallow / "findings" / "2026-10-31-live" / "01-seal", root=shallow)[1]
        self.assertIn("shallow", why)

    def test_a_step_outside_findings_or_holding_a_symlink_has_no_anchor(self):
        self.pr("findings", self.write_step)
        os.symlink("capsule.json", self.step / "link.json")
        self.assertIsNone(self.anchor()[0])
        os.unlink(self.step / "link.json")
        self.assertIsNotNone(self.anchor()[0])
        elsewhere = self.repo / "lab" / "x" / "01-seal"
        self.write_step(elsewhere)
        self.commit("lab")
        self.assertIsNone(self.anchor(elsewhere)[0])

    def test_an_input_the_anchor_predates_is_empty(self):
        merged = self.pr("findings", self.write_step)
        env = step.inputs_at(merged, Path(self.tmp.name) / "in", root=self.repo)
        self.assertFalse(Path(env["LOOP_WITNESS_LISTING"]).exists())  # lab/witness/ came later
        self.assertEqual(list(Path(env["LOOP_LAB_RUNS"]).iterdir()), [])

    def test_join_commitments_are_read_from_the_anchors_lab_runs(self):
        runs = Path(self.tmp.name) / "runs"
        dump((runs / "r" / "join" / "n").mkdir(parents=True) or runs / "r" / "join" / "n" / "commitment.json",
             {"key_id": "k"})
        member = mock.Mock(checkpoint_member=lambda doc: ("book", "key"))
        with mock.patch.dict(os.environ, {"LOOP_LAB_RUNS": str(runs)}), \
                mock.patch.object(step.cc, "verify", return_value=([], [])), \
                mock.patch.object(step, "step_module", return_value=member):
            self.assertEqual(step.join_commitment_books(), {"k": {("book", "key")}})

    def test_verify_run_reads_each_steps_inputs_at_its_anchor_and_names_a_step_without_one(self):
        def inputs(name):
            (self.repo / "lab" / "witness").mkdir(parents=True, exist_ok=True)
            (self.repo / "lab" / "witness" / "witnesses.json").write_text(name + "\n")
            (self.repo / "lab" / "runs" / name).mkdir(parents=True, exist_ok=True)
            (self.repo / "lab" / "runs" / name / "x").write_text("x\n")
        merged = self.pr("findings", lambda: (self.write_step(), inputs("then")))
        self.key("22")
        inputs("now")  # a witness listing and a join re-issued after publication
        self.commit("rotate")
        self.sync()
        (self.run / "06-settle").mkdir()
        write_run_json(self.run, ["08"], "live")
        seen = {}

        def verify_step(d, pop_dir, population, tmp, anchor=None):
            listing = Path(os.environ.get("LOOP_NODES_DIR", self.repo / "nodes")) / f"{self.NODE}.json"
            seen[d.name] = (anchor, load(listing)["key_id"])
            if anchor:  # what the step modules read at import: 02's witness listing, 07's lab/runs
                seen["witness"] = step.step_module("02-book").LISTING.read_text()
                seen["runs"] = sorted(p.name for p in step.step_module("07-ask").LAB_RUNS.iterdir())
            return {"dir": d.name, "step": d.name[:2], "status": "reproduced", "relies_on": []}
        with mock.patch.object(step, "ROOT", self.repo), mock.patch.object(step, "verify_step", verify_step), \
                mock.patch.dict(os.environ):
            for k in ("LOOP_NODES_DIR", "LOOP_LAB_RUNS", "LOOP_WITNESS_LISTING"):
                os.environ.pop(k, None)
            entries = step.verify_run(self.run, self.run / "08-verify", POPULATION, "live")
            self.assertNotIn("LOOP_NODES_DIR", os.environ)  # put back after each step
            self.assertEqual(seen["01-seal"], (merged, "11" * 32))
            self.assertEqual((seen["witness"], seen["runs"]), ("then\n", ["then"]))
            self.assertEqual(seen["06-settle"], (None, "22" * 32))  # 06 holds nothing to a key: no anchor, no gap
            self.assertEqual([e["relies_on"] for e in entries], [[], []])
            (self.step / "capsule.json").write_text("forged\n")
            entries = step.verify_run(self.run, self.run / "08-verify", POPULATION, "live")
        self.assertEqual(seen["01-seal"], (None, "22" * 32))
        gap = entries[0]["relies_on"]
        self.assertEqual([(g["what"], g["issue"]) for g in gap], [(f"step 01: {step.ANCHOR_WHAT}", step.TRUST_ISSUE)])


class Step08RevocationsAnchorTest(unittest.TestCase):
    """loop#281: the revocations a live 05 or 12 read are held to main outside the run, never to its own start."""

    def git(self, *a, date=None):
        env = dict(os.environ, **({"GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date} if date else {}))
        return subprocess.run(["git", "-C", str(self.repo), "-c", "user.name=t", "-c", "user.email=t@t.invalid", "-c",
                               "commit.gpgsign=false", *a], capture_output=True, text=True, check=True,
                              env=env).stdout.strip()

    def commit(self, date, files=None):
        """Commit the revocations folder with `files` ({name: text, or None to remove}) on main, as if pushed."""
        folder = self.repo / "nodes" / "revocations"
        for name, text in (files or {}).items():
            folder.mkdir(parents=True, exist_ok=True)
            (folder / name).unlink() if text is None else (folder / name).write_text(text)
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", date, date=date)
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        return self.git("rev-parse", "HEAD")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.repo = t / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "-b", "main")
        self.c = self.commit("2026-10-01T00:00:00Z")
        # The step: a nodes.json proof file naming C, and a judge whose verdict is the list it reads.
        self.bundle = t / "run" / "05-deal"
        self.bundle.mkdir(parents=True)
        dump(self.bundle / "nodes.json", {"revocations_main": {"commit": self.c}})
        (self.bundle / "lab-a").mkdir()
        dump(self.bundle / "lab-a" / "checks.json", {"written": "by the judge"})
        self.work = t / "work" / "05-deal"
        shutil.copytree(self.bundle, self.work)
        self.pop = t / "pop"
        self.pop.mkdir()
        dump(self.pop / "population.json", {"population": "live", "nodes": []})
        self.read = []  # (out dir, commit) each judge run read
        self.verdicts = {}  # commit -> result; PASS otherwise

        def judge_live(pop, out, started):
            commit = load(Path(out) / "nodes.json")["revocations_main"]["commit"]
            self.read.append((Path(out), commit))
            self.assertFalse((Path(out) / "lab-a" / "checks.json").exists())  # re-written files are left out of the copy
            return {"result": self.verdicts.get(commit, "PASS"),
                    "not_checked": [{"what": "w", "why": f"read at {commit}", "issue": "i"}]}
        import binding_verify
        self.m = type("M", (), {"revocations_root": staticmethod(lambda: self.repo), "bv": binding_verify,
                                "REVOCATIONS_KEY": "revocations_main", "judge_live": staticmethod(judge_live)})
        record = {"population": "live", "started": "2026-10-31T00:00:00Z", "result": "PASS",
                  "not_checked": [{"what": "w", "why": f"read at {self.c}", "issue": "i"}],
                  "proof": [{"path": "nodes.json"}, {"path": "lab-a/checks.json"}]}
        self.ctx = step.Ctx(self.bundle, self.work, t / "scratch", self.pop, record)
        (t / "scratch").mkdir()

    def anchor(self, snap="kept"):
        return step.revocations_anchor(self.m, self.ctx, "05", {"commit": self.c} if snap == "kept" else snap)

    def test_no_kept_commit_has_nothing_to_compare(self):
        for snap in (None, {"commit": None, "why": "origin/main could not be fetched"}):
            self.assertIsNone(self.anchor(snap))
        self.assertEqual(self.ctx.relies, [])

    def test_a_kept_commit_that_is_no_commit_id_is_a_mismatch(self):
        for bad in ("--output=x", "ab" * 19, 7):
            with self.assertRaises(step.Mismatch):
                self.anchor({"commit": bad})

    def test_a_kept_commit_this_clones_main_does_not_hold_is_not_reproduced(self):
        with self.assertRaises(step.Unreproducible) as e:
            self.anchor({"commit": "ab" * 20})
        self.assertIn("fetch main's history", e.exception.args[1])

    def test_the_same_list_at_this_clones_main_needs_no_second_run_and_names_the_read(self):
        tip = self.commit("2026-11-03T00:00:00Z")  # main moved on, no folder at either commit
        self.assertIsNone(self.anchor())
        [gap] = self.ctx.relies
        self.assertEqual(gap["what"], f"step 05: {step.ANCHOR_REVOKED}")  # verify_run folds it into ANCHOR_WHAT's
        self.assertIn(f"origin/main as last fetched, {tip}, committed 2026-11-03T00:00:00", gap["why"])
        self.assertEqual(gap["issue"], step.REVOCATIONS_ISSUE)
        self.c = self.commit("2026-11-04T00:00:00Z", {"x.json": "[]"})
        self.commit("2026-11-05T00:00:00Z")  # the folder present and unchanged at both
        self.assertIsNone(self.anchor())

    def test_a_list_main_changed_since_the_kept_commit_is_judged_again_there(self):
        tip = self.commit("2026-11-03T00:00:00Z", {"x.json": "[]"})  # absent at C, present at main: the first one
        self.assertEqual(self.anchor(), tip)
        self.commit("2026-11-04T00:00:00Z", {"x.json": None})  # removed again: the same tree as C
        self.assertIsNone(self.anchor())

    def test_a_kept_commit_newer_or_older_than_the_runs_start_is_not_itself_a_mismatch(self):
        # Only the list decides: an older C with the same tree as main is reproduced at C.
        self.c = self.commit("2026-12-01T00:00:00Z")
        self.ctx.record["started"] = "2026-10-15T00:00:00Z"
        self.assertIsNone(self.anchor())

    def test_the_second_run_reads_the_anchor_on_its_own_copy(self):
        tip = self.commit("2026-11-03T00:00:00Z", {"x.json": "[]"})
        step.judge_at_anchor(self.m, self.ctx, "05", tip)  # same result; the why naming the commit may differ
        [(out, read)] = self.read
        self.assertEqual(read, tip)
        self.assertNotEqual(out, self.work)
        self.assertEqual(load(self.work / "nodes.json")["revocations_main"]["commit"], self.c)  # untouched
        self.assertEqual(load(self.work / "lab-a" / "checks.json"), {"written": "by the judge"})

    def test_a_different_result_at_the_anchor_is_a_mismatch_either_way(self):
        tip = self.commit("2026-11-03T00:00:00Z", {"x.json": "[]"})
        self.verdicts[tip] = "FAIL"  # a statement merged since applies
        with self.assertRaises(step.Mismatch) as e:
            step.judge_at_anchor(self.m, self.ctx, "05", tip)
        self.assertIn(f"as main held it at {tip[:12]}", str(e.exception))
        self.assertIn(": FAIL, not_checked ", str(e.exception))
        self.assertIn(f"; recorded PASS, not_checked ", str(e.exception))
        self.verdicts[tip], self.ctx.record["result"] = "PASS", "FAIL"  # one C's run applied was removed since
        with self.assertRaises(step.Mismatch):
            step.judge_at_anchor(self.m, self.ctx, "05", tip)

    def test_a_different_gap_at_the_anchor_is_a_mismatch(self):
        tip = self.commit("2026-11-03T00:00:00Z", {"x.json": "[]"})
        self.ctx.record["not_checked"] = []
        with self.assertRaises(step.Mismatch):
            step.judge_at_anchor(self.m, self.ctx, "05", tip)

    def test_a_publication_is_the_anchor_and_needs_no_gap(self):
        pub = self.commit("2026-11-03T00:00:00Z", {"x.json": "[]"})
        self.commit("2026-11-04T00:00:00Z", {"x.json": None})  # main's tip has C's tree again; the publication not
        self.ctx.anchor = pub
        self.assertEqual(self.anchor(), pub)
        self.assertEqual(self.ctx.relies, [])
        self.ctx.anchor = "cd" * 20  # a publication the revocations repository does not hold
        with self.assertRaises(step.Unreproducible):
            self.anchor()


if __name__ == "__main__":
    unittest.main()

