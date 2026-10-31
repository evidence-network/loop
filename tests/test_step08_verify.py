"""steps/08-verify: every finding of a run reproduced offline from its bundle, or said not to be.

The base run is steps 03, 06 and 09 on the rehearsal population (python3, git and openssl only); without them
the tests SKIP, saying so, and LOOP_REQUIRE_TOOLS=1 makes that an error. Each planted failure edits a copy of
the base run, and its mutant check shows which part of 08 catches it: with that part of 08 switched off, the
plant goes through.
"""
import importlib.machinery
import importlib.util
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
HAS_BASE = bool(shutil.which("git") and shutil.which("openssl"))
NO_BASE = "SKIPPED LOUDLY: git and openssl are needed to run steps 03, 06 and 09 for the base run"
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
        sd = live / "06-settle"
        sd.mkdir(parents=True)
        write_run_json(live, ["06", "08"], "live")
        (sd / "recorded.json").write_text("{}\n")
        finding.write(sd, result="NOT_CHECKED", population="live", asserts="a settlement", pinned=[],
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
        self.assertEqual({nc["issue"] for nc in f["not_checked"]}, {step.RUN_ISSUE, step.TRUST_ISSUE})
        self.assertEqual([nc for nc in f["not_checked"] if nc["issue"] == step.TRUST_ISSUE], step.LIVE_RELIES_ON["07"])
        self.assertEqual(finding.check(self.run), [])

    def test_live_10_and_11_then_read_not_checked_on_the_window(self):
        self.verify()
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
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
        # 07 is reproduced, so it is no refused step; its asks finished in the open month, outside the window.
        self.assertTrue(any("07-ask" in x and "outside the window" in x for x in window["refused"]), window["refused"])
        self.assertEqual({nc["issue"] for nc in f10["not_checked"]}, {judge10.WINDOW_ISSUE})
        self.assertEqual({nc["issue"] for nc in f11["not_checked"]}, {judge10.WINDOW_ISSUE})
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

    def test_first_answered_the_proof_does_not_give_fails(self):
        def forge(d):
            fa = load(d / "first-answered.json")
            fa["answered_at"] = "2026-10-30T23:01:20.000000Z"
            dump(d / "first-answered.json", fa)
        f, entries = self.plant(forge)
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("first-answered.json re-derived is not the recorded first-answered.json", f["detail"])


if __name__ == "__main__":
    unittest.main()
