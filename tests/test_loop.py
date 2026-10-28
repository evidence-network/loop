"""loop.sh: every step ends with a valid finding, and a broken step is a FAIL, not a silence."""
import datetime as dt
import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOOP = ROOT / "loop.sh"
POPULATION = ROOT / "population"

# A fake step: writes a finding through steps/finding.py, or misbehaves as told.
STEP = textwrap.dedent("""\
    #!/usr/bin/env python3
    import argparse, json, sys, time
    sys.path.insert(0, {steps!r})
    import finding
    a = argparse.ArgumentParser()
    a.add_argument("--population"); a.add_argument("--out")
    args = a.parse_args()
    started = finding.now()
    how = {how!r}
    if how == "crash":
        print("the step broke here", file=sys.stderr)
        sys.exit(3)
    if how == "hang":
        time.sleep(30)
    if how == "malformed":
        open(args.out + "/finding.json", "w").write(json.dumps({{"result": "PASS"}}))
        sys.exit(0)
    if how == "silent":
        sys.exit(0)
    population = json.load(open(args.population + "/population.json"))["population"]
    if how == "other-population":
        population = "live" if population == "rehearsal" else "rehearsal"
    open(args.out + "/proof.txt", "w").write("proof bytes\\n")
    finding.write(args.out, result="PASS", population=population, asserts="a fake fact holds",
                  pinned=["capsulectl"], proof=["proof.txt"], detail="", started=started)
""")


class LoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.steps = self.dir / "steps"
        self.steps.mkdir()
        self.out = self.dir / "out"

    def tearDown(self):
        self.tmp.cleanup()

    def step(self, name, how="pass", executable=True):
        f = self.steps / name
        f.write_text(STEP.format(steps=str(ROOT / "steps"), how=how))
        f.chmod(0o755 if executable else 0o644)

    def all_steps(self, **how):
        for name in ("01-seal", "02-book", "03-advertise", "12-join"):
            if how.get(name) != "missing":
                self.step(name, how.get(name, "pass"))

    def loop(self, *args, env=None):
        e = dict(os.environ, LOOP_STEPS_DIR=str(self.steps), LOOP_FINDINGS=str(self.dir / "findings"))
        e.update(env or {})
        return subprocess.run([str(LOOP), "--stage", "1", *args], capture_output=True, text=True, env=e,
                              timeout=120)

    def finding(self, base, out=None):
        return json.loads(((out or self.out) / base / "finding.json").read_text())

    def test_good_steps_pass_check_clean_and_are_indexed(self):
        self.all_steps()
        r = self.loop("--out", str(self.out))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["01 seal PASS", "02 book PASS", "03 advertise PASS", "12 join PASS"])
        index = (self.out / "index.md").read_text()
        for base in ("01-seal", "02-book", "03-advertise", "12-join"):
            self.assertIn(f"]({base}/finding.json)", index)
            self.assertIn(f"]({base}/proof.txt)", index)
        self.assertEqual(subprocess.run(["python3", "-B", str(ROOT / "steps/finding.py"), "check",
                                         str(self.out)]).returncode, 0)

    def test_a_missing_step_is_a_fail_finding(self):
        self.all_steps(**{"02-book": "missing"})
        r = self.loop("--out", str(self.out))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("02 book FAIL", r.stdout.splitlines())
        f = self.finding("02-book")
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("no step executable", f["detail"])
        self.assertEqual(self.finding("01-seal")["result"], "PASS")

    def test_files_beside_a_step_are_not_steps(self):
        self.all_steps()
        for name in ("01-seal.constraints.txt", "03-advertise.md", "12-join.setup.sh"):
            (self.steps / name).write_text("not a step\n")
        (self.steps / "02-book.d").mkdir()
        r = self.loop("--out", str(self.out))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["01 seal PASS", "02 book PASS", "03 advertise PASS", "12 join PASS"])

    def test_a_crashing_step_is_a_fail_finding_with_its_output(self):
        self.all_steps(**{"03-advertise": "crash"})
        r = self.loop("--out", str(self.out))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("03 advertise FAIL", r.stdout.splitlines())
        f = self.finding("03-advertise")
        self.assertIn("exited 3", f["detail"])
        self.assertEqual([p["path"] for p in f["proof"]], ["loop-step.log"])
        self.assertIn("the step broke here", (self.out / "03-advertise/loop-step.log").read_text())

    def test_a_step_that_is_not_executable_or_hangs_is_a_fail(self):
        self.all_steps(**{"01-seal": "missing", "12-join": "hang"})
        self.step("01-seal", executable=False)
        r = self.loop("--out", str(self.out), env={"LOOP_STEP_TIMEOUT": "2"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("not executable", self.finding("01-seal")["detail"])
        self.assertIn("did not finish", self.finding("12-join")["detail"])

    def test_a_malformed_finding_exits_1(self):
        self.all_steps(**{"02-book": "malformed"})
        r = self.loop("--out", str(self.out))
        self.assertEqual(r.returncode, 1)
        self.assertIn("02 book MALFORMED", r.stdout.splitlines())
        self.assertIn("02-book", r.stderr)

    def test_a_step_that_exits_0_without_a_finding_exits_1(self):
        self.all_steps(**{"01-seal": "silent"})
        r = self.loop("--out", str(self.out))
        self.assertEqual(r.returncode, 1)
        self.assertIn("01-seal: no finding.json", r.stderr)

    def test_a_finding_for_the_other_population_exits_1(self):
        self.all_steps(**{"12-join": "other-population"})
        r = self.loop("--out", str(self.out))
        self.assertEqual(r.returncode, 1)
        self.assertIn("population is live, this run is rehearsal", r.stderr)

    def test_rehearsal_and_live_never_share_a_directory(self):
        self.all_steps()
        rehearsal, live = self.loop(), self.loop("--population", "live")
        self.assertEqual((rehearsal.returncode, live.returncode), (0, 0), rehearsal.stderr + live.stderr)
        day = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
        found = self.dir / "findings"
        self.assertEqual(sorted(p.name for p in found.iterdir()), [f"{day}-live", f"{day}-rehearsal"])
        self.assertEqual(self.finding("01-seal", found / f"{day}-live")["population"], "live")
        self.assertEqual(self.finding("01-seal", found / f"{day}-rehearsal")["population"], "rehearsal")
        # A live run pointed at a rehearsal directory is refused before any step runs.
        r = self.loop("--population", "live", "--out", str(found / f"{day}-rehearsal"))
        self.assertEqual(r.returncode, 2)
        self.assertEqual(self.finding("01-seal", found / f"{day}-rehearsal")["population"], "rehearsal")

    def test_steps_selects_within_the_stage_only(self):
        self.all_steps()
        r = self.loop("--out", str(self.out), "--steps", "01,12")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.splitlines(), ["01 seal PASS", "12 join PASS"])
        self.assertEqual(self.loop("--out", str(self.out), "--steps", "04").returncode, 2)
        self.assertEqual(self.loop("--population", "staging").returncode, 2)


class PopulationTest(unittest.TestCase):
    def test_rehearsal_population_has_the_agreed_shape(self):
        p = json.loads((POPULATION / "rehearsal/population.json").read_text())
        self.assertEqual(p, {"population": "rehearsal", "rail": "mock", "nodes": [
            {"name": "alice", "key_dir": "population/rehearsal/keys/alice"},
            {"name": "bob", "key_dir": "population/rehearsal/keys/bob"}]})

    def test_live_population_names_no_secret_or_host(self):
        p = json.loads((POPULATION / "live/population.json").read_text())
        self.assertEqual(p["population"], "live")
        self.assertEqual(len(p["nodes"]), 3)
        for n in p["nodes"]:
            self.assertEqual(sorted(n), ["book_dir", "data_dir", "host", "name", "node_id"])
            self.assertEqual(n["host"], "lab")
            for path in (n["data_dir"], n["book_dir"]):  # null, or named from $LAB_DIR: never a host path
                self.assertTrue(path is None or path.startswith("$LAB_DIR/"), path)
        self.assertFalse(list((POPULATION / "live").glob("**/*.pem")))

    def test_rehearsal_keys_load_and_match_their_pub_pems(self):
        for name in ("alice", "bob"):
            key_dir = POPULATION / "rehearsal/keys" / name
            priv, pub = key_dir / "node-key.pem", key_dir / "node-key.pub.pem"
            try:
                from cryptography.hazmat.primitives import serialization
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
            except ImportError:
                if not shutil.which("openssl"):
                    self.skipTest("SKIPPED LOUDLY: neither the cryptography package nor openssl is here; "
                                  "the rehearsal keys were not loaded")
                derived = subprocess.run(["openssl", "pkey", "-in", str(priv), "-pubout"], capture_output=True,
                                         text=True, check=True).stdout
                text = subprocess.run(["openssl", "pkey", "-in", str(priv), "-noout", "-text"],
                                      capture_output=True, text=True, check=True).stdout
                self.assertIn("ED25519", text.upper())
                self.assertEqual(derived, pub.read_text())
                continue
            k = serialization.load_pem_private_key(priv.read_bytes(), password=None)
            p = serialization.load_pem_public_key(pub.read_bytes())
            self.assertIsInstance(k, Ed25519PrivateKey)
            self.assertIsInstance(p, Ed25519PublicKey)
            raw = serialization.Encoding.Raw, serialization.PublicFormat.Raw
            self.assertEqual(k.public_key().public_bytes(*raw), p.public_bytes(*raw))
            p.verify(k.sign(b"rehearsal"), b"rehearsal")

    def test_alice_and_bob_are_different_keys(self):
        a, b = (POPULATION / f"rehearsal/keys/{n}/node-key.pub.pem" for n in ("alice", "bob"))
        self.assertNotEqual(a.read_bytes(), b.read_bytes())


if __name__ == "__main__":
    unittest.main()
