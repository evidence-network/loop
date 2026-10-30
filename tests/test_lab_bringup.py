"""Tests for the lab bring-up helper (lab/lab.sh, lab/pack.py): its dry run, the entries, the pack.

Run: python3 -B -m unittest tests.test_lab_bringup -v
Nothing here starts a node or touches a network: lab.sh runs only with --dry-run.
"""
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
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


def lab_sh(*args, env=None, root=ROOT):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("LAB_", "MESH_LLM_", "CAPSULES_", "GIT_"))}
    e.update(env or {})
    return subprocess.run(["bash", str(Path(root) / "lab" / "lab.sh"), *args], capture_output=True, text=True,
                          env=e, cwd=root)


def git(tree, *args):
    return subprocess.run(["git", "-C", str(tree), "-c", "user.name=lab test", "-c", "user.email=lab@example.invalid",
                           "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def new_tree(path, commits=1):
    """A git tree with `commits` empty commits; returns their SHAs, oldest first."""
    path.mkdir(parents=True)
    git(path, "init", "-q")
    shas = []
    for i in range(commits):
        git(path, "commit", "-q", "--allow-empty", "-m", f"c{i}")
        shas.append(git(path, "rev-parse", "HEAD"))
    return shas


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

    def test_commitments_from_each_live_node_console_after_bind_and_entries(self):
        lines = self.out.splitlines()
        cmds = [l for l in lines if "lab/commitments.py" in l]
        self.assertEqual(len(cmds), 3, cmds)
        for i, line in enumerate(cmds, 1):
            self.assertIn(f"--console http://127.0.0.1:981{i}", line)
        pos = lambda needle: next(i for i, l in enumerate(lines) if needle in l)
        self.assertLess(pos("lab/bind.py"), pos("check_nodes.py"))
        self.assertLess(pos("check_nodes.py"), pos("lab/commitments.py"))
        self.assertLess(pos("lab/commitments.py"), pos("loop.sh --stage 1"))

    def test_loop_from_the_reporter_console_and_pack_list(self):
        self.assertIn("env LOOP_CONSOLE=http://127.0.0.1:9813 LOOP_SELF_NODE=stevenmih-3 "
                      "LOOP_SECOND_CONSOLE=http://127.0.0.1:9811 LAB_DIR=", self.out)
        self.assertIn("LOOP_NODE_CONSOLES=stevenmih-1=http://127.0.0.1:9811\\,stevenmih-2=http://127.0.0.1:9812\\,"
                      "stevenmih-3=http://127.0.0.1:9813 ", self.out)
        self.assertIn("loop.sh --stage 1 --population live --out", self.out)
        self.assertRegex(self.out, r"would write nodes/<\S+ node_id>\.json")
        self.assertIn("would pack", self.out)
        for arc in ("population/live/population.json", "reporter/reporter.json"):
            self.assertIn(f"  {arc}  <-  ", self.out)
        # Today's live findings: missing before loop.sh runs, listed once a run is in the tree.
        self.assertRegex(self.out, r"(missing: findings/|  findings/)\d{4}-\d{2}-\d{2}-live/")
        self.assertRegex(self.out, r"MANIFEST.json")

    def test_all_runs_check_then_tools_then_the_rest(self):
        marks = [("check", "merge-base --is-ancestor"), ("tools", "steps/01-seal.setup.sh"),
                 ("start", " serve --config "), ("exchange", "/v1/chat/completions"),
                 ("join", "join.sh --name lab-1"), ("bind", "lab/bind.py --console"),
                 ("entries", "would write nodes/<"),
                 ("loop", "loop.sh --stage 1"), ("pack", "would pack")]
        at = [(self.out.find(text), name) for name, text in marks]
        self.assertTrue(all(i >= 0 for i, _ in at), at)
        self.assertEqual([name for _, name in sorted(at)], [name for name, _ in marks])

    def test_bind_covers_every_node_but_the_reporter_and_keeps_its_key_in_the_lab_dir(self):
        r = lab_sh("--dry-run", "bind", env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        binds = [l for l in r.stdout.splitlines() if "lab/bind.py" in l]
        self.assertEqual(len(binds), 2)
        self.assertTrue(all("--console http://127.0.0.1:981" in l for l in binds))
        self.assertFalse(any("9813" in l for l in binds), binds)  # lab-3 is the reporter
        self.assertTrue(all(f"{self.lab}/controllers/lab-" in l and l.rstrip().endswith("nostr.key") for l in binds), binds)
        r = lab_sh("--dry-run", "bind", env={**self.env, "LAB_REBIND": "1"})
        self.assertEqual(r.stdout.count("--rebind"), 2)

    def test_every_node_grants_the_plugin_its_admission_hook(self):
        r = lab_sh("--dry-run", "start", env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count("[plugin.openai_exchange_grant]"), 3)
        self.assertEqual(r.stdout.count("admission = true"), 3)
        self.assertEqual(r.stdout.count('phases = ["backend_selected"]'), 3)

    def test_without_lab_twins_the_lab_is_three_nodes_with_no_fault(self):
        r = lab_sh("--dry-run", "start", env=self.env)
        self.assertNotIn("lab-4", r.stdout)
        self.assertNotIn("ADJUDICATE_DIFFERING_TWINS", r.stdout)
        self.assertNotIn("request_defaults", r.stdout)
        self.assertNotEqual(lab_sh("--dry-run", "twins", env=self.env).returncode, 0)

    def test_lab_twins_adds_lab_4_on_its_own_ports_with_four_roles(self):
        r = lab_sh("--dry-run", "start", env={**self.env, "LAB_TWINS": "1", "LAB_MODEL_REF": "owner/repo/model-q4.gguf"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--port 9804 --console 9814 --bind-port 9824", r.stdout)
        self.assertEqual(r.stdout.count("[plugin.openai_exchange_grant]"), 4)
        # Only the requester (lab-3) turns the referee switch on.
        on = [l for l in r.stdout.splitlines() if "CAPSULES_ADJUDICATE_DIFFERING_TWINS=on" in l]
        self.assertEqual(len(on), 1)
        self.assertIn(f"HOME={self.lab}/nodes/lab-3 ", on[0])
        # The referee's re-answer goes to the requester's own API, not the plugin's 9337 default.
        self.assertIn("CAPSULES_OPENAI_API_URL=http://127.0.0.1:9803", on[0])
        self.assertEqual(r.stdout.count("CAPSULES_OPENAI_API_URL"), 1)
        # The fault is on the faulty twin's config alone, and is a system prompt, not a sampler change.
        self.assertEqual(r.stdout.count("[models.request_defaults]"), 1)
        self.assertEqual(r.stdout.count('system_prompt = "Ignore the question and reply with the single word PINEAPPLE."'), 1)
        # Every serving node (twin A lab-1, referee lab-2, twin B lab-4) loads the ref the same way, from config; the CLI names none.
        self.assertEqual(r.stdout.count('model = "owner/repo/model-q4.gguf"'), 3)
        self.assertNotIn("--model ", r.stdout)
        self.assertNotIn("model_path", r.stdout)  # an HF ref has no local path
        self.assertNotIn("--gguf", r.stdout)
        for forbidden in ("temperature =", "seed =", "top_k ="):
            self.assertNotIn(forbidden, r.stdout)

    def test_lab_twins_needs_a_hf_ref_not_a_local_file(self):
        env = {k: v for k, v in self.env.items() if k != "LAB_MODEL_REF"}
        r = lab_sh("start", env={**env, "LAB_TWINS": "1"})
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("LAB_TWINS needs LAB_MODEL_REF", r.stderr)

    def test_a_hf_ref_is_listed_under_the_name_lab_1_reports_not_the_ref(self):
        r = lab_sh("--dry-run", "start", env={**self.env, "LAB_TWINS": "1"})
        self.assertIn("served model name", (ROOT / "lab" / "lab.sh").read_text())
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_the_pair_is_complete_only_when_each_provider_half_with_the_digest_is_held(self):
        led = self.tmp / "led"
        led.mkdir()
        def poc(node, extra=None):  # the record shape the pinned plugin reads (referee/live.rs poc_text)
            return {"model_attestation": {"compute_attestation": {"x-mesh-poc-v1": {
                "serving_provenance": {"served_by_node_id": node}, **(extra or {})}}}}
        own = [dict(poc("A" * 8, {"twin_bracket_id": "b-1"}), effect={"request_digest": "d1"}),
               dict(poc("B" * 8, {"twin_bracket_id": "b-1"}), effect={"request_digest": "d1"})]
        (led / "capsules.jsonl").write_text("".join(json.dumps(r) + "\n" for r in own))
        script = 'eval "$(sed -n "/^halves_held()/,/^}/p" "$LAB")"; halves_held b-1 "$LED"'
        def held():
            return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                                  env={**os.environ, "LAB": str(LAB), "LED": str(led)}).returncode
        self.assertEqual(held(), 1)  # no pushed halves, no file
        rec = lambda node, d: json.dumps(dict(poc(node), effect={"request_digest": d})) + "\n"
        (led / "received-capsules.jsonl").write_text(rec("A" * 8, "d1"))
        self.assertEqual(held(), 1)  # only one twin's half
        (led / "received-capsules.jsonl").write_text(rec("A" * 8, "d1") + rec("B" * 8, "other"))
        self.assertEqual(held(), 1)  # a half of another exchange does not count
        (led / "received-capsules.jsonl").write_text(rec("A" * 8, "d1") + rec("B" * 8, "d1"))
        self.assertEqual(held(), 0)

    def test_start_waits_for_the_announced_keys_and_takes_the_advertised_name(self):
        r = lab_sh("--dry-run", "start", env={**self.env, "LAB_TWINS": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        for n in ("lab-1", "lab-2", "lab-3", "lab-4"):
            self.assertIn(f"{n}'s host announces the other nodes' plugin keys", r.stdout)
        self.assertIn("LAB_KEY_SETTLE", r.stdout)
        text = (ROOT / "lab" / "lab.sh").read_text()
        self.assertIn("advertised_name", text)

    def _bash(self, body, status):
        script = ('eval "$(sed -n "/^keys_announced()/,/^}/p;/^advertised_name()/,/^}/p" "$LAB")"; console() { echo "http://x/$1"; }; '
                  f'curl() {{ cat <<\'J\'\n{status}\nJ\n}}; NODES=(lab-1 lab-2 lab-3); {body}')
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env={**os.environ, "LAB": str(LAB)})

    def test_keys_announced_needs_every_other_nodes_plugin_key(self):
        keys = lambda n: json.dumps({"node_id": "a", "peers": {f"p{i}": {"capsules": "a" * 64} for i in range(n)}})
        self.assertEqual(self._bash("keys_announced lab-1", keys(1)).returncode, 1)
        self.assertEqual(self._bash("keys_announced lab-1", keys(2)).returncode, 0)
        withdrawn = json.dumps({"peers": {"p0": {"capsules": "a" * 64}, "p1": {}}})
        self.assertEqual(self._bash("keys_announced lab-1", withdrawn).returncode, 1)
        not_a_key = json.dumps({"peers": {"p0": {"capsules": "a" * 64}, "p1": {"capsules": "K" * 64}}})
        self.assertEqual(self._bash("keys_announced lab-1", not_a_key).returncode, 1)  # the plugin keeps only 64 lowercase hex

    def test_rejected_pushes_are_counted_and_explained_without_shell_noise(self):
        f = self.tmp / "rej.jsonl"
        script = ('eval "$(sed -n "/^rejected_count()/p;/^rejected_reasons()/,/^}/p" "$LAB")"; '
                  'echo "count=$(rejected_count "$F")"; rejected_reasons "$F" 1')
        run = lambda: subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                                     env={**os.environ, "LAB": str(LAB), "F": str(f)})
        r = run()  # no file: zero, silent
        self.assertEqual((r.stdout.strip(), r.stderr), ("count=0", ""))
        f.write_text('{"reason":"old"}\n{"reason":"signature_unverified"}\n{"reason":"model_mismatch"}\n')
        r = run()
        self.assertEqual(r.stdout.split(), ["count=3", "signature_unverified", "model_mismatch"])  # only lines past the first

    def test_twins_wait_explains_a_timeout_by_the_refusals_logged_during_the_run(self):
        f = self.tmp / "rej.jsonl"
        script = ('eval "$(sed -n "/^rejected_count()/p;/^rejected_reasons()/,/^}/p;/^twins_wait()/,/^}/p" "$LAB")"; DRY=0; '
                  f'wait_for() {{ return $WAIT; }}; TWINS_REJ="$F"; TWINS_REJ_BEFORE=1; twins_wait 1 "the halves" true; echo "rc=$?"')
        def run(wait):
            return subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                                  env={**os.environ, "LAB": str(LAB), "F": str(f), "WAIT": str(wait)})
        self.assertEqual(run(0).stdout.strip(), "rc=0")  # the wait passed: nothing said
        r = run(1)  # timed out, no rejections: just the failure
        self.assertEqual((r.stdout.strip(), r.stderr), ("rc=1", ""))
        f.write_text('{"reason":"stale_reason"}\n{"reason":"model_mismatch"}\n')
        r = run(1)  # another reason: reported, but no key advice
        self.assertIn("model_mismatch", r.stderr)
        self.assertNotIn("had not read each other's keys", r.stderr)
        self.assertNotIn("stale_reason", r.stderr)  # earlier than this run
        f.write_text('{"reason":"stale_reason"}\n{"reason":"signature_unverified"}\n')
        r = run(1)
        self.assertIn("signature_unverified", r.stderr)
        self.assertIn("had not read each other's keys", r.stderr)
        self.assertEqual(r.stdout.strip(), "rc=1")

    def test_the_asking_node_is_the_requester_with_twins_and_lab_2_otherwise(self):
        text = (ROOT / "lab" / "lab.sh").read_text()
        self.assertIn("asker=lab-2", text)
        self.assertIn("asker=$REQUESTER", text)
        self.assertNotIn("REQUESTER_OR_REPORTER", text)

    def test_the_key_settle_default_covers_one_plugin_read(self):
        self.assertIn("KEY_SETTLE=${LAB_KEY_SETTLE:-40}", (ROOT / "lab" / "lab.sh").read_text())

    def test_the_advertised_name_is_the_first_a_peer_serves(self):
        status = json.dumps({"peers": [{"serving_models": []},
                                       {"serving_models": ["Qwen/Qwen2.5-0.5B-Instruct-GGUF:q4_k_m"]}]})
        r = self._bash("advertised_name lab-3", status)
        self.assertEqual(r.stdout.strip(), "Qwen/Qwen2.5-0.5B-Instruct-GGUF:q4_k_m", r.stderr)
        self.assertEqual(self._bash("advertised_name lab-3", json.dumps({"peers": []})).returncode, 1)

    def test_join_skips_a_node_that_has_already_joined(self):
        # join.sh fails on a node with a book profile already: a second `join` must not repeat lab-1..3.
        script = 'eval "$(sed -n "/^cmd_join()/,/^}/p" "$LAB")"; die() { echo "$*"; exit 2; }; DRY=0; ROOT=/nonexistent; ' \
                 'NODES=(lab-1 lab-2); run() { echo "RUN $*"; }; data_of() { echo "$LAB_DIR/data/$1"; }; ' \
                 'home_of() { echo "$LAB_DIR/home/$1"; }; mkdir -p "$LAB_DIR/data/lab-1/keys" "$LAB_DIR/data/lab-2/keys" "$LAB_DIR/join/lab-1"; ' \
                 'touch "$LAB_DIR/join/lab-1/doctor.json" "$LAB_DIR/data/lab-1/keys/node-key.pem" "$LAB_DIR/data/lab-2/keys/node-key.pem"; cmd_join'
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                           env={**os.environ, "LAB": str(LAB), "LAB_DIR": str(self.tmp / "labdir")})
        self.assertIn("lab-1 already joined", r.stdout, r.stderr)
        self.assertNotIn("--name lab-1", r.stdout)
        self.assertIn("--name lab-2", r.stdout)

    def test_lab_twins_needs_four_distinct_nodes(self):
        r = lab_sh("--dry-run", "start", env={**self.env, "LAB_TWINS": "1", "LAB_MODEL_REF": "owner/repo/model-q4.gguf", "LAB_REFEREE": "lab-1"})
        self.assertEqual(r.returncode, 2)
        self.assertIn("four distinct nodes", r.stderr)

    def test_lab_twins_makes_the_twins_and_the_referee_push_their_halves_to_the_requester(self):
        r = lab_sh("--dry-run", "start", env={**self.env, "LAB_TWINS": "1", "LAB_MODEL_REF": "owner/repo/model-q4.gguf"})
        push = {n for n in ("lab-1", "lab-2", "lab-3", "lab-4")
                if any(f"HOME={self.lab}/nodes/{n} " in l and "CAPSULES_SHARE_RECORD_AT_COMPLETION=counterparty" in l
                       for l in r.stdout.splitlines())}
        self.assertEqual(push, {"lab-1", "lab-2", "lab-4"})  # not the requester lab-3

    def test_stop_includes_lab_4_without_lab_twins(self):
        r = lab_sh("--dry-run", "stop", env=self.env)
        self.assertIn("lab-4.pid", r.stdout)

    def test_lab_twins_refuses_a_fault_that_would_not_apply_or_not_parse(self):
        for env, text in (({"LAB_FAULT_SYSTEM_PROMPT": 'Answer "bleu".'}, "double quote"),
                          ({"LAB_MODEL_REF": "owner/repo\\file.gguf"}, "backslash")):
            r = lab_sh("--dry-run", "start", env={**self.env, "LAB_TWINS": "1", "LAB_MODEL_REF": "owner/repo/model-q4.gguf", **env})
            self.assertEqual(r.returncode, 2, r.stdout)
            self.assertIn(text, r.stderr)

    def test_the_twins_command_asks_the_referee_then_sends_one_bracket_to_both_twins(self):
        r = lab_sh("--dry-run", "twins", env={**self.env, "LAB_TWINS": "1", "LAB_MODEL_REF": "owner/repo/model-q4.gguf"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.count("x-mesh-twin-bracket:"), 2)
        self.assertEqual(r.stdout.count("http://127.0.0.1:9803/v1/chat/completions"), 3)
        self.assertIn("twin-fault.json", r.stdout)
        # Stock mesh-llm passes plugins no exchange text: the client supplies the pair's request and answers.
        self.assertIn("referee_supply_twin_texts", r.stdout)
        # Each run has its own request digest: the bracket (a timestamp) is in the prompt.
        self.assertRegex(r.stdout, r"colour\.\\ \\\(run\\ lab-twins-\d{8}T\d{6}Z")

    def test_dispute_runs_step_09_read_only_on_the_live_population(self):
        r = lab_sh("--dry-run", "dispute", env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("steps/09-dispute --population", r.stdout)
        self.assertIn(str(ROOT / "population" / "live"), r.stdout)
        # With LAB_TWINS the four-node population `entries` wrote under the lab dir is read.
        r = lab_sh("--dry-run", "dispute", env={**self.env, "LAB_TWINS": "1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"{self.lab}/population-twins", r.stdout)
        r = lab_sh("dispute", env={**self.env, "LAB_TWINS": "1"})  # not dry: no twins population yet
        self.assertEqual(r.returncode, 2)
        self.assertIn("run LAB_TWINS=1 entries first", r.stderr)

    def test_port_base_moves_every_port(self):
        r = lab_sh("--dry-run", "start", env={**self.env, "LAB_PORT_BASE": "9900"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--port 9903 --console 9913 --bind-port 9923", r.stdout)
        self.assertNotIn("--port 980", r.stdout)

    def test_a_lab_1_reporter_is_asked_through_lab_2s_console(self):
        # LOOP_SELF_NODE is the label population/live gives the lab-1 directory (stevenmih-1).

        r = lab_sh("--dry-run", "loop", env={**self.env, "LAB_REPORTER": "lab-1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("env LOOP_CONSOLE=http://127.0.0.1:9811 LOOP_SELF_NODE=stevenmih-1 "
                      "LOOP_SECOND_CONSOLE=http://127.0.0.1:9812 ", r.stdout)

    def test_no_node_is_told_to_push_unless_named(self):
        # Unnamed nodes keep the plugin's own default (on at the pin), not a value lab.sh sets.
        self.assertNotIn("CAPSULES_SHARE_RECORD_AT_COMPLETION", self.out)

    def test_share_at_completion_reaches_only_the_named_nodes(self):
        for names in ("lab-1 lab-2", "lab-1\tlab-2", "\nlab-2\n lab-1 "):
            r = lab_sh("--dry-run", "start", env={**self.env, "LAB_SHARE_AT_COMPLETION": names})
            self.assertEqual(r.returncode, 0, r.stderr)
            launches = {n: l for l in r.stdout.splitlines() if " serve --config " in l
                        for n in ("lab-1", "lab-2", "lab-3") if f"HOME={self.lab}/nodes/{n} " in l}
            self.assertEqual(sorted(launches), ["lab-1", "lab-2", "lab-3"])
            for n, line in launches.items():
                self.assertEqual("CAPSULES_SHARE_RECORD_AT_COMPLETION=counterparty" in line, n != "lab-3",
                                 (repr(names), n))

    def test_share_at_completion_refuses_an_unknown_node(self):
        r = lab_sh("--dry-run", "start", env={**self.env, "LAB_SHARE_AT_COMPLETION": "lab-1 lab-9"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("names lab-9, not a lab node", r.stderr)

    def test_deal_runs_step_05_on_the_live_population_into_the_days_findings(self):
        r = lab_sh("--dry-run", "deal", env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, rf"env LAB_DIR={re.escape(str(self.lab))} \S+/steps/05-deal --population "
                                   rf"\S+/population/live --out \S+/findings/\d{{4}}-\d{{2}}-\d{{2}}-live/05-deal\n")
        self.assertRegex(r.stdout, r"steps/finding\.py check \S+/findings/\d{4}-\d{2}-\d{2}-live\n")
        self.assertNotIn("/v1/chat/completions", r.stdout)
        self.assertFalse(self.lab.exists(), "a dry run created LAB_DIR")

    def test_settle_runs_step_06_on_the_live_population_into_the_days_findings(self):
        r = lab_sh("--dry-run", "settle", env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, rf"env LAB_DIR={re.escape(str(self.lab))} \S+/steps/06-settle --population "
                                   rf"\S+/population/live --out \S+/findings/\d{{4}}-\d{{2}}-\d{{2}}-live/06-settle\n")
        self.assertRegex(r.stdout, r"steps/finding\.py check \S+/findings/\d{4}-\d{2}-\d{2}-live\n")
        self.assertFalse(self.lab.exists(), "a dry run created LAB_DIR")

    def test_ask_runs_step_07_on_the_live_population_asked_as_the_loop_asks(self):
        r = lab_sh("--dry-run", "ask", env={**self.env, "LAB_REPORTER": "lab-1"})
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, rf"env LOOP_CONSOLE=http://127.0.0.1:9811 LOOP_SELF_NODE=stevenmih-1 "
                                   rf"LOOP_SECOND_CONSOLE=http://127.0.0.1:9812 LAB_DIR={re.escape(str(self.lab))} "
                                   rf"LOOP_SECOND_DATA_DIR={re.escape(str(self.lab))}/nodes/lab-2/\S+ "
                                   rf"\S+/steps/07-ask --population \S+/population/live --out "
                                   rf"\S+/findings/\d{{4}}-\d{{2}}-\d{{2}}-live/07-ask\n")
        self.assertRegex(r.stdout, r"steps/finding\.py check \S+/findings/\d{4}-\d{2}-\d{2}-live\n")
        self.assertFalse(self.lab.exists(), "a dry run created LAB_DIR")


class LabRoot(unittest.TestCase):
    """A copy of lab/ and scripts/ in a scratch root with its own versions.lock and steps/,
    so lab.sh check and tools run for real against throwaway git trees and fake setups."""

    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git is not installed: lab.sh check cannot be run")
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.root, self.lab = self.tmp / "root", self.tmp / "lab"
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
        for d in ("lab", "scripts"):
            shutil.copytree(ROOT / d, self.root / d, ignore=ignore)
        (self.root / "steps").mkdir()
        # mesh-llm: the main pin, then the PR pin on top of it.
        self.mesh = new_tree(self.tmp / "mesh-llm", 2)
        self.cap = new_tree(self.tmp / "capsules", 1)
        git(self.tmp / "capsules", "tag", "v0.1.5")
        for name, body in (("mesh-llm", "#!/bin/sh\necho mesh-llm 0.0.0-test\n"), ("capsules", "plugin\n")):
            f = self.tmp / "bin" / name
            f.parent.mkdir(exist_ok=True)
            f.write_text(body)
            f.chmod(0o755)
        self.env = {"LAB_DIR": str(self.lab), "MESH_LLM_SRC": str(self.tmp / "mesh-llm"),
                    "MESH_LLM_BIN": str(self.tmp / "bin/mesh-llm"), "CAPSULES_SRC": str(self.tmp / "capsules"),
                    "CAPSULES_BIN": str(self.tmp / "bin/capsules")}
        self.lock(self.mesh[0], self.mesh[1], "v0.1.5")

    def lock(self, mesh, pr, cap):
        (self.root / "versions.lock").write_text(
            "# test pins\n"
            f"mesh-llm Mesh-LLM/mesh-llm {mesh}\nmesh-llm-pr-2620 Mesh-LLM/mesh-llm {pr}\n"
            f"capsules Mesh-LLM/capsules-plugin {cap}\n")

    def run_lab(self, *args, **env):
        return lab_sh(*args, env={**self.env, **env}, root=self.root)

    def build(self):
        return json.loads((self.lab / "build.json").read_text())

    def setup_script(self, name, body):
        f = self.root / "steps" / f"{name}.setup.sh"
        f.write_text("#!/usr/bin/env bash\n" + body + "\n")
        f.chmod(0o755)


class CheckTest(LabRoot):
    def assert_fails_with(self, r, text):
        self.assertEqual(r.returncode, 1, r.stderr)
        build = self.build()
        self.assertIs(build["ok"], False)
        self.assertTrue(any(text in f for f in build["failures"]), build["failures"])
        self.assertIn(text, r.stderr)

    def test_a_tag_pin_resolves_to_its_commit(self):
        r = self.run_lab("check")
        self.assertEqual(r.returncode, 0, r.stderr)
        build = self.build()
        self.assertIs(build["ok"], True, build["failures"])
        self.assertEqual(build["capsules"]["pin"], "v0.1.5")
        self.assertEqual(build["capsules"]["pin_commit"], self.cap[0])
        self.assertEqual(build["capsules"]["source_head"], self.cap[0])
        self.assertEqual(build["mesh_llm"]["version"], "mesh-llm 0.0.0-test")
        host_build, plugin = pack.build_strings(build)
        self.assertEqual(plugin.split(",")[0], f"capsules v0.1.5 ({self.cap[0]})")

    def test_a_tag_pin_is_fetched_when_the_tree_lacks_it(self):
        clone = self.tmp / "capsules-clone"
        subprocess.run(["git", "clone", "-q", "--no-tags", str(self.tmp / "capsules"), str(clone)], check=True)
        self.assertEqual(git(clone, "tag"), "")
        r = self.run_lab("check", CAPSULES_SRC=str(clone))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.build()["capsules"]["pin_commit"], self.cap[0])

    def test_a_tree_past_the_tag_fails(self):
        git(self.tmp / "capsules", "commit", "-q", "--allow-empty", "-m", "past the tag")
        self.assert_fails_with(self.run_lab("check"), f"the capsules pin v0.1.5 is {self.cap[0]}")

    def test_a_missing_tag_fails(self):
        self.lock(self.mesh[0], self.mesh[1], "v9.9.9")
        self.assert_fails_with(self.run_lab("check"), "the capsules pin v9.9.9 is not a tag")

    def test_a_full_sha_pin_compares_as_is(self):
        self.lock(self.mesh[0], self.mesh[1], self.cap[0])
        r = self.run_lab("check")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.build()["capsules"]["pin_commit"], self.cap[0])
        self.lock(self.mesh[0], self.mesh[1], "0" * 40)
        self.assert_fails_with(self.run_lab("check"), f"the capsules pin {'0' * 40} is {'0' * 40}")

    def test_a_mesh_pin_not_in_the_tree_writes_build_json(self):
        self.lock("1" * 40, self.mesh[1], "v0.1.5")
        self.assert_fails_with(self.run_lab("check"), f"the mesh-llm pin {'1' * 40} is not in the mesh-llm source")

    def test_a_mesh_tree_without_the_pr_pin_writes_build_json(self):
        git(self.tmp / "mesh-llm", "checkout", "-q", self.mesh[0])
        self.assert_fails_with(self.run_lab("check"), f"does not contain the mesh-llm-pr-2620 pin {self.mesh[1]}")

    def test_unset_paths_and_missing_binaries_write_build_json(self):
        self.assert_fails_with(self.run_lab("check", MESH_LLM_SRC="", CAPSULES_BIN=str(self.tmp / "nope")),
                               "MESH_LLM_SRC is not set")
        self.assertIn(f"CAPSULES_BIN {self.tmp / 'nope'} is not a readable file", self.build()["failures"])

    def test_a_failed_check_replaces_an_ok_build(self):
        self.assertEqual(self.run_lab("check").returncode, 0)
        self.lock(self.mesh[0], self.mesh[1], "v9.9.9")
        self.assert_fails_with(self.run_lab("check"), "v9.9.9")


class ToolsTest(LabRoot):
    def test_every_setup_runs_and_each_failure_is_recorded(self):
        self.assertEqual(self.run_lab("check").returncode, 0)
        ran = self.tmp / "ran"
        self.setup_script("01-a", f"echo 01 >> {ran}")
        self.setup_script("02-b", f"echo 02 >> {ran}; exit 3")
        self.setup_script("12-c", f"echo 12 >> {ran}; exit 5")
        r = self.run_lab("tools")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(ran.read_text().split(), ["01", "02", "12"])
        self.assertIn("02-b.setup.sh FAILED (exit 3)", r.stderr)
        self.assertIn("12-c.setup.sh FAILED (exit 5)", r.stderr)
        build = self.build()
        self.assertIs(build["ok"], False)
        self.assertEqual(build["tools"]["setups"], [{"name": "01-a.setup.sh", "exit": 0},
                                                    {"name": "02-b.setup.sh", "exit": 3},
                                                    {"name": "12-c.setup.sh", "exit": 5}])
        self.assertEqual(build["failures"], ["tools: steps/02-b.setup.sh exited 3", "tools: steps/12-c.setup.sh exited 5"])
        # The check keeps the tools record: it fails, naming the setups, until tools passes.
        r = self.run_lab("check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("tools: steps/02-b.setup.sh exited 3", r.stderr)
        self.setup_script("02-b", "exit 0")
        self.setup_script("12-c", "exit 0")
        self.assertEqual(self.run_lab("tools").returncode, 0)
        self.assertIs(self.build()["ok"], True, self.build()["failures"])

    def test_tools_before_check_is_not_ok(self):
        self.setup_script("01-a", "exit 0")
        self.assertEqual(self.run_lab("tools").returncode, 0)
        self.assertEqual(self.build()["failures"], ["lab/lab.sh check has not run"])


@unittest.skipUnless(shutil.which("tmux"), "tmux is not installed: lab.sh start cannot be run")
class StartTest(LabRoot):
    """lab.sh start for real, in a tmux session of its own, with a fake mesh-llm that dies:
    start must stop within seconds and show the node's log tail, not wait out its 600 s."""

    def setUp(self):
        super().setUp()
        self.session = f"loop-lab-test-{os.getpid()}-{id(self)}"
        self.addCleanup(subprocess.run, ["tmux", "kill-session", "-t", self.session], capture_output=True)
        self.lab.mkdir()
        (self.lab / "build.json").write_text(json.dumps(BUILD))

    def start(self, body, **env):
        mesh = self.tmp / "bin" / "mesh-llm"
        mesh.write_text("#!/bin/sh\n" + body + "\n")
        mesh.chmod(0o755)
        return self.run_lab("start", LAB_MODEL=str(self.tmp / "model.gguf"), LAB_TMUX_SESSION=self.session,
                            LAB_PORT_BASE=str(20000 + os.getpid() % 20000), **env)

    def test_a_node_that_exits_at_once_is_reported_within_10s(self):
        t = time.monotonic()
        r = self.start("echo 'boom: planted failure' >&2; exit 1")
        self.assertLess(time.monotonic() - t, 10, r.stderr)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("lab-1 is not running", r.stderr)
        self.assertIn("boom: planted failure", r.stderr)
        self.assertNotIn("timed out", r.stderr)

    def test_a_node_that_dies_while_start_waits_stops_the_wait(self):
        t = time.monotonic()
        r = self.start("sleep 4; echo 'gone: planted failure' >&2; exit 1", LAB_UP_SECONDS="2")
        self.assertLess(time.monotonic() - t, 20, r.stderr)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("started lab-1", r.stdout)
        self.assertIn("gone: planted failure", r.stderr)
        self.assertIn("stopped waiting: lab-1 has loaded the model", r.stderr)

    def test_tmux_runs_the_launcher_through_bash_into_the_log(self):
        # Never the bare path: where the script cannot be executed (a noexec mount), a bare path
        # closes the window before the launcher's first line, with an empty log.
        r = self.run_lab("--dry-run", "start", LAB_MODEL="/opt/model.gguf", LAB_TMUX_SESSION=self.session)
        for name in ("lab-1", "lab-2", "lab-3"):
            launcher, log = self.lab / "run" / f"{name}.sh", self.lab / "logs" / f"{name}.log"
            self.assertIn(f"-n {name} bash\\ {launcher}\\ \\>\\>\\ {log}\\ 2\\>\\&1", r.stdout)


class RunbookTest(unittest.TestCase):
    """LAB-BRINGUP.md section 5 matches loop.sh's step files and the live steps' inputs."""

    def setUp(self):
        text = (ROOT / "LAB-BRINGUP.md").read_text()
        self.section = text.split("## 5. Stage 1, once", 1)[1].split("\n## 6.", 1)[0]

    def test_one_step_file_per_stage_1_step(self):
        # loop.sh's own rule: a step is a file named exactly <NN>-<name>.
        loop = (ROOT / "loop.sh").read_text()
        self.assertIn("=~ ^[0-9]{2}-[a-z][a-z0-9-]*$", loop)
        for nn in ("01", "02", "03", "12"):
            steps = [p.name for p in (ROOT / "steps").glob(f"{nn}-*")
                     if p.is_file() and re.fullmatch(r"[0-9]{2}-[a-z][a-z0-9-]*", p.name)]
            self.assertEqual(len(steps), 1, steps)
            self.assertIn(f"`{steps[0].replace('-', ' ', 1)}`", self.section)

    def test_section_5_names_the_live_inputs_not_the_old_failures(self):
        for text in ("data_dir", "book_dir", "population/live/population.json", "LAB_DIR"):
            self.assertIn(text, self.section)
        self.assertNotIn("second executable", self.section)
        self.assertNotIn("read a test key", self.section)


class LabFixture(unittest.TestCase):
    """A fake LAB_DIR (three plugin data dirs with TEST public keys) and a fake repository root."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.lab, self.root = self.tmp / "lab", self.tmp / "root"
        for d in ("population/live", "reporter", "nodes"):
            (self.root / d).mkdir(parents=True)
        # The population before any lab run: names, and every id and dir null.
        shutil.copy(ROOT / "tests/fixtures/live/null/population.json", self.root / "population/live/population.json")
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

    def _lab_4(self):
        d = pack.data_dir(self.lab, "lab-4")
        (d / "keys").mkdir(parents=True)
        subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(self.tmp / "k4.pem")], check=True)
        subprocess.run(["openssl", "pkey", "-in", str(self.tmp / "k4.pem"), "-pubout", "-out",
                        str(d / "keys" / "node-key.pub.pem")], check=True)
        (d / "self-peer-id").write_text("4" * 64 + "\n")

    def test_add_host_needs_the_twins_only_nodes_identity(self):
        with contextlib.redirect_stdout(io.StringIO()):
            errs = pack.entries(self.root, self.lab, "lab-operator", "lab-3", add_hosts=["lab-4"])
        self.assertEqual(len(errs), 1, errs)
        self.assertIn("lab-4", errs[0])  # named as the others are, then its missing key is reported

    def test_a_twins_only_node_stays_out_of_the_committed_population_and_nodes(self):
        self._lab_4()
        before = (self.root / "population" / "live" / "population.json").read_text()
        with contextlib.redirect_stdout(io.StringIO()):
            errs = pack.entries(self.root, self.lab, "lab-operator", "lab-3", add_hosts=["lab-4"])
        self.assertEqual(errs, [])
        committed = json.loads((self.root / "population" / "live" / "population.json").read_text())["nodes"]
        self.assertEqual(len(committed), 3)  # no lab-4 in population/live
        self.assertEqual(len(list((self.root / "nodes").glob("*.json"))), 3)  # and no nodes/ entry for it
        self.assertNotIn("4" * 64, before + json.dumps(committed))
        twins = json.loads((self.lab / "population-twins" / "population.json").read_text())["nodes"]
        self.assertEqual(len(twins), 4)  # the four-node population lives under the lab dir
        self.assertEqual(twins[3]["data_dir"], "$LAB_DIR/nodes/lab-4/.local/share/capsules")
        self.assertEqual(twins[3]["node_id"], "4" * 64)
        self.assertEqual(twins[3]["name"].rsplit("-", 1)[0], twins[0]["name"].rsplit("-", 1)[0])  # the others' casing
        self.assertTrue(all(n.get("node_id") for n in twins))

    def test_entries_dry_run_names_the_twins_population_outside_the_root_and_writes_nothing(self):
        self._lab_4()
        (self.lab / "build.json").write_text(json.dumps(BUILD))
        before = tree_digest(self.root)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            errs = pack.entries(self.root, self.lab, "lab-operator", "lab-3", dry_run=True, add_hosts=["lab-4"])
        self.assertEqual(errs, [])
        self.assertIn(f"{self.lab}/population-twins/population.json", out.getvalue())
        self.assertEqual(tree_digest(self.root), before)
        self.assertFalse((self.lab / "population-twins").exists())

    def test_the_pack_carries_the_twins_population_and_lab_4s_join(self):
        self._lab_4()
        (self.lab / "build.json").write_text(json.dumps(BUILD))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(pack.entries(self.root, self.lab, "lab-operator", "lab-3", add_hosts=["lab-4"]), [])
        j = self.lab / "join" / "lab-4"
        j.mkdir(parents=True)
        (j / "commitment.json").write_text("{}")
        files, _ = pack.plan(self.root, self.lab, DAY)
        arcs = {a for a, _ in files}
        self.assertIn(f"lab/runs/{DAY}/population-twins/population.json", arcs)
        self.assertTrue(any("/join/" in a and a.endswith("-4/commitment.json") for a in arcs), sorted(arcs))

    def test_a_later_entries_run_without_the_twins_node_drops_the_old_twins_population(self):
        self._lab_4()
        (self.lab / "build.json").write_text(json.dumps(BUILD))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(pack.entries(self.root, self.lab, "lab-operator", "lab-3", add_hosts=["lab-4"]), [])
        self.assertTrue((self.lab / "population-twins" / "population.json").is_file())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(pack.entries(self.root, self.lab, "lab-operator", "lab-3"), [])
        self.assertFalse((self.lab / "population-twins" / "population.json").exists())
        files, _ = pack.plan(self.root, self.lab, DAY)
        self.assertFalse(any("population-twins" in a for a, _ in files))

    def test_a_missing_twins_only_join_is_reported_by_the_pack(self):
        self._lab_4()
        (self.lab / "build.json").write_text(json.dumps(BUILD))
        with contextlib.redirect_stdout(io.StringIO()):
            pack.entries(self.root, self.lab, "lab-operator", "lab-3", add_hosts=["lab-4"])
        _, missing = pack.plan(self.root, self.lab, DAY)
        self.assertTrue(any("LAB_TWINS=1 lab/lab.sh join" in m for m in missing), missing)

    def test_the_reporter_cannot_be_the_twins_only_node(self):
        r = lab_sh("entries", env={"LAB_DIR": str(self.lab), "LAB_REPORTER": "lab-4", "LAB_OPERATOR": "x"})
        self.assertEqual(r.returncode, 2)
        self.assertIn("twins-only", r.stderr)

    def test_without_add_host_no_twins_population_is_written(self):
        self.assertEqual(self.entries(), [])
        self.assertFalse((self.lab / "population-twins").exists())

    def test_the_twin_exchange_wait_reads_the_ledger_on_every_retry(self):
        led = self.tmp / "capsules.jsonl"
        script = ('eval "$(sed -n "/^sealed_twice()/p" "$LAB")"; '
                  '(sleep 1; echo b-1 >> "$LED"; echo b-1 >> "$LED") & '
                  'for i in 1 2 3 4 5 6 7 8; do sealed_twice b-1 "$LED" && echo seen && exit 0; sleep 0.5; done; echo never')
        r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env={**os.environ, "LAB": str(LAB), "LED": str(led)})
        self.assertEqual(r.stdout.strip(), "seen", r.stderr)

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


class LabelTest(LabFixture):
    """A node's name is its operator's label; its directories on the host keep lab-N (from data_dir)."""

    def test_labelled_population_reads_and_packs_the_lab_n_directories(self):
        pop_path = self.root / "population/live/population.json"
        pop = json.loads(pop_path.read_text())
        for i, n in enumerate(pop["nodes"], 1):
            n["name"] = f"operator-{i}"
            n["data_dir"] = f"$LAB_DIR/nodes/lab-{i}/.local/share/capsules"
            n["book_dir"] = f"$LAB_DIR/nodes/lab-{i}/join-book"
        pop_path.write_text(json.dumps(pop))
        self.assertEqual(self.entries(), [])
        written = json.loads(pop_path.read_text())["nodes"]
        self.assertEqual([n["name"] for n in written], ["operator-1", "operator-2", "operator-3"])
        self.assertEqual([n["node_id"] for n in written], [self.ids[f"lab-{i}"] for i in (1, 2, 3)])
        self.assertTrue(all(n["data_dir"].startswith(f"$LAB_DIR/nodes/lab-{i}/") for i, n in enumerate(written, 1)))
        (self.lab / "join" / "lab-1").mkdir(parents=True)
        (self.lab / "join" / "lab-1" / "join.json").write_text("{}\n")
        files, _ = pack.plan(self.root, self.lab, DAY)
        self.assertIn(f"lab/runs/{DAY}/join/operator-1/join.json", [a for a, _ in files])


class PackTest(LabFixture):
    def test_the_controllers_dir_is_never_packed_even_with_a_hex_nostr_key_in_it(self):
        (self.lab / "controllers" / "lab-1").mkdir(parents=True)
        (self.lab / "controllers" / "lab-1" / "nostr.key").write_text("ab" * 32 + "\n")
        files, _ = pack.plan(self.root, self.lab, DAY)
        self.assertFalse(any("controllers" in a or "nostr" in a for a, _ in files), files)

    def setUp(self):
        super().setUp()
        pop_path = self.root / "population/live/population.json"
        pop = json.loads(pop_path.read_text())
        for n in pop["nodes"]:
            n["node_id"] = self.ids[n["name"]]
            (self.root / "nodes" / f"{n['node_id']}.json").write_text("{}\n")
            (self.root / "nodes" / "commitments" / "policies").mkdir(parents=True, exist_ok=True)
            (self.root / "nodes" / "commitments" / f"{n['node_id']}.json").write_text("{}\n")
            (self.root / "nodes" / "commitments" / "policies" / f"{n['node_id']}.json").write_text("{}\n")
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
            self.assertIn(f"nodes/commitments/{node_id}.json", names)
            self.assertIn(f"nodes/commitments/policies/{node_id}.json", names)
        for arc in ("population/live/population.json", "reporter/reporter.json",
                    f"findings/{DAY}-live/12-join/finding.json", f"findings/{DAY}-live/run.json",
                    f"lab/runs/{DAY}/build.json", f"lab/runs/{DAY}/join/lab-2/commitment.json"):
            self.assertIn(arc, names)
        self.assertFalse([n for n in names if n.endswith((".seed", ".pem"))], names)
        self.assertEqual(set(manifest["files"]) | {f"lab/runs/{DAY}/MANIFEST.json"}, names)

    def test_missing_commitments_stop_the_pack_naming_them(self):
        node_id = next(iter(self.ids.values()))
        (self.root / "nodes" / "commitments" / f"{node_id}.json").unlink()
        errs = self.pack()
        self.assertTrue(any(f"nodes/commitments/{node_id}.json" in e for e in errs), errs)

    def test_refuses_a_nostr_key_in_a_commitment(self):
        node_id = next(iter(self.ids.values()))
        (self.root / "nodes" / "commitments" / f"{node_id}.json").write_text('{"controller": "npub1' + "q" * 58 + '"}\n')
        errs = self.pack()
        self.assertTrue(errs and "Nostr key" in errs[0], errs)

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
