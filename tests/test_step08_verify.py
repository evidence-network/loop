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
        with mock.patch.object(step, "keys_09", return_value=[]):
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
        sd = self.run / "12-join"
        sd.mkdir()
        (sd / "alice").mkdir()
        shutil.copyfile(self.run / "03-advertise" / "alice.commitment.json", sd / "alice" / "commitment.json")
        finding.write(sd, result="PASS", population="rehearsal", asserts="a join", pinned=[],
                      proof=["alice/commitment.json"], detail="", started=finding.now())
        verdict, entries = verify(self.run)
        self.assertEqual(verdict["result"], "NOT_CHECKED", verdict["detail"])
        self.assertEqual(entries["12-join"]["status"], "not_reproduced")
        self.assertIsNone(entries["12-join"]["rederived"])
        [nc] = verdict["not_checked"]
        self.assertEqual(sorted(nc), ["issue", "what", "why"])
        self.assertEqual(nc["issue"], "evidence-network/loop#24")
        self.assertIn("probe/stranger", nc["why"])
        self.assertIn("12-join: recorded PASS, not reproduced", verdict["detail"])
        self.assertEqual(finding.check(self.run), [])
        # Mutant: a 08 that took step 12's PASS on trust would pass the run.
        trust = (lambda m, c: c.record, step.STEPS["12"][1])
        with mock.patch.dict(step.STEPS, {"12": trust}):
            verdict, _ = verify(self.run)
        self.assertEqual(verdict["result"], "PASS")

    def test_a_step_whose_tool_is_missing_is_not_checked(self):
        sd = self.run / "05-deal"
        sd.mkdir()
        (sd / "deal.json").write_text("{}\n")
        (sd / "declarations.json").write_text("[]\n")
        finding.write(sd, result="PASS", population="rehearsal", asserts="a deal", pinned=[],
                      proof=["deal.json", "declarations.json"], detail="", started=finding.now())
        with mock.patch.dict(os.environ, {"LOOP_STEP05_DEAL_BIN": str(Path(self.tmp.name) / "none")}):
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
        (sd / "recorded.json").write_text("{}\n")
        finding.write(sd, result="NOT_CHECKED", population="live", asserts="a settlement", pinned=[],
                      proof=["recorded.json"], detail="", started=finding.now(),
                      not_checked=[{"what": "x", "why": "y", "issue": "evidence-network/loop#24"}])
        out = live / "08-verify"
        step.main(["--population", str(ROOT / "population" / "live"), "--out", str(out)])
        f = load(out / "finding.json")
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertEqual(f["population"], "live")
        self.assertIn("live result", f["not_checked"][0]["what"])

    def test_an_empty_run_is_not_checked(self):
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        step.main(["--population", str(POPULATION), "--out", str(empty / "08-verify")])
        f = load(empty / "08-verify" / "finding.json")
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertEqual(finding.check(empty), [])


@unittest.skipUnless(HAS_BASE and HAS_GO, NO_GO)
class Step08NotCheckedListTest(unittest.TestCase):
    """A recorded not_checked list is reproduced entry for entry: step 01's FAIL carries one."""

    def test_a_dropped_not_checked_entry_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "run"
            run_step("01-seal", Path(tmp) / "sealed")  # sealed with alice's repository key
            capsule = load(Path(tmp) / "sealed" / "01-seal" / "capsule.json")
            capsule["effect"]["effect_attestation"] = "gate_executed"  # a class an in-process seal cannot have
            dump(Path(tmp) / "planted.json", capsule)
            run_step("01-seal", run, "--sealed", str(Path(tmp) / "planted.json"))
            f = load(run / "01-seal" / "finding.json")
            self.assertEqual(f["result"], "FAIL")
            self.assertEqual(len(f["not_checked"]), 1)
            verdict, entries = verify(run)
            self.assertEqual(entries["01-seal"]["status"], "reproduced", verdict["detail"])
            f["not_checked"] = []
            dump(run / "01-seal" / "finding.json", f)
            verdict, entries = verify(run)
            self.assertEqual(verdict["result"], "FAIL")
            self.assertIn("01-seal: did not hold: recorded not_checked [], re-derived", verdict["detail"])
            # Mutant: with the not_checked comparison off, the dropped entry would go through.
            with mock.patch.object(step, "canon", return_value=[]):
                verdict, _ = verify(run)
            self.assertEqual(verdict["result"], "PASS")


@unittest.skipUnless(HAS_ALL, NO_ALL)
class Step08RehearsalTest(unittest.TestCase):
    """08 on a rehearsal run of every existing step: as steps/08-verify.md's table says."""

    def test_every_step_but_12_is_reproduced(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "2026-10-30-rehearsal"
            for base in ("01-seal", "02-book", "03-advertise", "04-admit", "05-deal", "06-settle", "07-ask",
                         "09-dispute", "12-join"):
                run_step(base, run)
            run_step("08-verify", run)
            f = load(run / "08-verify" / "finding.json")
            entries = {e["dir"]: e for e in load(run / "08-verify" / "verified.json")["steps"]}
            self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
            self.assertEqual({d: e["status"] for d, e in entries.items()},
                             {**{d: "reproduced" for d in entries if d != "12-join"}, "12-join": "not_reproduced"})
            self.assertEqual(len(entries), 9)
            self.assertEqual(subprocess.run([sys.executable, "-B", str(STEPS / "finding.py"), "check", str(run)]
                                            ).returncode, 0)


if __name__ == "__main__":
    unittest.main()
