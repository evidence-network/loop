"""The Goose lab driver (lab/goose.sh, scripts/goose_live.py, evidence-network/loop#349): what it refuses and prints
without Goose tools, and, where steps/goose.setup.sh has installed them, one session on persistent homes judged live.

Run: python3 -m unittest tests.test_goose_live -v
"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOOSE_SH = ROOT / "lab" / "goose.sh"
TOOLS = Path(os.environ.get("LOOP_TOOLS", Path.home() / ".cache" / "evidence-network-loop"))


def goose(*args, **env):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("LAB_", "LOOP_GOOSE"))}
    e.update(env)
    return subprocess.run([str(GOOSE_SH), *args], stdin=subprocess.DEVNULL, capture_output=True, text=True, env=e)


class DriverArgumentsTest(unittest.TestCase):
    def test_it_needs_a_lab_dir_and_a_known_command(self):
        r = goose("run")
        self.assertEqual(r.returncode, 2)
        self.assertIn("set LAB_DIR", r.stderr)
        with tempfile.TemporaryDirectory() as lab:
            self.assertEqual(goose("fly", LAB_DIR=lab).returncode, 2)

    def test_dry_run_prints_the_plan_and_changes_nothing(self):
        with tempfile.TemporaryDirectory() as lab:
            r = goose("--dry-run", "run", LAB_DIR=lab, LOOP_TOOLS=lab)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("goose_live.py session", r.stdout)
            self.assertIn("--ecosystem goose --population live", r.stdout)
            self.assertEqual(sorted(p.name for p in Path(lab).iterdir()), [])

    def test_run_refuses_while_the_lab_is_held(self):
        with tempfile.TemporaryDirectory() as lab:
            python = Path(lab) / "goose" / "venv" / "bin" / "python"
            python.parent.mkdir(parents=True)
            python.write_text("#!/bin/sh\n")
            python.chmod(0o755)
            (Path(lab) / "goose").mkdir(exist_ok=True)
            (Path(lab) / "goose" / "ids.json").write_text("{}")
            (Path(lab) / "run").mkdir()
            (Path(lab) / "run" / "LIVE_RUN").write_text("held")
            r = goose("run", LAB_DIR=lab, LOOP_TOOLS=lab)
            self.assertEqual(r.returncode, 2)
            self.assertIn("no aac-go verifier", r.stderr)  # loop#358: refused before step 01 could be NOT_CHECKED
            verifier = Path(lab) / "01-seal" / "bin" / "cross_language"
            verifier.parent.mkdir(parents=True)
            verifier.write_text("#!/bin/sh\n")
            verifier.chmod(0o755)
            r = goose("run", LAB_DIR=lab, LOOP_TOOLS=lab)
            self.assertEqual(r.returncode, 2)
            self.assertIn("LIVE_RUN exists", r.stderr)
            self.assertTrue((Path(lab) / "run" / "LIVE_RUN").is_file())  # the other run's lock is not ours to remove


class ReporterListTest(unittest.TestCase):
    def test_the_rehearsal_confidence_list_leaves_out_nodes_the_mesh_reporter_does_not_ask(self):
        import sys
        sys.path.insert(0, str(ROOT / "scripts"))
        import rehearsal_confidence as confidence
        with tempfile.TemporaryDirectory() as d:
            for node, route in (("a" * 64, "mesh"), ("b" * 64, "nostr")):
                (Path(d) / f"{node}.json").write_text(json.dumps({"node_id": node, "ask_route": route}))
            self.assertEqual(sorted(confidence.listed(d)), ["a" * 64])


class LoopShGooseLiveTest(unittest.TestCase):
    def test_loop_sh_needs_the_population_the_driver_wrote(self):
        with tempfile.TemporaryDirectory() as out:
            r = subprocess.run([str(ROOT / "loop.sh"), "--stage", "1", "--ecosystem", "goose", "--population", "live",
                                "--out", out], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                               env={k: v for k, v in os.environ.items() if k != "LOOP_GOOSE_POPULATION"})
            self.assertEqual(r.returncode, 2)
            self.assertIn("LOOP_GOOSE_POPULATION", r.stderr)


@unittest.skipUnless((TOOLS / "goose" / "venv" / "bin" / "python").is_file(), "run steps/goose.setup.sh to install the Goose tools")
class SessionTest(unittest.TestCase):
    """init and session on scratch homes; no node of the repository's nodes/ is listed, so only what the session
    wrote is read."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.lab = Path(cls.tmp.name)
        env = {"LAB_DIR": str(cls.lab), "LOOP_TOOLS": str(TOOLS)}
        cls.init = goose("init", **env)
        assert cls.init.returncode == 0, cls.init.stderr
        cls.ids = json.loads((cls.lab / "goose" / "ids.json").read_text())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_init_keeps_the_keys_private_and_prints_only_public_ids(self):
        self.assertEqual(sorted(self.ids), ["goose-1", "goose-2"])
        for name, d in self.ids.items():
            self.assertRegex(d["node_id"], r"^[0-9a-f]{64}$")
            self.assertTrue((self.lab / "goose" / "keys" / name / "node-key.pub.pem").is_file())
            self.assertFalse((self.lab / "goose" / "keys" / name / "node-key.pem").exists())
            self.assertEqual(oct((self.lab / "goose" / "homes" / name / "producer.key").stat().st_mode & 0o777), "0o600")
        self.assertNotIn("PRIVATE", self.init.stdout + self.init.stderr)
        self.assertEqual(oct((self.lab / "goose" / "homes").stat().st_mode & 0o777), "0o700")

    def test_init_again_keeps_the_identities(self):
        again = goose("init", LAB_DIR=str(self.lab), LOOP_TOOLS=str(TOOLS))
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(json.loads((self.lab / "goose" / "ids.json").read_text()), self.ids)

    def drive(self, *args):
        py = TOOLS / "goose" / "venv" / "bin" / "python"
        r = subprocess.run([str(py), "-B", str(ROOT / "scripts" / "goose_live.py"), *args], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True,
                           env={**os.environ, "LOOP_GOOSE_SRC": str(TOOLS / "goose" / "src")})
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        return r

    def test_a_session_joins_nobody_again_and_judges_01_and_07_with_the_listed_key(self):
        lab = self.lab / "goose"
        pub = self.lab / "published"
        self.drive("advertise", "--homes", str(lab / "homes"), "--out", str(pub))
        work = self.lab / "work"
        self.drive("session", "--homes", str(lab / "homes"), "--keys", str(lab / "keys"), "--ids", str(lab / "ids.json"),
                   "--published", str(pub), "--work", str(work))
        pop_dir = work / "population"
        (work / "session.log").write_text("the log lab/goose.sh tees\n")
        pop = json.loads((pop_dir / "population.json").read_text())
        self.assertEqual(pop["population"], "live")
        self.assertEqual([n["node_id"] for n in pop["nodes"]], [self.ids[n]["node_id"] for n in ("goose-1", "goose-2")])
        self.assertEqual(pop["goose"]["steps"], ["01", "03", "07"])
        # the asks used the first join record, not a new one
        nodes = json.loads((work / "asks" / "nodes.json").read_text())
        for name, d in nodes.items():
            self.assertEqual(d["first_record"], self.ids[name]["record_id"])
        # step 07 judges the asks
        out7 = self.lab / "07-ask"
        r = subprocess.run([str(ROOT / "steps/07-ask"), "--population", str(pop_dir), "--out", str(out7)],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        found7 = json.loads((out7 / "finding.json").read_text())
        self.assertEqual(found7["result"], "PASS")
        self.assertIn("wss://relay.loop-lab.example) is the lab's, reachable only from the lab host", found7["detail"])
        self.assertIn("(PT1M) holds during a run only", found7["detail"])
        self.assertIn("the askers are the lab's persistent nodes, no deal to compare them with", found7["detail"])
        # step 01 holds the sealing node to the key nodes/<node_id>.json lists
        if not (TOOLS / "01-seal").is_dir():
            self.skipTest("run steps/01-seal.setup.sh for the step 01 half")
        for listed_key, want in ((self.ids["goose-2"]["key_id"], "PASS"), ("0" * 64, "FAIL")):
            nd = self.lab / f"nodes-{want}"
            nd.mkdir()
            (nd / f"{self.ids['goose-2']['node_id']}.json").write_text(json.dumps({"key_id": listed_key}))
            out1 = self.lab / want.lower() / "01-seal"
            r = subprocess.run([str(ROOT / "steps/01-seal"), "--population", str(pop_dir), "--out", str(out1)],
                               stdin=subprocess.DEVNULL, capture_output=True, text=True,
                               env={**os.environ, "LOOP_NODES_DIR": str(nd)})
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])
            found = json.loads((out1 / "finding.json").read_text())
            self.assertEqual(found["result"], want, found["detail"])
            if want == "PASS":
                self.assertIn("placeholder model (rehearsal/rehearsal-model), not a real inference", found["detail"])
