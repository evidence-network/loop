"""Steps 01, 02, 03 and 12 on the live population: each node's own key and book, or NOT_CHECKED.

On a population with every data_dir, book_dir and node_id null (the file before the
lab run, kept as tests/fixtures/live/null) each step writes NOT_CHECKED with a `live node <name>` entry, never a crash or a FAIL,
and `loop.sh --stage 1 --population live` exits 0. With a fixture LAB_DIR (the
TEST keys of tests/fixtures/step02 as each node's plugin keys, and a book join.sh
makes here with them), step 01 seals with each node's key and PASSes, and step 02
appends to each node's book and gives the expected NOT_CHECKED (the witness part
only). The tests that need a real tool SKIP and say so when it is missing.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "steps"))
import finding  # noqa: E402
import live  # noqa: E402

LIVE = ROOT / "population" / "live"
FIXTURE = ROOT / "tests" / "fixtures" / "live" / "population.json"
NULL = ROOT / "tests" / "fixtures" / "live" / "null"
TEST_KEYS = ROOT / "tests" / "fixtures" / "step02" / "rehearsal" / "keys"
NODE_KEYS = {"lab-1": "alice", "lab-2": "bob"}  # TEST keys only: never a live node's
TOOLS = Path(os.environ.get("LOOP_TOOLS") or Path.home() / ".cache" / "evidence-network-loop")
SEAL_TOOLS = (TOOLS / "01-seal" / "venv" / "bin" / "python", TOOLS / "01-seal" / "bin" / "cross_language")
CAPSULECTL = TOOLS / "bin" / "capsulectl"


def need(tool, setup):
    if not tool.is_file():
        msg = f"SKIPPED LOUDLY: {tool} is not installed; run {setup} (needs network)"
        print(msg, file=sys.stderr)
        raise unittest.SkipTest(msg)


def run_step(step, population, out, env=None):
    r = subprocess.run([sys.executable, "-B", str(ROOT / "steps" / step), "--population", str(population),
                        "--out", str(out)], capture_output=True, text=True, env=env, cwd=ROOT)
    return r


class LiveCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.findings = self.tmp / "findings"

    def step(self, step, population=LIVE, env=None):
        out = self.findings / step
        r = run_step(step, population, out, env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(finding.check(self.findings), [])
        return json.loads((out / "finding.json").read_text())

    def assert_null_nodes(self, r, field="data_dir"):
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertEqual(r["population"], "live")
        for name in ("lab-1", "lab-2", "lab-3"):
            self.assertIn(live.not_checked(name, field), r["not_checked"])
            self.assertIn(name, r["detail"])

    def lab(self):
        """A fixture LAB_DIR: each node's plugin data dir holds its TEST key pair. Returns (population dir, env)."""
        lab = self.tmp / "lab"
        for name, test in NODE_KEYS.items():
            keys = lab / "nodes" / name / ".local" / "share" / "capsules" / "keys"
            keys.mkdir(parents=True)
            for f in ("node-key.pem", "node-key.pub.pem"):
                shutil.copy(TEST_KEYS / test / f, keys / f)
            os.chmod(keys / "node-key.pem", 0o600)
        pop = self.tmp / "population" / "live"
        pop.mkdir(parents=True)
        shutil.copy(FIXTURE, pop / "population.json")
        return pop, lab, dict(os.environ, LAB_DIR=str(lab))


class CommittedLiveTest(LiveCase):
    """The committed file names the three lab nodes, each listed in nodes/; the null file, no node.
    No step needs a tool to say so."""

    def test_committed_file_names_the_listed_lab_nodes(self):
        pop = json.loads((LIVE / "population.json").read_text())
        # The operator's labels; each node's directory on the lab host keeps its lab-N name.
        self.assertEqual([n["name"] for n in pop["nodes"]], ["stevenmih-1", "stevenmih-2", "stevenmih-3"])
        for i, n in enumerate(pop["nodes"], 1):
            self.assertRegex(n["node_id"], r"^[0-9a-f]{64}$")
            self.assertTrue((ROOT / "nodes" / f"{n['node_id']}.json").is_file(), n["name"])
            self.assertEqual(n["host"], "lab")
            self.assertTrue(n["data_dir"].startswith(f"$LAB_DIR/nodes/lab-{i}/"), n)
            self.assertTrue(n["book_dir"].startswith(f"$LAB_DIR/nodes/lab-{i}/"), n)

    def test_null_file_is_all_null(self):
        pop = json.loads((NULL / "population.json").read_text())
        for n in pop["nodes"]:
            self.assertEqual(n, {"name": n["name"], "node_id": None, "host": "lab", "data_dir": None,
                                 "book_dir": None})

    def test_01_null_data_dir_is_not_checked(self):
        self.assert_null_nodes(self.step("01-seal", NULL))

    def test_02_null_data_dir_is_not_checked(self):
        r = self.step("02-book", NULL)
        self.assert_null_nodes(r, "data_dir")
        self.assert_null_nodes(r, "book_dir")
        self.assertEqual(set(r["pinned"]), {"capsulectl", "capsulectl-book"})

    def test_03_is_not_checked(self):
        self.assertEqual(self.step("03-advertise", NULL)["result"], "NOT_CHECKED")

    def test_12_null_node_id_is_not_checked(self):
        # No node is asked, so no console is reached: an address nothing answers on.
        r = self.step("12-join", NULL, env=dict(os.environ, LOOP_CONSOLE="http://127.0.0.1:9"))
        self.assertEqual(r["result"], "NOT_CHECKED")
        for name in ("lab-1", "lab-2", "lab-3"):
            self.assertIn({"what": f"live node {name}", "why": "node_id not set, or no nodes/<node_id>.json",
                           "issue": live.ISSUE}, r["not_checked"])

    def test_unexpanded_lab_dir_is_not_checked(self):
        pop = self.tmp / "population" / "live"
        pop.mkdir(parents=True)
        shutil.copy(FIXTURE, pop / "population.json")
        env = {k: v for k, v in os.environ.items() if k != "LAB_DIR"}
        r = self.step("01-seal", pop, env)
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertIn(live.not_checked("lab-1"), r["not_checked"])

    def test_loop_stage_1_live_exits_0_with_no_fail(self):
        out = self.tmp / "loop"
        r = subprocess.run([str(ROOT / "loop.sh"), "--stage", "1", "--population", "live", "--out", str(out)],
                           capture_output=True, text=True, env=dict(os.environ, LOOP_CONSOLE="http://127.0.0.1:9"))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.split("\n")[:4],
                         ["01 seal NOT_CHECKED", "02 book NOT_CHECKED", "03 advertise PASS",
                          "12 join NOT_CHECKED"])
        self.assertNotIn("FAIL", r.stdout)


class LiveHelperTest(unittest.TestCase):
    def test_worst_over_the_nodes(self):
        self.assertEqual(live.worst(["PASS", "NOT_CHECKED", "PASS"]), "NOT_CHECKED")
        self.assertEqual(live.worst(["NOT_CHECKED", "FAIL"]), "FAIL")
        self.assertEqual(live.worst(["PASS", "PASS"]), "PASS")

    def test_lab_dir_is_expanded_and_unreadable_is_none(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "keys").mkdir()
            (Path(d) / "keys" / "node-key.pem").write_text("x")
            os.environ["LOOP_TEST_LAB"] = d
            try:
                node = {"data_dir": "$LOOP_TEST_LAB"}
                self.assertEqual(live.readable(node, "data_dir", "keys/node-key.pem"), Path(d))
                self.assertIsNone(live.readable(node, "data_dir", "keys/node-key.pub.pem"))
                self.assertIsNone(live.readable({"data_dir": None}, "data_dir"))
                self.assertIsNone(live.readable({}, "data_dir"))
            finally:
                del os.environ["LOOP_TEST_LAB"]


class Step01LiveTest(LiveCase):
    def setUp(self):
        super().setUp()
        for t in SEAL_TOOLS:
            need(t, "steps/01-seal.setup.sh")
        self.pop, self.lab_dir, self.env = self.lab()

    def publish(self, listed_keys=None):
        """Give each node a node_id and a nodes/<node_id>.json listing its TEST public key (or `listed_keys[name]`);
        returns the env that points the step at that directory."""
        import importlib.machinery, importlib.util
        loader = importlib.machinery.SourceFileLoader("step01_seal", str(ROOT / "steps" / "01-seal"))
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("step01_seal", loader))
        loader.exec_module(mod)
        pop = json.loads((self.pop / "population.json").read_text())
        nodes = self.tmp / "published-nodes"
        nodes.mkdir()
        for i, n in enumerate(pop["nodes"]):
            n["node_id"] = f"{i + 1:064x}"
            key = (listed_keys or {}).get(n["name"]) or mod.raw_public_hex(TEST_KEYS / NODE_KEYS[n["name"]] / "node-key.pub.pem")
            (nodes / f"{n['node_id']}.json").write_text(json.dumps({"node_id": n["node_id"], "key_id": key}))
        (self.pop / "population.json").write_text(json.dumps(pop))
        return dict(self.env, LOOP_NODES_DIR=str(nodes))

    def test_a_node_listing_another_key_fails(self):
        r = self.step("01-seal", self.pop, self.publish({"lab-2": "f" * 64}))
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("node lab-2: signature: authored under key", r["detail"])
        self.assertIn("the node publishes in nodes/", r["detail"])

    def test_each_node_seals_with_its_own_key_and_passes(self):
        r = self.step("01-seal", self.pop, self.publish())
        self.assertEqual(r["result"], "PASS", r["detail"])
        for name, test in NODE_KEYS.items():
            self.assertIn(f"node {name}: Class 1 ok", r["detail"])
            self.assertIn(f"{name}/capsule.json", [p["path"] for p in r["proof"]])
            capsule = json.loads((self.findings / "01-seal" / name / "capsule.json").read_text())
            checks = json.loads((self.findings / "01-seal" / name / "checks.json").read_text())
            self.assertTrue(checks["checks"]["key"]["ok"])
            self.assertIn(capsule["key_id"], checks["checks"]["key"]["detail"])
        # The producer signature is verified in Go, so a PASS leaves nothing outside its assertion.
        self.assertEqual(r["not_checked"], [])
        self.assertIn("producer signature authored under", r["detail"])
        self.assertTrue(all(json.loads((self.findings / "01-seal" / n / "checks.json").read_text())
                            ["checks"]["signature"]["state"] == "ok" for n in NODE_KEYS))

    def test_a_node_with_no_published_listing_is_not_checked(self):
        pop = json.loads((self.pop / "population.json").read_text())
        for i, n in enumerate(pop["nodes"]):
            n["node_id"] = f"{i:064x}" if i else None  # one has a node_id with no listing, one has no node_id
        (self.pop / "population.json").write_text(json.dumps(pop))
        nodes = self.tmp / "no-nodes"
        nodes.mkdir()
        r = self.step("01-seal", self.pop, dict(self.env, LOOP_NODES_DIR=str(nodes)))
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertEqual({n["what"] for n in r["not_checked"]},
                         {"the producer envelope signature, verified in a second language"})
        self.assertEqual(len(r["not_checked"]), len(NODE_KEYS))

    def test_a_node_with_another_nodes_public_key_fails(self):
        keys = self.lab_dir / "nodes" / "lab-2" / ".local" / "share" / "capsules" / "keys"
        shutil.copy(TEST_KEYS / "alice" / "node-key.pub.pem", keys / "node-key.pub.pem")
        r = self.step("01-seal", self.pop, self.env)
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("node lab-2: key:", r["detail"])
        self.assertIn("node lab-1: Class 1 ok", r["detail"])

    def test_one_null_node_makes_the_finding_not_checked_and_lists_both(self):
        pop = json.loads((self.pop / "population.json").read_text())
        pop["nodes"][1]["data_dir"] = None
        (self.pop / "population.json").write_text(json.dumps(pop))
        r = self.step("01-seal", self.pop, self.env)
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertIn("node lab-1: Class 1 ok", r["detail"])
        self.assertIn("node lab-2: not checked, data_dir not set or unreadable", r["detail"])
        self.assertIn(live.not_checked("lab-2"), r["not_checked"])
        self.assertNotIn(live.not_checked("lab-1"), r["not_checked"])


class Step02LiveTest(LiveCase):
    """Each node's book is made here by join.sh with its TEST key, as lab/lab.sh join makes it."""

    def setUp(self):
        super().setUp()
        need(CAPSULECTL, "steps/12-join.setup.sh")
        if shutil.which("openssl") is None:
            self.skipTest("SKIPPED LOUDLY: openssl is not installed; join.sh needs it")
        self.pop, self.lab_dir, self.env = self.lab()
        for name in NODE_KEYS:
            home = self.lab_dir / "nodes" / name
            ctl = self.tmp / f"{name}-controller.pem"  # a TEST controller key, made here
            subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(ctl)], check=True,
                           capture_output=True)
            r = subprocess.run([str(ROOT / "join.sh"), "--name", name, "--node-key",
                                str(home / ".local/share/capsules/keys/node-key.pem"), "--controller-key", str(ctl),
                                "--out", str(self.tmp / "join" / name), "--home", str(home / "join-book")],
                               capture_output=True, text=True, env=self.env)
            self.assertEqual(r.returncode, 0, r.stderr)
            (home / "join-book" / "node.seed").unlink()  # as lab/lab.sh join does

    def records(self, name):
        path = self.lab_dir / "nodes" / name / "join-book" / "book" / "book" / "records" / "records.jsonl"
        return [json.loads(ln) for ln in path.read_text().splitlines()]

    def test_each_node_appends_to_its_own_book_and_only_the_witness_is_not_checked(self):
        before = {name: len(self.records(name)) for name in NODE_KEYS}
        r = self.step("02-book", self.pop, self.env)
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        self.assertEqual([n["what"] for n in r["not_checked"]],
                         ["a witness receipt on the book's checkpoint, and its verification"])
        for name in NODE_KEYS:
            checks = json.loads((self.findings / "02-book" / name / "checks.json").read_text())["checks"]
            self.assertTrue(all(c["ok"] for c in checks.values()), checks)
            self.assertEqual(set(checks), {"bundle", "membership", "record", "position", "signer", "checkpoint",
                                           "node_key"})
            self.assertIn(f"node {name}: book {name}-book", r["detail"])
            self.assertGreater(len(self.records(name)), before[name])
            capsule = json.loads((self.findings / "02-book" / name / "capsule.json").read_text())
            self.assertIn(capsule["capsule_id"], [rec["header"].get("subject_ref") for rec in self.records(name)])
        self.assertFalse(list(self.findings.rglob("*.seed")), "the node key's seed copy stayed in the findings")

    def test_another_nodes_key_on_a_book_fails(self):
        # lab-2's data_dir now holds lab-1's key: it is not the key lab-2's book was bound to.
        keys = self.lab_dir / "nodes" / "lab-2" / ".local" / "share" / "capsules" / "keys"
        shutil.copy(TEST_KEYS / "alice" / "node-key.pem", keys / "node-key.pem")
        r = self.step("02-book", self.pop, self.env)
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("node lab-2", r["detail"])
        self.assertIn("node_key (node key", r["detail"])

    def test_a_null_book_dir_is_not_checked_for_that_node(self):
        pop = json.loads((self.pop / "population.json").read_text())
        pop["nodes"][0]["book_dir"] = None
        (self.pop / "population.json").write_text(json.dumps(pop))
        r = self.step("02-book", self.pop, self.env)
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertIn(live.not_checked("lab-1", "book_dir"), r["not_checked"])
        self.assertIn("node lab-1: not checked, book_dir not set or unreadable", r["detail"])
        self.assertIn("node lab-2: book lab-2-book", r["detail"])


if __name__ == "__main__":
    unittest.main()
