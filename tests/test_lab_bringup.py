"""Tests for the lab bring-up helper (lab/lab.sh, lab/pack.py): its dry run, the entries, the pack.

Run: python3 -B -m unittest tests.test_lab_bringup -v
Nothing here starts a node or touches a network: lab.sh runs only with --dry-run.
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAB = ROOT / "lab" / "lab.sh"
sys.path.insert(0, str(ROOT / "lab"))
sys.path.insert(0, str(ROOT / "scripts"))
import check_nodes  # noqa: E402
import pack  # noqa: E402

# Public TEST keys from the rehearsal population, standing in for each lab node's plugin key.
# lab-1 shares alice's here; EntriesTest gives it a key of its own, since entries refuse a repeated key_id.
TEST_PUB = {"lab-1": "alice", "lab-2": "alice", "lab-3": "bob"}
DAY = "2026-10-27"
BUILD = {
    "ok": True, "failures": [], "checked_at": "2026-10-27T00:00:00Z",
    "mesh_llm": {"source_head": "a" * 40, "contains": {"mesh-llm": "b" * 40, "mesh-llm-pr-2620": "c" * 40},
                 "binary_sha256": "d" * 64, "version": "mesh-llm 0.0.0"},
    "capsules": {"source_head": "e" * 40, "binary_sha256": "f" * 64},
    "relay": "off",
}


def lab_sh(*args, env=None):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("LAB_", "MESH_LLM_", "CAPSULES_"))}
    e.update(env or {})
    return subprocess.run(["bash", str(LAB), *args], capture_output=True, text=True, env=e, cwd=ROOT)


def tree_digest(path):
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(path).rglob("*")) if p.is_file()}


class ShellTest(unittest.TestCase):
    def test_bash_n(self):
        r = subprocess.run(["bash", "-n", str(LAB)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_needs_lab_dir(self):
        r = lab_sh("--dry-run", "all")
        self.assertEqual(r.returncode, 2)
        self.assertIn("LAB_DIR", r.stderr)


class DryRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.lab = self.tmp / "lab"
        self.env = {"LAB_DIR": str(self.lab), "LAB_OPERATOR": "lab-operator", "MESH_LLM_BIN": "/opt/mesh-llm",
                    "CAPSULES_BIN": "/opt/capsules", "LAB_MODEL": "/opt/model.gguf",
                    "MESH_LLM_SRC": "/src/mesh-llm", "CAPSULES_SRC": "/src/capsules"}
        self.before = {d: tree_digest(ROOT / d) for d in ("nodes", "population", "reporter")}
        self.r = lab_sh("--dry-run", "all", env=self.env)
        self.out = self.r.stdout

    def test_runs_nothing(self):
        self.assertEqual(self.r.returncode, 0, self.r.stderr)
        self.assertFalse(self.lab.exists(), "a dry run created LAB_DIR")
        for d, digest in self.before.items():
            self.assertEqual(tree_digest(ROOT / d), digest, f"a dry run changed {d}/")

    def test_prints_the_build_check_against_the_pins(self):
        pins = {l.split()[0]: l.split()[2] for l in (ROOT / "versions.lock").read_text().splitlines()
                if l and not l.startswith("#")}
        for name in ("mesh-llm", "mesh-llm-pr-2620"):
            self.assertIn(f"merge-base --is-ancestor {pins[name]} HEAD", self.out)
        self.assertIn("git -C /src/capsules rev-parse HEAD", self.out)
        self.assertIn("sha256sum /opt/mesh-llm /opt/capsules", self.out)

    def test_three_nodes_own_home_and_ports_no_relay(self):
        for i, name in enumerate(("lab-1", "lab-2", "lab-3"), 1):
            self.assertIn(f"HOME={self.lab}/nodes/{name} ", self.out)
            self.assertIn(f"--port 980{i} --console 981{i} --bind-port 982{i}", self.out)
            self.assertIn(f"tmux new-window -d -t loop-lab -n {name}", self.out)
            self.assertIn(f"CAPSULES_HOST_CONSOLE_URL=http://127.0.0.1:981{i}", self.out)
        launches = [l for l in self.out.splitlines() if " serve --config " in l]
        self.assertEqual(len(launches), 3)
        for line in launches:
            self.assertIn("--disable-iroh-relays", line)
            self.assertIn("env -u CAPSULES_PEER_KEYS", line)
            self.assertNotIn("--publish", line)
            self.assertNotIn("--client", line)
        self.assertEqual(sum("--join-file" in l for l in launches), 2)
        self.assertIn("--gguf /opt/model.gguf", launches[0])

    def test_join_each_node_with_its_plugin_key(self):
        for name in ("lab-1", "lab-2", "lab-3"):
            self.assertIn(f"join.sh --name {name} --node-key {self.lab}/nodes/{name}/.local/share/capsules/keys/"
                          f"node-key.pem --controller-key {self.lab}/controllers/{name}/controller-key.pem "
                          f"--out {self.lab}/join/{name}", self.out)
            self.assertIn(f"rm -f {self.lab}/nodes/{name}/join-book/node.seed", self.out)

    def test_loop_from_the_reporter_console_and_pack_list(self):
        self.assertIn("env LOOP_CONSOLE=http://127.0.0.1:9813", self.out)
        self.assertIn("loop.sh --stage 1 --population live --out", self.out)
        self.assertIn("would write nodes/<lab-1 node_id>.json", self.out)
        self.assertIn("would pack", self.out)
        for arc in ("population/live/population.json", "reporter/reporter.json"):
            self.assertIn(f"  {arc}  <-  ", self.out)
        self.assertRegex(self.out, r"missing: findings/\d{4}-\d{2}-\d{2}-live/")
        self.assertRegex(self.out, r"MANIFEST.json")

    def test_port_base_moves_every_port(self):
        r = lab_sh("--dry-run", "start", env={**self.env, "LAB_PORT_BASE": "9900"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--port 9903 --console 9913 --bind-port 9923", r.stdout)
        self.assertNotIn("--port 980", r.stdout)


class LabFixture(unittest.TestCase):
    """A fake LAB_DIR (three plugin data dirs with TEST public keys) and a fake repository root."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.lab, self.root = self.tmp / "lab", self.tmp / "root"
        for d in ("population/live", "reporter", "nodes"):
            (self.root / d).mkdir(parents=True)
        shutil.copy(ROOT / "population/live/population.json", self.root / "population/live/population.json")
        shutil.copy(ROOT / "reporter/reporter.json", self.root / "reporter/reporter.json")
        self.ids = {}
        for i, (name, test) in enumerate(TEST_PUB.items(), 1):
            d = pack.data_dir(self.lab, name)
            (d / "keys").mkdir(parents=True)
            shutil.copy(ROOT / f"population/rehearsal/keys/{test}/node-key.pub.pem", d / "keys/node-key.pub.pem")
            (d / "keys/node-key.pem").write_text("not read: only its owner and mode are\n")
            os.chmod(d / "keys/node-key.pem", 0o600)
            self.ids[name] = f"{i}" * 64
            (d / "self-peer-id").write_text(self.ids[name] + "\n")
        (self.lab / "build.json").write_text(json.dumps(BUILD))

    def entries(self, **kw):
        with contextlib.redirect_stdout(io.StringIO()):
            return pack.entries(self.root, self.lab, kw.get("operator", "lab-operator"), kw.get("reporter", "lab-3"))

    def pack(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return pack.pack(self.root, self.lab, DAY)


class EntriesTest(LabFixture):
    def setUp(self):
        super().setUp()
        # Three distinct keys: a TEST key pair for lab-1, made here with openssl.
        if shutil.which("openssl") is None:
            self.skipTest("openssl is not installed: cannot make a third TEST key")
        d = pack.data_dir(self.lab, "lab-1") / "keys"
        subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(self.tmp / "k.pem")], check=True)
        subprocess.run(["openssl", "pkey", "-in", str(self.tmp / "k.pem"), "-pubout", "-out",
                        str(d / "node-key.pub.pem")], check=True)

    def test_writes_entries_that_pass_check_nodes(self):
        self.assertEqual(self.entries(), [])
        errs, count = check_nodes.check_dir(self.root / "nodes")
        self.assertEqual((errs, count), ([], 3))
        for node_id in self.ids.values():
            entry = json.loads((self.root / "nodes" / f"{node_id}.json").read_text())
            self.assertEqual(entry["consent"], check_nodes.CONSENT)
            self.assertEqual(entry["operator"], "lab-operator")
            self.assertIn("Mesh-LLM/capsules-plugin#48", entry["host_build"])
            self.assertIn("e" * 40, entry["plugin_version"])
        pop = json.loads((self.root / "population/live/population.json").read_text())
        self.assertEqual({n["name"]: n["node_id"] for n in pop["nodes"]}, self.ids)
        rep = json.loads((self.root / "reporter/reporter.json").read_text())
        self.assertEqual(rep["node_id"], self.ids["lab-3"])
        self.assertEqual(rep["operator"], "lab-operator")
        self.assertEqual(rep["retry_after_s"], [3600, 10800, 21600])

    def test_refuses_a_build_that_is_not_pinned(self):
        (self.lab / "build.json").write_text(json.dumps({**BUILD, "ok": False, "failures": ["not the pin"]}))
        errs = self.entries()
        self.assertTrue(errs and "not the pinned one" in errs[0], errs)
        self.assertEqual(list((self.root / "nodes").iterdir()), [])

    def test_refuses_a_reporter_key_others_can_read(self):
        os.chmod(pack.data_dir(self.lab, "lab-3") / "keys/node-key.pem", 0o644)
        errs = self.entries()
        self.assertTrue(errs and "readable beyond this user" in errs[0], errs)

    def test_refuses_an_email_for_operator(self):
        errs = self.entries(operator="someone@example.org")
        self.assertTrue(errs and "email" in errs[0], errs)


class PackTest(LabFixture):
    def setUp(self):
        super().setUp()
        pop_path = self.root / "population/live/population.json"
        pop = json.loads(pop_path.read_text())
        for n in pop["nodes"]:
            n["node_id"] = self.ids[n["name"]]
            (self.root / "nodes" / f"{n['node_id']}.json").write_text("{}\n")
        pop_path.write_text(json.dumps(pop))
        f = self.root / "findings" / f"{DAY}-live" / "12-join"
        f.mkdir(parents=True)
        (f / "finding.json").write_text("{}\n")
        (self.root / "findings" / f"{DAY}-live" / "run.json").write_text("{}\n")
        for name in TEST_PUB:
            j = self.lab / "join" / name
            (j / "probe" / "stranger").mkdir(parents=True)
            (j / "commitment.json").write_text('{"node": "%s"}\n' % name)
            (j / "probe" / "checkpoints.answer.json").write_text("{}\n")
            (j / "probe" / "stranger" / "signing.seed").write_text("00" * 32 + "\n")
        (self.lab / "run").mkdir()
        (self.lab / "run" / "invite.token").write_text("invite-token-value-for-the-test\n")
        self.out = self.lab / "out" / f"loop-lab-{DAY}.tgz"

    def test_packs_repository_paths_with_a_manifest(self):
        self.assertEqual(self.pack(), [])
        with tarfile.open(self.out) as tar:
            names = set(tar.getnames())
            manifest = json.loads(tar.extractfile(f"lab/runs/{DAY}/MANIFEST.json").read())
            for arc, digest in manifest["files"].items():
                self.assertEqual(hashlib.sha256(tar.extractfile(arc).read()).hexdigest(), digest)
        for node_id in self.ids.values():
            self.assertIn(f"nodes/{node_id}.json", names)
        for arc in ("population/live/population.json", "reporter/reporter.json",
                    f"findings/{DAY}-live/12-join/finding.json", f"findings/{DAY}-live/run.json",
                    f"lab/runs/{DAY}/build.json", f"lab/runs/{DAY}/join/lab-2/commitment.json"):
            self.assertIn(arc, names)
        self.assertFalse([n for n in names if n.endswith((".seed", ".pem"))], names)
        self.assertEqual(set(manifest["files"]) | {f"lab/runs/{DAY}/MANIFEST.json"}, names)

    def test_refuses_a_private_key(self):
        (self.lab / "join" / "lab-1" / "oops.json").write_text(
            "-----BEGIN PRIVATE KEY-----\nMC4CAQAwBQYDK2VwBCIEIA==\n-----END PRIVATE KEY-----\n")
        errs = self.pack()
        self.assertTrue(errs and "private key" in errs[0], errs)
        self.assertFalse(self.out.exists())

    def test_refuses_the_invite_token(self):
        (self.root / "findings" / f"{DAY}-live" / "12-join" / "loop-step.log").write_text(
            "joined with invite-token-value-for-the-test\n")
        errs = self.pack()
        self.assertTrue(errs and "invite token" in errs[0], errs)
        self.assertFalse(self.out.exists())

    def test_refuses_when_something_is_missing(self):
        shutil.rmtree(self.lab / "join" / "lab-3")
        errs = self.pack()
        self.assertTrue(any("join/lab-3" in e for e in errs), errs)
        self.assertFalse(self.out.exists())

    def test_dry_run_lists_files_and_writes_nothing(self):
        r = subprocess.run([sys.executable, "-B", str(ROOT / "lab/pack.py"), "pack", "--lab-dir", str(self.lab),
                            "--date", DAY, "--root", str(self.root), "--dry-run"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"lab/runs/{DAY}/join/lab-1/commitment.json  <-  ", r.stdout)
        self.assertNotIn("signing.seed", r.stdout)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
