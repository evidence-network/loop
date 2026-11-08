"""Tests for the lab's second host (lab/lab.sh LAB_REMOTE_NODES, lab/remote/loop-lab-remote).

Run: python3 -B -m unittest tests.test_lab_remote -v
Nothing here touches a network: ssh is a stand-in script that runs the verb on this machine, as the forced command would.
"""
import os
import shutil
import sqlite3
import stat
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAB = ROOT / "lab" / "lab.sh"
WRAPPER = ROOT / "lab" / "remote" / "loop-lab-remote"
TRUE = shutil.which("true")


def clean_env(extra):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("LAB_", "MESH_LLM_", "CAPSULES_", "GIT_", "SSH_"))}
    e.update(extra)
    return e


class RemoteBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.lab = self.tmp / "lab"          # the driver's LAB_DIR
        self.rlab = self.tmp / "rlab"        # the second host's LAB_DIR
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.log = self.tmp / "ssh.log"
        self.env_file = self.tmp / "remote.env"
        self.env_file.write_text(f"LOOP_LAB_SH={LAB}\nLAB_DIR={self.rlab}\nLAB_ONLY='lab-2 lab-4'\nLAB_TWINS=1\n"
                                 f"MESH_LLM_BIN={TRUE}\nCAPSULES_BIN={TRUE}\n")
        # A stand-in ssh: `-O check` fails (no master), the tunnel (-fN) is logged, and a command runs through the
        # wrapper with SSH_ORIGINAL_COMMAND, as a forced command would.
        fake = self.bin / "ssh"
        fake.write_text(f"""#!/bin/bash
echo "ssh $*" >> {self.log}
case "$*" in
  *"-O check"*) exit 255 ;;
  *"-O exit"*) exit 0 ;;
  *" -fN "*) exit 0 ;;
esac
cmd=${{@: -1}}
exec env -i PATH="$PATH" HOME="$HOME" SSH_ORIGINAL_COMMAND="$cmd" LOOP_LAB_REMOTE_ENV={self.env_file} {WRAPPER}
""")
        fake.chmod(0o755)

    def lab_sh(self, *args, **env):
        e = clean_env({"PATH": f"{self.bin}:{os.environ['PATH']}", "LAB_DIR": str(self.lab), "LAB_PORT_BASE": "29800",
                       "LAB_REMOTE_NODES": "lab-2 lab-4", "LAB_REMOTE_SSH": "labprov", "LAB_TWINS": "1",
                       "LAB_MODEL_REF": "o/r/m.gguf", "MESH_LLM_BIN": TRUE, "CAPSULES_BIN": TRUE, **env})
        return subprocess.run(["bash", str(LAB), *args], capture_output=True, text=True, env=e, cwd=ROOT)

    def wrapper(self, command, stdin=""):
        e = clean_env({"SSH_ORIGINAL_COMMAND": command, "LOOP_LAB_REMOTE_ENV": str(self.env_file)})
        return subprocess.run([str(WRAPPER)], input=stdin, capture_output=True, text=True, env=e)


class DryRunTest(RemoteBase):
    def test_start_sends_the_token_and_starts_each_remote_node_through_the_wrapper_only(self):
        r = self.lab_sh("--dry-run", "start")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn("put-token", out)
        self.assertIn("node-start\\ lab-2", out)
        self.assertIn("node-start\\ lab-4", out)
        # Only lab-1 and lab-3 get a launcher here.
        self.assertIn(str(self.lab / "run" / "lab-1.sh"), out)
        self.assertIn(str(self.lab / "run" / "lab-3.sh"), out)
        self.assertNotIn(str(self.lab / "run" / "lab-2.sh"), out)
        self.assertNotIn(str(self.lab / "run" / "lab-4.sh"), out)
        # The forwards: API and console of each remote node to here.
        self.assertIn("-L 127.0.0.1:29802:127.0.0.1:29802", out)
        self.assertIn("-L 127.0.0.1:29814:127.0.0.1:29814", out)
        self.assertNotIn("-R ", out)  # nothing on the second host needs the witness
        self.assertIn("guard: lab-2's home here is a mirror", out)

    def test_stop_stops_this_hosts_nodes_and_asks_the_other_to_stop_its_own(self):
        r = self.lab_sh("--dry-run", "stop")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(str(self.lab / "run" / "lab-1.pid"), r.stdout)
        self.assertNotIn(str(self.lab / "run" / "lab-2.pid"), r.stdout)
        self.assertIn(" stop", r.stdout)

    def test_a_node_that_cannot_move_is_refused(self):
        for nodes, why in (("lab-1", "makes the mesh"), ("lab-3", "never moves"), ("lab-9", "not a lab node")):
            r = self.lab_sh("--dry-run", "status", LAB_REMOTE_NODES=nodes)
            self.assertEqual(r.returncode, 2, nodes)
            self.assertIn(why, r.stderr)
        r = self.lab_sh("--dry-run", "status", LAB_REMOTE_SSH="")
        self.assertEqual(r.returncode, 2)
        self.assertIn("LAB_REMOTE_SSH", r.stderr)

    def test_without_remote_nodes_nothing_changes(self):
        r = self.lab_sh("--dry-run", "start", LAB_REMOTE_NODES="", LAB_REMOTE_SSH="")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("ssh", r.stdout)
        self.assertIn(str(self.lab / "run" / "lab-2.sh"), r.stdout)


class WrapperTest(RemoteBase):
    def test_put_token_writes_it_0600_and_only_it(self):
        r = self.wrapper("put-token", stdin="tok-123\n")
        self.assertEqual(r.returncode, 0, r.stderr)
        f = self.rlab / "run" / "invite.token"
        self.assertEqual(f.read_text(), "tok-123\n")
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        self.assertEqual(self.wrapper("put-token", stdin="").returncode, 1)

    def test_unknown_verbs_other_nodes_and_injection_are_refused(self):
        for cmd in ("bash", "rm -rf /", "node-start lab-1", "node-start lab-3", "node-id lab-2;id", "mirror ../lab-2",
                    "node-start lab-2 extra extra", "running x y", "running " + "a" * 64 + " zz", ""):
            r = self.wrapper(cmd)
            self.assertEqual(r.returncode, 2, (cmd, r.stderr))
            self.assertNotIn("command not found", r.stderr)

    def test_node_id_prints_the_id_and_fails_without_one(self):
        d = self.rlab / "nodes" / "lab-2" / ".local" / "share" / "capsules"
        self.assertNotEqual(self.wrapper("node-id lab-2").returncode, 0)
        d.mkdir(parents=True)
        (d / "self-peer-id").write_text("2" * 64 + "\n")
        r = self.wrapper("node-id lab-2")
        self.assertEqual((r.returncode, r.stdout.strip()), (0, "2" * 64))
        self.assertEqual(self.wrapper("node-id lab-1").returncode, 2)

    def test_running_needs_four_digests_and_a_matching_script_and_config(self):
        a = "a" * 64
        self.assertEqual(self.wrapper(f"running {a} {a}").returncode, 2)
        r = self.wrapper(f"running {a} {a} {a} {a}")  # runs lab.sh running with the wrong script digest
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not the driver's", r.stderr)
        self.assertIn("config digest", r.stderr)


class MirrorTest(RemoteBase):
    def second_host_node(self, name="lab-2"):
        d = self.rlab / "nodes" / name / ".local" / "share" / "capsules"
        (d / "ledger").mkdir(parents=True)
        (d / "keys").mkdir()
        (d / "keys" / "node-key.pem").write_text("PRIVATE: must not travel\n")
        (d / "payments").mkdir()
        (d / "payments" / "seedphrase.txt").write_text("PRIVATE: must not travel\n")
        (d / "self-peer-id").write_text("2" * 64 + "\n")
        (d / "controller-binding.json").write_text("{}")
        (d / "sharing-effective.json").write_text("{}")
        (self.rlab / "run").mkdir(exist_ok=True)
        (self.rlab / "run" / f"{name}.build.json").write_text('{"plugin_sha256": "x"}')
        (d / "ledger" / "received-capsules.jsonl").write_text('{"a":1}\n')
        db = sqlite3.connect(d / "ledger" / "shelf.db")
        db.execute("create table t (x)")
        db.execute("insert into t values (42)")
        db.commit()
        db.close()
        (d / "ledger" / "shelf.db-wal").write_text("junk")
        return d

    def test_the_mirror_holds_the_ledger_and_peer_id_and_never_a_key_or_a_wallet(self):
        self.second_host_node()
        r = subprocess.run(["bash", str(LAB), "mirror", "lab-2"], capture_output=True,
                           env=clean_env({"LAB_DIR": str(self.rlab), "LAB_ONLY": "lab-2 lab-4", "LAB_TWINS": "1"}))
        self.assertEqual(r.returncode, 0, r.stderr)
        out = self.tmp / "out"
        out.mkdir()
        (self.tmp / "m.tar").write_bytes(r.stdout)
        with tarfile.open(self.tmp / "m.tar") as t:
            names = sorted(m.name for m in t.getmembers() if m.isfile())
            t.extractall(out)
        self.assertEqual(names, ["./build-mark.json", "./controller-binding.json", "./ledger/received-capsules.jsonl", "./ledger/shelf.db",
                                 "./self-peer-id", "./sharing-effective.json"])
        self.assertEqual(sqlite3.connect(out / "ledger" / "shelf.db").execute("select x from t").fetchone(), (42,))

    def test_refresh_replaces_ledger_and_peer_id_in_the_driver_mirror_and_keeps_the_rest(self):
        self.second_host_node()
        mirror = self.lab / "nodes" / "lab-2"
        data = mirror / ".local" / "share" / "capsules"
        (data / "ledger").mkdir(parents=True)
        (data / "ledger" / "old.jsonl").write_text("stale\n")
        (data / "keys").mkdir()
        (data / "keys" / "node-key.pub.pem").write_text("pub\n")
        (mirror / "join-book").mkdir()
        (mirror / ".mirror").write_text("")
        for n in ("lab-4",):  # lab-4 has a mirror too, with a node behind it
            self.second_host_node(n)
            m = self.lab / "nodes" / n
            m.mkdir(parents=True)
            (m / ".mirror").write_text("")
        (self.lab / "run").mkdir(exist_ok=True)
        r = self.lab_sh("refresh-mirror")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((data / "ledger" / "received-capsules.jsonl").exists(), r.stderr)
        self.assertTrue((data / "controller-binding.json").exists())
        self.assertEqual((self.lab / "run" / "lab-2.build.json").read_text(), '{"plugin_sha256": "x"}')
        self.assertFalse((data / "ledger" / "old.jsonl").exists())
        self.assertEqual((data / "self-peer-id").read_text(), "2" * 64 + "\n")
        self.assertTrue((data / "keys" / "node-key.pub.pem").exists())
        self.assertTrue((mirror / "join-book").is_dir())
        self.assertFalse((data / "payments").exists())
        self.assertFalse((data / "keys" / "node-key.pem").exists())

    def test_a_home_that_is_not_a_mirror_is_never_overwritten(self):
        self.second_host_node()
        home = self.lab / "nodes" / "lab-2"
        (home / ".local" / "share" / "capsules").mkdir(parents=True)
        (home / "config.toml").write_text("real node home\n")
        (self.lab / "run").mkdir(exist_ok=True)
        r = self.lab_sh("refresh-mirror", "lab-2")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not a mirror", r.stderr)
        self.assertEqual((home / "config.toml").read_text(), "real node home\n")


class EnvironmentTest(RemoteBase):
    def test_a_mirror_without_the_remote_setting_refuses_every_command(self):
        home = self.lab / "nodes" / "lab-2"
        home.mkdir(parents=True)
        (home / ".mirror").write_text("")
        for cmd in ("stop", "start", "status", "running"):
            r = self.lab_sh("--dry-run", cmd, LAB_REMOTE_NODES="", LAB_REMOTE_SSH="")
            self.assertEqual(r.returncode, 2, cmd)
            self.assertIn("mirror of a node on another host", r.stderr)

    def test_remote_names_need_not_be_in_a_twins_free_node_list(self):
        r = self.lab_sh("--dry-run", "stop", LAB_TWINS="0")
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_the_requester_cannot_be_remote(self):
        r = self.lab_sh("--dry-run", "stop", LAB_REMOTE_NODES="lab-2", LAB_REQUESTER="lab-2", LAB_REFEREE="lab-3")
        self.assertEqual(r.returncode, 2)
        self.assertIn("requester", r.stderr)

    def test_an_unreachable_second_host_still_stops_this_one_and_says_so(self):
        (self.bin / "ssh").write_text("#!/bin/bash\nexit 255\n")
        r = self.lab_sh("stop")
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("could not stop lab-2 lab-4", r.stderr)
        self.assertIn("stopped lab-1 lab-3", r.stdout)


class GuardTest(RemoteBase):
    def guard(self):
        return self.lab_sh("guard-moved", "lab-2", LAB_PAYMENTS="lab-1 lab-2 lab-3")

    def test_only_a_mirror_with_no_node_home_may_start_remotely(self):
        home = self.lab / "nodes" / "lab-2"
        home.mkdir(parents=True)
        self.assertNotEqual(self.guard().returncode, 0)  # no .mirror marker
        (home / ".mirror").write_text("")
        r = self.guard()
        self.assertEqual(r.returncode, 0, r.stderr)
        for leftover in ("config.toml", "payments", ".mesh-llm/key"):
            p = home / leftover
            p.parent.mkdir(parents=True, exist_ok=True)
            p.mkdir() if leftover == "payments" else p.write_text("")
            r = self.guard()
            self.assertNotEqual(r.returncode, 0, leftover)
            self.assertIn("never run in two places", r.stderr)
            shutil.rmtree(p) if p.is_dir() else p.unlink()

    def test_a_live_process_refuses_it_too(self):
        home = self.lab / "nodes" / "lab-2"
        home.mkdir(parents=True)
        (home / ".mirror").write_text("")
        (self.lab / "run").mkdir()
        (self.lab / "run" / "lab-2.pid").write_text(str(os.getpid()))  # this test process is alive
        self.assertNotEqual(self.guard().returncode, 0)

    def test_a_local_node_is_not_guarded(self):
        self.assertNotEqual(self.lab_sh("guard-moved", "lab-1").returncode, 0)


if __name__ == "__main__":
    unittest.main()
