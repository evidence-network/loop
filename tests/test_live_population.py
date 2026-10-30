"""Steps 01, 02, 03 and 12 on the live population: each node's own key and book, or NOT_CHECKED.

On a population with every data_dir, book_dir and node_id null (the file before the
lab run, kept as tests/fixtures/live/null) each step writes NOT_CHECKED with a `live node <name>` entry, never a crash or a FAIL,
and `loop.sh --stage 1 --population live` exits 0. With a fixture LAB_DIR (the
TEST keys of tests/fixtures/step02 as each node's plugin keys, and a book join.sh
makes here with them), step 01 seals with each node's key and PASSes, and step 02
appends to each node's book and gives the expected NOT_CHECKED (the witness part
only). The tests that need a real tool SKIP and say so when it is missing.
"""
import base64
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
DRIVER = Path(os.environ.get("LOOP_STEP02_BOOK_BIN") or TOOLS / "02-book" / "bin" / "loop-step02-book")
UNSET_LAB_DIR = ("book_dir names $LAB_DIR, which this step's environment does not set "
                 "(export it, or run the loop through lab/lab.sh loop)")


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

    def test_02_unexpanded_lab_dir_is_not_checked_naming_the_variable(self):
        pop = self.tmp / "population" / "live"
        pop.mkdir(parents=True)
        shutil.copy(FIXTURE, pop / "population.json")
        env = {k: v for k, v in os.environ.items() if k != "LAB_DIR"}
        r = self.step("02-book", pop, env)
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertIn(live.not_checked("lab-1", "book_dir", UNSET_LAB_DIR), r["not_checked"])
        self.assertIn(live.not_checked("lab-2", "data_dir", UNSET_LAB_DIR.replace("book_dir", "data_dir")),
                      r["not_checked"])
        self.assertIn(f"node lab-1: not checked, {UNSET_LAB_DIR.replace('book_dir', 'data_dir')}; {UNSET_LAB_DIR}",
                      r["detail"])
        self.assertNotIn("not set or unreadable", r["detail"])

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
                         ["01 seal NOT_CHECKED", "02 book NOT_CHECKED", "03 advertise NOT_CHECKED",
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

    def test_why_not_names_an_unset_variable_then_a_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            node = {"book_dir": "$LOOP_TEST_UNSET_DIR/join-book"}
            os.environ.pop("LOOP_TEST_UNSET_DIR", None)
            self.assertEqual(live.unexpanded(node, "book_dir"), ["LOOP_TEST_UNSET_DIR"])
            self.assertIn("book_dir names $LOOP_TEST_UNSET_DIR, which this step's environment does not set",
                          live.why_not(node, "book_dir", "checkpoint.seed"))
            os.environ["LOOP_TEST_UNSET_DIR"] = d
            try:
                (Path(d) / "join-book" / "book" / "book" / "records").mkdir(parents=True)
                self.assertEqual(live.unexpanded(node, "book_dir"), [])
                self.assertEqual(live.why_not(node, "book_dir", "checkpoint.seed"),
                                 "book_dir has no readable checkpoint.seed")
                self.assertEqual(live.why_not({"book_dir": None}, "book_dir"), "book_dir not set or unreadable")
            finally:
                del os.environ["LOOP_TEST_UNSET_DIR"]


class Step01LiveTest(LiveCase):
    def setUp(self):
        super().setUp()
        for t in SEAL_TOOLS:
            need(t, "steps/01-seal.setup.sh")
        self.pop, self.lab_dir, self.env = self.lab()

    def test_each_node_seals_with_its_own_key_and_passes(self):
        r = self.step("01-seal", self.pop, self.env)
        self.assertEqual(r["result"], "PASS", r["detail"])
        for name, test in NODE_KEYS.items():
            self.assertIn(f"node {name}: Class 1 ok", r["detail"])
            self.assertIn(f"{name}/capsule.json", [p["path"] for p in r["proof"]])
            capsule = json.loads((self.findings / "01-seal" / name / "capsule.json").read_text())
            checks = json.loads((self.findings / "01-seal" / name / "checks.json").read_text())
            self.assertTrue(checks["checks"]["key"]["ok"])
            self.assertIn(capsule["key_id"], checks["checks"]["key"]["detail"])
        # A PASS covers its assertion: the signature, outside it, is said in detail, not as not_checked.
        self.assertEqual(r["not_checked"], [])
        self.assertIn("Outside this step's assertion: the producer envelope signature", r["detail"])

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


class JoinedLab(LiveCase):
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


class Step02LiveTest(JoinedLab):
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

    def test_a_book_profile_named_lab_n_is_found_under_an_operator_label(self):
        # lab/lab.sh join names each book profile lab-N; population.json names the node by its operator's label.
        pop = json.loads((self.pop / "population.json").read_text())
        for i, n in enumerate(pop["nodes"], 1):
            n["name"] = f"operator-{i}"
        (self.pop / "population.json").write_text(json.dumps(pop))
        r = self.step("02-book", self.pop, self.env)
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        self.assertEqual([n["what"] for n in r["not_checked"]],
                         ["a witness receipt on the book's checkpoint, and its verification"])
        self.assertIn("node operator-1: book lab-1-book", r["detail"])
        self.assertIn("node operator-2: book lab-2-book", r["detail"])


class Step02LiveWitnessTest(JoinedLab):
    """The lab test witness (lab/witness.py, signing with step 02's driver) gives each book's checkpoint a
    receipt; step 02 checks it against a listing made here. Every key is a TEST key made here."""

    def setUp(self):
        super().setUp()
        need(DRIVER, "steps/02-book.setup.sh")
        self.keys = self.tmp / "witness"
        self.keys.mkdir(mode=0o700)
        self.cert, tls = self.keys / "tls-cert.pem", self.keys / "tls-key.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1",
                        "-nodes", "-keyout", str(tls), "-out", str(self.cert), "-days", "1", "-subj", "/CN=test",
                        "-addext", "subjectAltName=IP:127.0.0.1"], check=True, capture_output=True)
        self.tls = tls
        self.listing = self.tmp / "witnesses.json"
        self.env = dict(self.env, LOOP_WITNESS_LISTING=str(self.listing), LOOP_WITNESS_CA=str(self.cert),
                        LOOP_STEP02_BOOK_BIN=str(DRIVER))

    def key(self, name, source=None):
        """A witness key (a new TEST key, or a copy of `source`) and its public key id."""
        pem = self.keys / f"{name}.pem"
        if source:
            shutil.copy(source, pem)
        else:
            subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(pem)], check=True,
                           capture_output=True)
        os.chmod(pem, 0o600)
        der = subprocess.run(["openssl", "pkey", "-in", str(pem), "-pubout", "-outform", "DER"],
                             check=True, capture_output=True).stdout
        return pem, der[12:].hex()

    def list_keys(self, *entries):
        self.listing.write_text(json.dumps({"witnesses": [
            {"name": "lab-test-witness", "key_id": k, "not_before": nb} for k, nb in entries]}))

    def serve(self, pem, at=None):
        """Start the witness with this key; returns its port."""
        p = subprocess.Popen([sys.executable, "-B", str(ROOT / "lab" / "witness.py"), "serve", "--key", str(pem),
                              "--cert", str(self.cert), "--tls-key", str(self.tls), "--port", "0",
                              "--driver", str(DRIVER), *(["--at", at] if at else [])],
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.addCleanup(p.stdout.close)
        self.addCleanup(p.wait)
        self.addCleanup(p.terminate)
        return int(p.stdout.readline().rsplit(":", 1)[1])

    def configure(self, port, key_id):
        """Point each node's book profile at the witness, as lab/lab.sh witness does."""
        for name in NODE_KEYS:
            r = subprocess.run([str(CAPSULECTL), "profile", "update", "--profile", name, "--checkpoint-endpoint",
                                f"https://127.0.0.1:{port}", "--checkpoint-public-key", key_id],
                               capture_output=True, text=True,
                               env=dict(self.env, XDG_CONFIG_HOME=str(self.lab_dir / "nodes" / name / "join-book" /
                                                                     "config")))
            self.assertEqual(r.returncode, 0, r.stderr)

    def witnessed(self, pem, key_id, at=None):
        self.configure(self.serve(pem, at), key_id)
        return self.step("02-book", self.pop, self.env)

    def test_a_listed_witness_receipt_passes(self):
        pem, kid = self.key("witness")
        self.list_keys((kid, "2026-01-01T00:00:00Z"))
        r = self.witnessed(pem, kid)
        self.assertEqual(r["result"], "PASS", r["detail"])
        self.assertEqual(r["not_checked"], [])
        self.assertIn("witnesses.json", [p["path"] for p in r["proof"]])
        for name in NODE_KEYS:
            checks = json.loads((self.findings / "02-book" / name / "checks.json").read_text())["checks"]
            self.assertTrue(all(c["ok"] for c in checks.values()), checks)
            self.assertEqual({k for k in checks if k.startswith("witness")},
                             {"witness_listed", "witness_not_node", "witness_not_before", "witness_receipt"})
            self.assertIn(f"{name}/witness-status.json", [p["path"] for p in r["proof"]])

    def test_a_receipt_under_a_key_the_listing_does_not_name_fails(self):
        pem, kid = self.key("witness")
        _, other = self.key("listed")
        self.list_keys((other, "2026-01-01T00:00:00Z"))
        r = self.witnessed(pem, kid)
        self.assertEqual(r["result"], "FAIL")
        self.assertIn(f"witness_listed (receipt key id {kid}", r["detail"])
        self.assertNotIn("witness_receipt (", r["detail"])

    def test_a_receipt_signed_by_the_witnessed_node_itself_fails(self):
        # lab-1's own plugin key signs as the witness, and is even listed: it is still a node's key.
        pem, kid = self.key("node", TEST_KEYS / "alice" / "node-key.pem")
        self.list_keys((kid, "2026-01-01T00:00:00Z"))
        r = self.witnessed(pem, kid)
        self.assertEqual(r["result"], "FAIL")
        self.assertIn(f"witness_not_node (receipt key id {kid}; a node's own key)", r["detail"])
        self.assertNotIn("witness_listed (", r["detail"])

    def test_a_book_whose_witness_gives_no_receipt_is_not_checked_saying_why(self):
        _, kid = self.key("witness")
        self.list_keys((kid, "2026-01-01T00:00:00Z"))
        with socket.socket() as s:  # a port nothing answers on
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.configure(port, kid)
        r = self.step("02-book", self.pop, self.env)
        self.assertEqual(r["result"], "NOT_CHECKED", r["detail"])
        for name in NODE_KEYS:
            gap = next(n for n in r["not_checked"] if n["what"].startswith(f"a witness receipt on node {name}'s"))
            self.assertIn("to its witness and holds no receipt: ", gap["why"])

    def test_an_old_receipt_under_a_rotated_out_key_still_passes(self):
        old, old_id = self.key("old")
        _, new_id = self.key("new")
        self.list_keys((old_id, "2026-01-01T00:00:00Z"), (new_id, "2026-06-01T00:00:00Z"))
        r = self.witnessed(old, old_id, at="2026-03-01T00:00:00Z")
        self.assertEqual(r["result"], "PASS", r["detail"])

    def test_a_receipt_signed_before_its_keys_not_before_fails(self):
        _, old_id = self.key("old")
        new, new_id = self.key("new")
        self.list_keys((old_id, "2026-01-01T00:00:00Z"), (new_id, "2026-06-01T00:00:00Z"))
        r = self.witnessed(new, new_id, at="2026-03-01T00:00:00Z")
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("witness_not_before (signed at 2026-03-01T00:00:00Z; the entry's not_before "
                      "2026-06-01T00:00:00Z)", r["detail"])



def cose_receipt(kid, iat):
    """COSE_Sign1 bytes whose protected header is {alg: EdDSA, 395: 1, kid, 15: {iat}} (signature zeroed: the step
    reads the header, capsulectl checks the signature)."""
    protected = (bytes.fromhex("a4" "0127" "19018b01" "045820") + bytes.fromhex(kid)
                 + bytes.fromhex("0fa1061a") + iat.to_bytes(4, "big"))
    return (bytes.fromhex("d284") + bytes([0x58, len(protected)]) + protected + bytes.fromhex("a0f6")
            + bytes.fromhex("5840") + bytes(64))


class WitnessChecksTest(unittest.TestCase):
    """steps/02-book's checks on a stored receipt, on receipts made here: no tool needed."""
    KID, NODE = "ab" * 32, "cd" * 32
    AT = 1772323200  # 2026-03-01T00:00:00Z

    def setUp(self):
        loader = importlib.machinery.SourceFileLoader("step02_live", str(ROOT / "steps" / "02-book"))
        spec = importlib.util.spec_from_loader("step02_live", loader)
        self.step = importlib.util.module_from_spec(spec)
        loader.exec_module(self.step)
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        self.step.LISTING = tmp / "witnesses.json"
        self.step.LISTING.write_text(json.dumps({"witnesses": [
            {"name": "lab-test-witness", "key_id": self.KID, "not_before": "2026-01-01T00:00:00Z"}]}))

    def checks(self, kid=KID, rc=0, state="verified", profile_key=KID):
        receipt = base64.b64encode(cose_receipt(kid, self.AT)).decode()
        return self.step.witness_checks(rc, {"state": state, "receipt": {"Bytes": receipt}}, profile_key, {self.NODE})

    def failed(self, checks):
        return {k for k, c in checks.items() if not c["ok"]}

    def test_a_listed_key_under_its_not_before_and_reverified_holds(self):
        c = self.checks()
        self.assertEqual(self.failed(c), set(), c)
        self.assertIn("signed at 2026-03-01T00:00:00Z", c["witness_not_before"]["detail"])

    def test_a_receipt_naming_a_listed_key_id_but_verified_under_another_key_fails(self):
        self.assertEqual(self.failed(self.checks(profile_key=self.NODE)), {"witness_receipt"})

    def test_a_receipt_capsulectl_does_not_reverify_fails(self):
        self.assertEqual(self.failed(self.checks(rc=1)), {"witness_receipt"})
        self.assertEqual(self.failed(self.checks(state="pending")), {"witness_receipt"})

    def test_an_unlisted_key_id_fails(self):
        self.assertEqual(self.failed(self.checks(kid="ef" * 32, profile_key="ef" * 32)),
                         {"witness_listed", "witness_not_before"})

    def test_a_receipt_signed_before_the_entrys_not_before_fails(self):
        self.step.LISTING.write_text(json.dumps({"witnesses": [
            {"name": "lab-test-witness", "key_id": self.KID, "not_before": "2026-06-01T00:00:00Z"}]}))
        self.assertEqual(self.failed(self.checks()), {"witness_not_before"})

    def test_a_node_key_id_fails_even_listed(self):
        self.step.LISTING.write_text(json.dumps({"witnesses": [
            {"name": "lab-test-witness", "key_id": self.NODE, "not_before": "2026-01-01T00:00:00Z"}]}))
        self.assertEqual(self.failed(self.checks(kid=self.NODE, profile_key=self.NODE)), {"witness_not_node"})

if __name__ == "__main__":
    unittest.main()
