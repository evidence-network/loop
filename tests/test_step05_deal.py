"""steps/05-deal: both halves of one deal, pushed, held by digest and cited, checked on the proof bytes.

The rehearsal verdicts come from the driver steps/05-deal.setup.sh installs (a
Rust build of evidencebook's crate at the pinned tag); the live ones also need
the AAC Go verifier step 01's setup installs. Without them these tests SKIP,
saying so: they never pass without the verifier. With LOOP_REQUIRE_TOOLS=1
(CI, after the setups ran) a missing tool is an error instead.
"""
import copy
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
STEP = ROOT / "steps" / "05-deal"
FIXTURES = ROOT / "tests" / "fixtures" / "step05"
POPULATION = FIXTURES / "rehearsal"
LIVE = FIXTURES / "live"

sys.path.insert(0, str(ROOT / "steps"))
sys.path.insert(0, str(ROOT / "scripts"))
import check_no_preimages  # noqa: E402
import finding  # noqa: E402
import live  # noqa: E402

# A live step fetches origin/main before reading nodes/revocations/ (bv.snapshot); tests never touch the network.
os.environ.setdefault("LOOP_REVOCATIONS_FETCH", "0")

_loader = importlib.machinery.SourceFileLoader("step05_deal", str(STEP))
_spec = importlib.util.spec_from_loader("step05_deal", _loader)
step = importlib.util.module_from_spec(_spec)
_loader.exec_module(step)

BIN = step.driver()
NO_DRIVER = ("SKIPPED LOUDLY: step 05's driver is not installed, so evidencebook's verifier cannot run; "
             "run steps/05-deal.setup.sh (needs cargo and crates.io)")
CROSS = step.cross_language()
NO_CROSS = ("SKIPPED LOUDLY: the AAC Go verifier is not installed, so a held half's capsule_id cannot be "
            "recomputed; run steps/01-seal.setup.sh (needs go)")
HAS_CROSS = CROSS.is_file() and os.access(CROSS, os.X_OK)
for missing, why in ((BIN is None, NO_DRIVER), (not HAS_CROSS, NO_CROSS)):
    if missing:
        if os.environ.get("LOOP_REQUIRE_TOOLS") == "1":
            raise RuntimeError(f"LOOP_REQUIRE_TOOLS=1: {why}")
        print(f"\ntest_step05_deal: {why}", file=sys.stderr)


def run_step(population, out, env=None):
    r = subprocess.run([sys.executable, "-B", str(STEP), "--population", str(population), "--out", str(out)],
                       capture_output=True, text=True, env=env)
    return r


class Step05ExchangePairingTest(unittest.TestCase):
    """Halves join on client_nonce, never on agent_input_digest (loop#66); no verifier needed."""
    OUT_A, OUT_B = "a" * 64, "b" * 64

    def test_two_exchanges_sharing_a_prompt_count_as_two(self):
        halves = {"n1": [("x1", "requested", self.OUT_A), ("x2", "requested", self.OUT_B)],
                  "n2": [("x1", "served", self.OUT_A)],
                  "n3": [("x2", "served", self.OUT_B)]}
        paired, notes = step.paired_exchanges(halves)
        self.assertEqual(paired, {"x1", "x2"})
        self.assertEqual(notes, [])

    def test_a_half_on_one_node_only_is_not_an_exchange(self):
        paired, notes = step.paired_exchanges({"n1": [("x1", "requested", self.OUT_A)], "n2": []})
        self.assertEqual(paired, set())
        self.assertIn("nonce x1 is not one exchange held on two nodes: n1 requested", notes)

    def test_two_halves_of_one_role_are_not_an_exchange(self):
        paired, notes = step.paired_exchanges({"n1": [("x1", "served", self.OUT_A)],
                                               "n2": [("x1", "served", self.OUT_A)]})
        self.assertEqual(paired, set())
        self.assertEqual(len(notes), 1)

    def test_two_halves_with_different_outputs_are_not_an_exchange(self):
        paired, notes = step.paired_exchanges({"n1": [("x1", "requested", self.OUT_A)],
                                               "n2": [("x1", "served", self.OUT_B)]})
        self.assertEqual(paired, set())
        self.assertIn("output digests differ or are missing", notes[0])

    def test_two_halves_with_no_output_digest_are_not_an_exchange(self):
        paired, notes = step.paired_exchanges({"n1": [("x1", "requested", None)], "n2": [("x1", "served", None)]})
        self.assertEqual(paired, set())
        self.assertIn("missing", notes[0])

    def test_a_malformed_half_is_named_not_raised_on(self):
        def half(**x):
            return {"model_attestation": {"compute_attestation": {"agent_output_digest": ["d"],
                                                                  "x-mesh-poc-v1": x}}}
        keys = [step.exchange_key(half(client_nonce=["a"], role={"r": 1})), step.exchange_key(half(client_nonce="")),
                step.exchange_key({}), step.exchange_key({"model_attestation": "x"}),
                step.exchange_key({"model_attestation": {"compute_attestation": []}})]
        self.assertEqual(keys, [(None, None, None)] * 5)
        paired, notes = step.paired_exchanges({"n1": keys})
        self.assertEqual((paired, len(notes)), (set(), 5))

    def test_a_half_with_no_nonce_is_named_not_dropped(self):
        paired, notes = step.paired_exchanges({"n1": [(None, "requested", self.OUT_A)]})
        self.assertEqual(paired, set())
        self.assertEqual(notes, ["a half held by n1 carries no client_nonce"])

    def test_the_2026_10_29_live_halves_are_two_exchanges(self):
        root = ROOT / "findings" / "2026-10-29-live" / "05-deal"
        files = sorted(root.glob("stevenmih-*/held-?.json"))
        if not files:
            self.skipTest("the 2026-10-29 live proof files are not in this checkout")
        halves = {}
        for f in files:
            halves.setdefault(f.parent.name, []).append(step.exchange_key(json.loads(f.read_text())))
        paired, notes = step.paired_exchanges(halves)
        self.assertEqual(paired, {"26c09646-1582-4ee4-a0f8-f030163f917f", "de9cf398-1203-47d5-abc5-5ea0d7b5c25a"})
        self.assertEqual(notes, [])


@unittest.skipIf(BIN is None, NO_DRIVER)
class Step05DealRehearsalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.findings = Path(self.tmp.name) / "findings"
        self.out = self.findings / "05-deal"
        self.population, self.keys = step.nodes(POPULATION)

    def tearDown(self):
        self.tmp.cleanup()

    def run_step(self, population=POPULATION):
        r = run_step(population, self.out)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads((self.out / "finding.json").read_text())

    def dealt(self, settings=None, decls=None, plant=None):
        """The proof files of one deal, made the way the step makes them."""
        started = finding.now()
        step.clean(self.out)
        if decls is None:
            decls = step.declarations(BIN, POPULATION, self.keys, started)
        step.deal(BIN, self.population, self.keys, settings or step.settings(POPULATION), self.out, started,
                  decls=decls, plant=plant)
        return started

    def decide(self, started):
        return step.decide(BIN, self.out, population=self.population, keys=self.keys, started=started)

    def failed(self):
        v = json.loads((self.out / "verify.json").read_text())
        return {(c["side"], c["check"]) for c in v["checks"] if not c["ok"]}

    def assert_clean(self):
        self.assertEqual(finding.check(self.findings), [])

    def with_settings(self, **sides):
        s = step.settings(POPULATION)
        for side, values in sides.items():
            s[side].update(values)
        return s

    # (a) the rehearsal population
    def test_rehearsal_passes_with_acceptance_under_a_controller_declaration(self):
        f = self.run_step()
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertEqual(f["not_checked"], [])
        self.assertEqual(self.failed(), set())
        v = json.loads((self.out / "verify.json").read_text())
        self.assertEqual(v["acceptance_not_checked"], [])
        self.assertIn("v0.8.1", v["verifier"])
        for c in ("half_cites_counterparty_checkpoint", "pushed_at_completion", "holds_foreign_half",
                  "cites_foreign_half", "accepted_under_controller_declaration", "counterparty_distinct",
                  "share_record_at_completion", "halves_of_one_exchange"):
            self.assertEqual(sum(1 for x in v["checks"] if x["check"] == c and x["ok"]),
                             1 if c == "halves_of_one_exchange" else 2, c)
            self.assertIn(c, f["detail"])
        self.assertEqual(finding.gaps(f["detail"]), [(
            "the controller declaration's format, which is the rehearsal's stand-in (steps/05-deal.d), not the "
            "controller-binding/0 the pinned capsules plugin reads, so the Go binding verifier cannot check it",
            "evidence-network/loop#134")])
        self.assertIn("would spend 0 USD", f["detail"])
        self.assertEqual(set(f["pinned"]), {"evidencebook", "checkpointed-local-log"})
        paths = {p["path"] for p in f["proof"]}
        for side in ("alice", "bob"):
            for p in ("half.json", "push.json", "held/received-records.jsonl", "held/received-provenance.jsonl",
                      "citing.json", "checkpoint-open.json"):
                self.assertIn(f"{side}/{p}", paths)
            prov = json.loads((self.out / side / "held" / "received-provenance.jsonl").read_text())
            self.assertEqual(prov["accepted_via"], "controller_declaration")
            self.assertFalse((self.out / side / "refusal.json").exists())
        self.assert_clean()  # (c)

    def test_the_repository_rehearsal_population_passes(self):
        f = self.run_step(ROOT / "population" / "rehearsal")
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assert_clean()

    def test_a_second_run_into_the_same_directory_is_clean(self):
        self.run_step()
        self.assertEqual(self.run_step()["result"], "PASS")
        self.assert_clean()

    # (b) planted failures
    def test_a_half_citing_the_wrong_counterparty_checkpoint_fails(self):
        started = self.dealt(plant="wrong-checkpoint")
        f = self.decide(started)
        self.assertEqual(f["result"], "FAIL")
        self.assertEqual(self.failed(), {("alice", "half_cites_counterparty_checkpoint")})
        self.assertIn("rehearsal-bob-other", f["detail"])
        self.assert_clean()

    def test_a_held_half_whose_bytes_do_not_match_its_digest_fails(self):
        started = self.dealt()
        held = self.out / "bob" / "held" / "received-records.jsonl"
        record = json.loads(held.read_text())
        record["header"]["statement"]["role"] = "provider"
        held.write_text(json.dumps(record, separators=(",", ":")) + "\n")
        f = self.decide(started)
        self.assertEqual(f["result"], "FAIL")
        self.assertEqual(self.failed(), {("bob", "holds_foreign_half"), ("bob", "cites_foreign_half")})
        self.assertIn("none of the 1 held records recomputes to it", f["detail"])
        self.assert_clean()

    def test_a_counterparty_bound_to_the_receivers_own_controller_fails(self):
        started = finding.now()
        ctl = POPULATION / "keys" / "controller-alice" / "controller-key.pem"
        both = ",".join(step.raw_public_hex(self.keys[s][1]) for s in ("alice", "bob"))
        r = subprocess.run([str(BIN), "declare", "--controller-key", str(ctl), "--controller-id",
                            "rehearsal-controller-alice", "--keys", both, "--time", started],
                           capture_output=True, text=True, check=True)
        self.dealt(decls=[json.loads(r.stdout)])
        f = self.decide(started)
        self.assertEqual(f["result"], "FAIL")
        self.assertEqual(self.failed(), {("alice", "counterparty_distinct"), ("bob", "counterparty_distinct")})
        self.assertIn("not a different counterparty", f["detail"])
        self.assert_clean()

    def test_a_declaration_whose_signature_does_not_verify_fails(self):
        started = finding.now()
        decls = step.declarations(BIN, POPULATION, self.keys, started)
        decls[0]["controller_id"] = "rehearsal-controller-other"
        self.dealt(decls=decls)
        f = self.decide(started)
        # Bob finds no verifying declaration for Alice's key and falls back to the stopgap.
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertEqual(f["not_checked"][0]["issue"], step.ACCEPTANCE_ISSUE)
        self.assertIn("operator_stopgap (bob)", f["detail"])
        self.assert_clean()

    def test_a_side_that_does_not_push_fails(self):
        started = self.dealt(settings=self.with_settings(bob={"share_record_at_completion": "off"}))
        f = self.decide(started)
        self.assertEqual(f["result"], "FAIL")
        self.assertEqual(self.failed(), {("bob", "share_record_at_completion"), ("bob", "pushed_at_completion"),
                                         ("alice", "holds_foreign_half"), ("alice", "cites_foreign_half"),
                                         ("bob", "holds_foreign_half"), ("bob", "cites_foreign_half")})
        refusal = json.loads((self.out / "bob" / "refusal.json").read_text())
        self.assertEqual(refusal["reason"], "policy_decline")
        self.assertEqual(refusal["key_id"], step.raw_public_hex(self.keys["alice"][1]))
        self.assertNotIn("signature", refusal)
        self.assert_clean()

    def test_a_population_without_settings_is_off_and_fails(self):
        pop = Path(self.tmp.name) / "no-settings"
        shutil.copytree(POPULATION, pop)
        (pop / "step05" / "settings.json").unlink()
        f = self.run_step(pop)
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("share_record_at_completion off", f["detail"])
        self.assert_clean()

    # acceptance other than under a controller declaration is never a pass
    def test_acceptance_via_the_stopgap_or_the_host_is_not_checked(self):
        for via in ("operator_stopgap", "host_announced"):
            with self.subTest(via=via):
                started = self.dealt(settings=self.with_settings(alice={"accepted_via": via}))
                f = self.decide(started)
                self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
                self.assertEqual([n["issue"] for n in f["not_checked"]], [step.ACCEPTANCE_ISSUE])
                self.assertIn(f"{via} (alice)", f["not_checked"][0]["why"])
                self.assertEqual(self.failed(), set())
                self.assert_clean()

    def test_no_declarations_at_all_is_not_checked(self):
        started = self.dealt(decls=[])
        f = self.decide(started)
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertIn("operator_stopgap (alice)", f["detail"])
        self.assertIn("operator_stopgap (bob)", f["detail"])
        self.assert_clean()


class Step05DealNoDriverTest(unittest.TestCase):
    def test_rehearsal_without_the_driver_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, LOOP_TOOLS=tmp)
            env.pop("LOOP_STEP05_DEAL_BIN", None)
            r = run_step(POPULATION, Path(tmp) / "05-deal", env=env)
            self.assertEqual(r.returncode, 2)
            self.assertIn("05-deal.setup.sh", r.stderr)

    def test_live_without_the_tools_is_not_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, LOOP_TOOLS=tmp, LAB_DIR=str(LIVE))
            env.pop("LOOP_STEP05_DEAL_BIN", None)
            r = run_step(LIVE, Path(tmp) / "f" / "05-deal", env=env)
            self.assertEqual(r.returncode, 0, r.stderr)
            f = json.loads((Path(tmp) / "f" / "05-deal" / "finding.json").read_text())
            self.assertEqual(f["result"], "NOT_CHECKED")
            self.assertIn(step.LIVE_SETUP, {n["issue"] for n in f["not_checked"]})
            self.assertEqual(finding.check(Path(tmp) / "f"), [])


BINDINGS = json.loads((FIXTURES / "controller-bindings.json").read_text())["cases"]
REVOCATIONS = json.loads((FIXTURES / "controller-bindings.json").read_text())["revocations"]
DELEGATED_BY = json.loads((ROOT / "tests" / "fixtures" / "step12" / "delegated-by.json").read_text())


def binding_digest(text):
    r = subprocess.run([str(CROSS), "binding"], capture_output=True,
                       input=json.dumps({"binding": text, "at": ["2026-10-30T00:00:00Z"]}).encode())
    return json.loads(r.stdout)["binding_digest"]


@unittest.skipUnless(HAS_CROSS, NO_CROSS)
class Step05BindingCheckTest(unittest.TestCase):
    """The sender's controller-binding.json, checked by `cross_language binding` (aac-go go/v0.17.0)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.data = self.dir / "data"
        self.data.mkdir()
        (self.dir / "nodes").mkdir()
        os.environ["LOOP_NODES_DIR"] = str(self.dir / "nodes")
        self.addCleanup(os.environ.pop, "LOOP_NODES_DIR")
        # Revocation statements are read from main as it stood at the step's start: a scratch repository's main.
        os.environ["LOOP_REVOCATIONS_GIT"] = str(self.dir)
        self.addCleanup(os.environ.pop, "LOOP_REVOCATIONS_GIT")
        self.snap = None  # bv.snapshot of the scratch origin/main at the step's start, unless a test sets one
        self.git("init", "-q", "-b", "main")
        self.commit("2026-10-01T00:00:00Z", "start", "--allow-empty")

    def git(self, *args, date=None):
        env = dict(os.environ, **({"GIT_COMMITTER_DATE": date, "GIT_AUTHOR_DATE": date} if date else {}))
        subprocess.run(["git", "-C", str(self.dir), "-c", "user.name=t", "-c", "user.email=t@t.invalid", "-c",
                        "commit.gpgsign=false", *args], check=True, capture_output=True, env=env)

    def commit(self, date, msg, *extra):
        if (self.dir / "nodes" / "revocations").is_dir():
            self.git("add", "nodes/revocations")
        self.git("commit", "-qm", msg, *extra, date=date)
        branch = subprocess.run(["git", "-C", str(self.dir), "branch", "--show-current"], capture_output=True,
                                text=True).stdout.strip()
        if branch == "main":  # as if pushed: the run reads origin/main, never a local branch
            self.git("update-ref", "refs/remotes/origin/main", "HEAD")

    def check(self, case, file=True, listed=True, digest=None, key=None, times=("2026-10-30T00:00:00Z",),
              started="2026-10-31T00:00:00Z"):
        text, keys = BINDINGS[case]["binding"], BINDINGS[case]["keys"]
        key = key or keys[0]
        node_id = "b1" * 32
        if listed:
            (self.dir / "nodes" / f"{node_id}.json").write_text(json.dumps({"node_id": node_id, "key_id": key}))
        if file:
            (self.data / step.BINDING_FILE).write_text(text)
        half = {"key_id": key, "timestamp": times[0]}
        citing = {"timestamp": times[-1]}
        snap = self.snap if self.snap is not None else step.bv.snapshot(self.dir, started)
        return step.binding_check(digest or binding_digest(text), half, citing, {node_id: ("lab-b", self.data)},
                                  started, snap)

    def test_a_binding_that_names_the_key_and_applies_is_ok(self):
        state, detail = self.check("valid-same-controller")
        self.assertEqual(state, "ok", detail)
        # CB-1 option A: with no file published, the run declares the empty list complete as of the step's start.
        self.assertIn(self.passed(b"[]"), detail)

    def passed(self, octets, started="2026-10-31T00:00:00Z"):
        """The revocation PASS text for the list `octets` that main held at `started`."""
        main = step.bv.main_commit(self.dir, started)
        return (f"revocation PASS: no statement applies in the list evidence-network/loop step 05 (run started "
                f"{started}), nodes/revocations/ at main {main} declared complete as of {started} under profile "
                f"{step.bv.PROFILE} (list sha256 {hashlib.sha256(octets).hexdigest()}); a statement made after "
                f"{started} is not excluded")

    def test_a_revocation_on_a_branch_is_read_only_once_merged_and_one_dropped_on_a_branch_still_counts(self):
        # Sherlock2 (loop#239): the list is main's at the step's start, never the checked-out branch's.
        digest = self.publish_revocations("valid-same-controller")
        self.git("checkout", "-qb", "branch")
        (self.dir / "nodes" / "revocations" / f"{digest}.json").unlink()
        self.commit("2026-10-20T00:00:00Z", "drop it on a branch")
        state, detail = self.check("valid-same-controller")
        self.assertEqual(state, "bad", detail)  # main still holds the statement
        self.assertIn("controller_binding_revocation FAIL", detail)
        self.git("checkout", "-q", "main")
        self.git("checkout", "-qb", "other")
        (self.dir / "nodes" / "revocations").mkdir(exist_ok=True)
        self.git("rm", "-q", f"nodes/revocations/{digest}.json")
        self.commit("2026-10-21T00:00:00Z", "drop it on another branch")
        self.git("checkout", "-q", "main")
        self.git("rm", "-q", f"nodes/revocations/{digest}.json")
        self.commit("2026-11-02T00:00:00Z", "dropped on main after the start")
        self.assertEqual(self.check("valid-same-controller")[0], "bad")  # main at the start held it

    def test_with_no_origin_main_to_read_from_revocation_is_open(self):
        self.git("update-ref", "-d", "refs/remotes/origin/main")  # a local main alone is never read
        state, detail = self.check("valid-same-controller")
        self.assertEqual(state, "open", detail)
        self.assertIn("no origin/main commit at or before 2026-10-31T00:00:00Z", detail)

    def test_a_kept_commit_this_clone_lacks_or_a_failed_fetch_leaves_revocation_open(self):
        self.snap = {"commit": "ab" * 20}
        state, detail = self.check("valid-same-controller")
        self.assertEqual(state, "open", detail)
        self.assertIn("this clone does not hold main commit abababababab", detail)
        self.snap = {"commit": None, "why": "origin/main could not be fetched (offline)"}
        state, detail = self.check("valid-same-controller")
        self.assertEqual(state, "open", detail)
        self.assertIn("origin/main could not be fetched (offline)", detail)

    def test_the_snapshot_fetches_origin_main_first(self):
        # A clone that fetched before a revocation was merged reads it once the run fetches (reviewers, loop#239).
        side = tempfile.TemporaryDirectory()
        self.addCleanup(side.cleanup)
        remote = Path(side.name) / "remote"
        subprocess.run(["git", "clone", "-q", "--bare", str(self.dir), str(remote)], check=True)
        self.git("remote", "add", "origin", str(remote))
        self.git("fetch", "-q", "origin")
        before = step.bv.main_commit(self.dir, "2026-10-31T00:00:00Z")
        other = Path(side.name) / "other"
        subprocess.run(["git", "clone", "-q", str(remote), str(other)], check=True)
        env = dict(os.environ, GIT_COMMITTER_DATE="2026-10-20T00:00:00Z", GIT_AUTHOR_DATE="2026-10-20T00:00:00Z")
        subprocess.run(["git", "-C", str(other), "-c", "user.name=t", "-c", "user.email=t@t.invalid", "-c",
                        "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-m", "merged later"], check=True,
                       env=env)
        subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "HEAD:main"], check=True)
        with mock.patch.dict(os.environ, {"LOOP_REVOCATIONS_FETCH": "1"}):
            snap = step.bv.snapshot(self.dir, "2026-10-31T00:00:00Z")
        self.assertNotEqual(snap["commit"], before)
        self.assertEqual(snap["commit"], subprocess.run(["git", "-C", str(other), "rev-parse", "HEAD"],
                                                        capture_output=True, text=True).stdout.strip())

    def test_a_half_issued_after_the_steps_start_leaves_revocation_open(self):
        state, detail = self.check("valid-same-controller", started="2026-10-29T00:00:00Z")
        self.assertEqual(state, "open", detail)
        self.assertIn("revocation NOT_CHECKED: the verifier rejected the declaration (as_of is earlier than a time asked "
                      "about)", detail)

    # revocation statements published at nodes/revocations/<digest>.json (loop#138)
    def publish_revocations(self, case, statements=None):
        digest = binding_digest(BINDINGS[case]["binding"])
        (self.dir / "nodes" / "revocations").mkdir(exist_ok=True)
        statements = [json.loads(REVOCATIONS[case])] if statements is None else statements
        (self.dir / "nodes" / "revocations" / f"{digest}.json").write_text(json.dumps(statements))
        self.commit("2026-10-15T00:00:00Z", "publish", "--allow-empty")  # merged to main before the step starts
        return digest

    def test_a_published_revocation_before_the_halfs_time_makes_the_binding_not_apply(self):
        self.publish_revocations("valid-same-controller")
        state, detail = self.check("valid-same-controller")
        self.assertEqual(state, "bad", detail)
        self.assertIn("controller_binding_revocation FAIL", detail)

    def test_a_published_revocation_by_the_bip340_key_makes_it_not_apply_too(self):
        self.publish_revocations("valid-nostr-key")
        keys = BINDINGS["valid-nostr-key"]["keys"]
        state, detail = self.check("valid-nostr-key", key=keys[1])
        self.assertEqual(state, "bad", detail)
        self.assertIn("controller_binding_revocation FAIL", detail)

    def test_a_revocation_dated_after_both_times_leaves_the_binding_applying(self):
        # the vector's statement is revoked_at 2026-10-12, after a half issued on 2026-10-11
        self.publish_revocations("valid-same-controller")
        state, detail = self.check("valid-same-controller", times=("2026-10-11T00:00:00Z",))
        self.assertEqual(state, "ok", detail)
        digest = binding_digest(BINDINGS["valid-same-controller"]["binding"])
        listed = (self.dir / "nodes" / "revocations" / f"{digest}.json").read_bytes()
        self.assertIn(self.passed(listed), detail)

    def test_a_statement_whose_signature_does_not_verify_does_not_apply(self):
        statement = json.loads(REVOCATIONS["valid-same-controller"])
        sig = statement["sig"]
        statement["sig"] = ("0" if sig[0] != "0" else "1") + sig[1:]
        self.publish_revocations("valid-same-controller", [statement])
        self.assertEqual(self.check("valid-same-controller")[0], "ok")

    def test_a_revocations_file_out_of_shape_is_refused(self):
        good = json.loads(REVOCATIONS["valid-same-controller"])
        digest = binding_digest(BINDINGS["valid-same-controller"]["binding"])
        folder = self.dir / "nodes" / "revocations"
        folder.mkdir()
        for name, doc, why in ((f"{digest}.json", [], "non-empty array"),
                               (f"{digest}.json", [{**good, "extra": 1}], "members are not exactly"),
                               (f"{digest}.json", [{**good, "binding": "cd" * 32}], "for binding " + digest),
                               (f"{digest}.json", [{**good, "revocation_version": "x"}], "not a controller-binding"),
                               ("not-a-digest.json", [good], "not named")):
            (folder / name).write_text(json.dumps(doc))
            with self.assertRaises(ValueError) as e:
                step.bv.read_revocations(folder / name)
            self.assertIn(why, str(e.exception))
            (folder / name).unlink()
        self.assertEqual(step.bv.checkout_list(self.dir / "nodes", digest), "[]")
        # a repeated member, at the top or nested in a bip340 sig, is refused rather than read as its last value
        text = json.dumps(good)
        for dup in (text.replace('"revoked_at":', '"revoked_at": "2026-01-01T00:00:00Z", "revoked_at":', 1),
                    json.dumps({**good, "sig": {"a": 1}}).replace('"a": 1', '"a": 1, "a": 2')):
            (folder / f"{digest}.json").write_text("[" + dup + "]")
            with self.assertRaises(ValueError) as e:
                step.bv.read_revocations(folder / f"{digest}.json")
            self.assertIn("repeated member", str(e.exception))
        (folder / f"{digest}.json").write_text("not json")
        with self.assertRaises(ValueError):
            step.bv.checkout_list(self.dir / "nodes", digest)
        import check_revocations
        self.assertEqual(len(check_revocations.check(folder)), 1)
        (folder / f"{digest}.json").write_text(json.dumps([good]))
        self.assertEqual(check_revocations.check(folder), [])
        self.assertEqual(check_revocations.check(self.dir / "absent"), [])

    # the half's key's role (AAC#468, loop#196): in delegated_by it is the delegate, never the delegator
    def check_delegated(self, key):
        BINDINGS["delegated-by"] = {"binding": DELEGATED_BY["binding"], "keys": DELEGATED_BY["keys"]}
        self.addCleanup(BINDINGS.pop, "delegated-by", None)
        return self.check("delegated-by", key=key, times=(DELEGATED_BY["evaluation_time"],))

    def test_the_delegate_of_a_delegated_by_binding_is_ok(self):
        delegate = next(k for k in DELEGATED_BY["keys"] if k != DELEGATED_BY["delegator"])
        state, detail = self.check_delegated(delegate)
        self.assertEqual(state, "ok", detail)
        self.assertIn(f"names the half's key {delegate} as the delegate and applies", detail)

    def test_the_delegator_of_a_delegated_by_binding_is_bad(self):
        state, detail = self.check_delegated(DELEGATED_BY["delegator"])
        self.assertEqual(state, "bad", detail)
        self.assertIn("names the half's key as the delegator", detail)

    def test_a_same_controller_binding_gives_the_key_no_role(self):
        state, detail = self.check("valid-same-controller")
        self.assertEqual(state, "ok", detail)
        self.assertIn("(same_controller_asserted: no role)", detail)

    def test_an_unreadable_revocations_file_makes_the_binding_not_apply_with_a_reason(self):
        # loop#194: a malformed nodes/revocations/<digest>.json is a FAIL with a reason, not a stopped step
        digest = self.publish_revocations("valid-same-controller", [])
        (self.dir / "nodes" / "revocations" / f"{digest}.json").write_text("not json")
        self.commit("2026-10-16T00:00:00Z", "break")
        state, detail = self.check("valid-same-controller")
        self.assertEqual(state, "bad", detail)
        self.assertIn("the published revocation statements are unreadable", detail)

    def test_the_second_key_of_a_same_controller_binding_is_ok_too(self):
        keys = BINDINGS["valid-same-controller"]["keys"]
        self.assertEqual(self.check("valid-same-controller", key=keys[1])[0], "ok")

    def test_a_key_the_binding_does_not_name_is_bad(self):
        state, detail = self.check("valid-same-controller", key="ab" * 32)
        self.assertEqual(state, "bad", detail)
        self.assertIn("does not name the half's key", detail)

    def test_a_binding_outside_its_window_at_the_citing_record_is_bad(self):
        state, detail = self.check("valid-same-controller", times=("2026-10-30T00:00:00Z", "2028-01-01T00:00:00Z"))
        self.assertEqual(state, "bad", detail)
        self.assertIn("controller_binding_window FAIL", detail)

    def test_an_expired_binding_and_one_signed_by_one_key_are_bad(self):
        for case, prop in (("expired", "controller_binding_window FAIL"),
                           ("one-key-signed", "controller_binding_signatures FAIL")):
            state, detail = self.check(case)
            self.assertEqual(state, "bad", detail)
            self.assertIn(prop, detail)

    def test_a_method_the_verifier_does_not_implement_is_unsupported_not_ok(self):
        state, detail = self.check("unknown-method")
        self.assertEqual(state, "unsupported", detail)
        self.assertIn("controller_binding_signatures NOT_CHECKED", detail)

    def test_a_binding_of_the_halfs_ed25519_key_to_a_bip340_key_applies(self):
        # aac-go go/v0.17.0 (AAC#458): the controller key is bip340, the half's evidence key Ed25519.
        keys = BINDINGS["valid-nostr-key"]["keys"]
        state, detail = self.check("valid-nostr-key", key=keys[1], times=("2026-10-30T00:00:00Z",))
        self.assertEqual(state, "ok", detail)
        self.assertIn("signatures and window PASS", detail)

    def test_a_bip340_binding_with_one_altered_signature_byte_fails_its_signatures(self):
        case = BINDINGS["valid-nostr-key"]
        doc = json.loads(case["binding"])
        sig = doc["sigs"][0]["sig"]["sig"]
        doc["sigs"][0]["sig"]["sig"] = ("0" if sig[0] != "0" else "1") + sig[1:]
        BINDINGS["bad-nostr-sig"] = {"binding": json.dumps(doc), "keys": case["keys"]}
        self.addCleanup(BINDINGS.pop, "bad-nostr-sig", None)
        state, detail = self.check("bad-nostr-sig", key=case["keys"][1], times=("2026-10-30T00:00:00Z",))
        self.assertEqual(state, "bad", detail)
        self.assertIn("signatures FAIL", detail)

    def test_a_declared_bip340_key_type_is_no_longer_downgraded(self):
        # aac-go go/v0.17.0 verifies bip340 (AAC#458): a binding the verifier rejects is a FAIL whatever it declares.
        keys = BINDINGS["valid-same-controller"]["keys"]
        text = json.dumps({**json.loads(BINDINGS["valid-same-controller"]["binding"]), "key_types": ["ed25519", "bip340"]})
        BINDINGS["with-key-types"] = {"binding": text, "keys": keys}
        self.addCleanup(BINDINGS.pop, "with-key-types", None)
        state, detail = self.check("with-key-types")
        self.assertEqual(state, "bad", detail)

    def test_only_a_check_that_ran_and_passed_is_verified(self):
        for state in ("unread", "unsupported", "unlisted", "unkeyed", "unclaimed", "uncited"):
            c = step.state_check(state, "d")
            self.assertEqual((c["ok"], c["verified"], c["state"]), (True, False, state), state)
        for state in ("never_tied", "ambiguous"):
            c = step.state_check(state, "d", passed="tied")
            self.assertEqual((c["ok"], c["verified"]), (True, False), state)
        self.assertTrue(step.state_check("tied", "d", passed="tied")["verified"])
        self.assertEqual(step.state_check("bad", "d")["ok"], False)
        self.assertEqual(step.state_check("bad", "d")["verified"], False)
        self.assertEqual((step.state_check("ok", "d")["ok"], step.state_check("ok", "d")["verified"]), (True, True))

    def test_no_file_no_listing_or_another_binding_is_unread_never_bad(self):
        state, detail = self.check("valid-same-controller", file=False)
        self.assertEqual(state, "unread", detail)
        self.assertIn("no readable controller-binding.json", detail)
        for f in (self.dir / "nodes").glob("*.json"):
            f.unlink()
        self.assertIn("no nodes/<node_id>.json listing", self.check("valid-same-controller", listed=False)[1])
        state, detail = self.check("valid-same-controller", digest="cd" * 32)
        self.assertEqual(state, "unread", detail)
        self.assertIn("not the " + "cd" * 32 + " the citing record names", detail)

    def test_a_file_that_is_not_a_binding_is_not_the_cited_one_and_not_a_stopped_step(self):
        self.check("valid-same-controller")
        (self.data / step.BINDING_FILE).write_text("not json")
        state, _ = step.binding_check("cd" * 32, {"key_id": "ab" * 32, "timestamp": "2026-10-30T00:00:00Z"},
                                      {"timestamp": "2026-10-30T00:00:00Z"}, {"b1" * 32: ("lab-b", self.data)},
                                      "2026-10-31T00:00:00Z", step.bv.snapshot(self.dir, "2026-10-31T00:00:00Z"))
        self.assertEqual(state, "unread")

    def test_a_half_with_no_evidence_key_is_bad_not_a_crash(self):
        for key in (None, "zz", 5):
            state, detail = step.binding_check("cd" * 32, {"key_id": key, "timestamp": "2026-10-30T00:00:00Z"},
                                               {"timestamp": "2026-10-30T00:00:00Z"}, {}, "2026-10-31T00:00:00Z",
                                               step.bv.snapshot(self.dir, "2026-10-31T00:00:00Z"))
            self.assertEqual(state, "bad", detail)
        self.assertIsNone(step.listed_node_id(None))

    def test_both_issue_times_must_be_readable_to_hold_the_window(self):
        for half, citing in (("2026-10-30T00:00:00Z", "not a time"), (None, "2026-10-30T00:00:00Z"),
                             ("2026-10-30T00:00:00", "2026-10-30T00:00:00Z")):
            state, detail = self.check("valid-same-controller", times=(half, citing))
            self.assertEqual(state, "unread", detail)
            self.assertIn("no readable timestamp", detail)

    def test_the_entries_name_what_is_left_and_are_absent_when_nothing_is(self):
        self.assertEqual(step.live_declarations({}, set()), [])
        es = step.live_declarations({"ok": 2, "open": 1, "unread": 1, "unsupported": 1},
                                    {"lab-b has no readable controller-binding.json in its data_dir"})
        by = {(e["what"], e["issue"]): e["why"] for e in es}
        self.assertIn("1 cited bindings could not be checked", by[(step.LIVE_DECLARATION_WHAT, step.live.ISSUE)])
        self.assertIn("does not implement", by[(step.LIVE_DECLARATION_WHAT, step.bv.UNSUPPORTED_METHOD_ISSUE)])
        self.assertIn("1 cited bindings apply, with revocation NOT_CHECKED", by[(
            "revocation of the controller bindings that apply", step.BINDING_REVOCATION_ISSUE)])
        self.assertEqual(len(es), 3)
        # the key's role is checked by binding_check (AAC#468, loop#196), and revocation PASSes on the run's declared
        # snapshot (CB-1 option A): a binding that applies leaves nothing; one whose declaration the verifier rejected, CB-1
        self.assertEqual(step.live_declarations({"ok": 1}, set()), [])
        self.assertEqual([e["issue"] for e in step.live_declarations({"open": 1}, set())],
                         [step.BINDING_REVOCATION_ISSUE])


LAB3 = "2b800a2158c380666493fe83faddccfd882fae61af37639a168c6361704f03ac"  # stevenmih-3, published with no binding
LAB1 = "16a607c33006dd4243cb2347c4bdbaa5129a590b5257eea62ac5047abcf87864"  # stevenmih-1, published bound


class Step05PublishedCommitmentTest(unittest.TestCase):
    """A sender's "no binding" is read only from its commitment verified under its listed key (loop#187)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.nodes = Path(self.tmp.name) / "nodes"
        (self.nodes / "commitments" / "policies").mkdir(parents=True)
        for node in (LAB3, LAB1):
            shutil.copy(ROOT / "nodes" / f"{node}.json", self.nodes / f"{node}.json")
            shutil.copy(ROOT / "nodes" / "commitments" / f"{node}.json", self.nodes / "commitments" / f"{node}.json")
            shutil.copy(ROOT / "nodes" / "commitments" / "policies" / f"{node}.json",
                        self.nodes / "commitments" / "policies" / f"{node}.json")
        patch = mock.patch.dict(os.environ, {"LOOP_NODES_DIR": str(self.nodes)})
        patch.start()
        self.addCleanup(patch.stop)

    def key(self, node):
        return json.loads((self.nodes / f"{node}.json").read_text())["key_id"]

    def accept(self, node):
        key = self.key(node)
        citing = {"model_attestation": {"compute_attestation": {"received_half": {
            "accepted_via": "host_announced", "sender_key_id": key}}}}
        return step.accepted(citing, {"key_id": key})

    def test_lab3s_commitment_verifies_and_carries_no_binding(self):
        digest, commitment = step.published_binding(self.key(LAB3))
        self.assertIsNone(digest)
        self.assertIs(commitment["verified"], True)
        a = self.accept(LAB3)
        self.assertIs(a["optional"], True)
        self.assertIn("verified under that key, carries no controller binding (optional)", a["detail"])

    def test_a_bound_sender_is_not_optional(self):
        digest, commitment = step.published_binding(self.key(LAB1))
        self.assertTrue(step.is_key(digest))
        self.assertIs(commitment["verified"], True)
        self.assertNotIn("optional", self.accept(LAB1))

    def test_a_commitment_edited_after_signing_is_not_verified_so_not_optional(self):
        path = self.nodes / "commitments" / f"{LAB3}.json"
        doc = json.loads(path.read_text())
        doc["issued_at"] = "2026-01-01T00:00:00Z"
        path.write_text(json.dumps(doc))
        self.assertIs(step.published_binding(self.key(LAB3))[1]["verified"], False)
        self.assertNotIn("optional", self.accept(LAB3))

    def test_a_commitment_under_another_key_than_listed_is_not_verified(self):
        listing = self.nodes / f"{LAB3}.json"
        listing.write_text(json.dumps(dict(json.loads(listing.read_text()), key_id="ab" * 32)))
        self.assertIs(step.published_binding("ab" * 32)[1]["verified"], False)


@unittest.skipIf(BIN is None or not HAS_CROSS, NO_DRIVER if BIN is None else NO_CROSS)
class Step05DealLiveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lab = Path(self.tmp.name) / "lab"
        shutil.copytree(LIVE, self.lab)
        self.findings = Path(self.tmp.name) / "findings"
        self.out = self.findings / "05-deal"
        self.nodes = Path(self.tmp.name) / "nodes"
        self.nodes.mkdir()
        self.make_current()

    def tearDown(self):
        self.tmp.cleanup()

    def publish_binding(self, digest):
        """lab-b, the sender of the half lab-a holds, lists its key and publishes a commitment (loop#88)
        carrying this controller_binding.digest (None: a commitment with no binding)."""
        half = json.loads((self.lab / "nodes" / "lab-a" / "data" / "ledger" / "received-capsules.jsonl").read_text())
        node_id = "b1" * 32
        (self.nodes / f"{node_id}.json").write_text(json.dumps({"node_id": node_id, "key_id": half["key_id"]}))
        (self.nodes / "commitments").mkdir(exist_ok=True)
        doc = {} if digest is None else {"controller_binding": {"digest": digest}}
        (self.nodes / "commitments" / f"{node_id}.json").write_text(json.dumps(doc))

    def run_live(self, **env):
        r = run_step(self.lab, self.out, env=dict(os.environ, LAB_DIR=str(self.lab),
                                                  LOOP_NODES_DIR=str(self.nodes), **env))
        self.assertEqual(r.returncode, 0, r.stderr)
        record = json.loads((self.out / "finding.json").read_text())
        self.assert_rederives(record)
        return record

    def assert_rederives(self, record):
        """judge_live on a copy of only the cited files, with no LAB_DIR and no LOOP_SEALED_SINCE, gives the same
        finding: what step 08 checks (loop#188)."""
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "05-deal"
            for p in record["proof"]:
                (work / p["path"]).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self.out / p["path"], work / p["path"])
            saved = dict(os.environ)
            os.environ.pop("LOOP_SEALED_SINCE", None)
            os.environ.update(LAB_DIR="", LOOP_NODES_DIR=str(self.nodes))
            try:
                loader = importlib.machinery.SourceFileLoader("step05_again", str(STEP))
                mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("step05_again", loader))
                loader.exec_module(mod)
                again = mod.judge_live(json.loads((self.lab / "population.json").read_text()), work, record["started"])
            finally:
                os.environ.clear()
                os.environ.update(saved)
            self.assertEqual((again["result"], again["not_checked"], again["detail"]),
                             (record["result"], record["not_checked"], record["detail"]))

    def verify08(self):
        """Step 08 on the run self.findings holds (05 only), with the lab's population: (finding, manifest entries)."""
        (self.findings / "run.json").write_text(json.dumps({"population": "live", "stage": "1",
                                                            "started": "2026-10-31T00:00:00Z", "steps": ["05", "08"]}))
        saved = dict(os.environ)
        os.environ.pop("LOOP_SEALED_SINCE", None)
        os.environ.update(LAB_DIR="", LOOP_NODES_DIR=str(self.nodes))
        try:
            loader = importlib.machinery.SourceFileLoader("step08_for_05", str(ROOT / "steps" / "08-verify"))
            v = importlib.util.module_from_spec(importlib.util.spec_from_loader("step08_for_05", loader))
            loader.exec_module(v)
            shutil.rmtree(self.findings / "08-verify", ignore_errors=True)
            v.main(["--population", str(self.lab), "--out", str(self.findings / "08-verify")])
        finally:
            os.environ.clear()
            os.environ.update(saved)
        out = self.findings / "08-verify"
        return (json.loads((out / "finding.json").read_text()), v,
                {e["dir"]: e for e in json.loads((out / "verified.json").read_text())["steps"]})

    def rehash(self):
        rec = json.loads((self.out / "finding.json").read_text())
        for p in rec["proof"]:
            p["sha256"] = finding.sha256(self.out / p["path"])
        (self.out / "finding.json").write_text(json.dumps(rec))

    def test_live_a_preimage_in_a_node_chain_never_reaches_the_proof(self):
        # A settlement record keeps the payment's preimage; the copied chain keeps the record at its line without it.
        led = self.lab / "nodes" / "lab-a" / "data" / "ledger" / "capsules.jsonl"
        obs = {"capsule_id": "obs-1", "model_attestation": {"compute_attestation": {"x-mesh-settlement-v1": {
            "preimage": "ab" * 32, "payment_hash": "cd" * 32, "observed_by": "payer"}}}}
        led.write_text(led.read_text() + json.dumps(obs) + "\n")
        self.run_live()
        copied = self.out / "lab-a" / "ledger" / "capsules.jsonl"
        self.assertEqual(len(copied.read_text().splitlines()), len(led.read_text().splitlines()))
        self.assertNotIn("ab" * 32, copied.read_text())
        kept = json.loads(copied.read_text().splitlines()[-1])
        settlement = kept["model_attestation"]["compute_attestation"]["x-mesh-settlement-v1"]
        self.assertEqual((kept["capsule_id"], settlement["preimage"]), ("obs-1", live.REDACTED))
        self.assertEqual(settlement["payment_hash"], "cd" * 32)
        self.assertEqual(kept[live.REDACTION]["line_sha256"],
                         hashlib.sha256(led.read_bytes().splitlines()[-1]).hexdigest())
        self.assertEqual(check_no_preimages.scan([self.findings]), [])
        f, v, entries = self.verify08()
        self.assertEqual(entries["05-deal"]["status"], "reproduced", entries["05-deal"]["problems"])

    def test_08_reproduces_live_05_from_its_proof(self):
        self.run_live()
        f, v, entries = self.verify08()
        self.assertEqual(entries["05-deal"]["status"], "reproduced", entries["05-deal"]["problems"])
        self.assertIn(v.LIVE_RELIES_ON["05"][0], f["not_checked"])

    def test_08_fails_a_held_half_altered_in_the_copy(self):
        self.run_live()
        p = self.out / "lab-a" / "ledger" / "received-capsules.jsonl"
        half = json.loads(p.read_text().splitlines()[0])
        half["key_id"] = "00" * 32
        p.write_text(json.dumps(half) + "\n")
        self.rehash()
        f, v, entries = self.verify08()
        self.assertEqual(entries["05-deal"]["status"], "failed", f["detail"])

    def test_08_fails_a_node_dropped_from_nodes_json(self):
        self.run_live()
        n = json.loads((self.out / "nodes.json").read_text())
        n["read"].pop(sorted(n["read"])[0])
        (self.out / "nodes.json").write_text(json.dumps(n))
        self.rehash()
        f, v, entries = self.verify08()
        self.assertEqual(entries["05-deal"]["status"], "failed", f["detail"])

    def test_08_holds_the_kept_revocations_commit_to_main_at_the_steps_start(self):
        # nodes.json keeps the origin/main commit 05 read nodes/revocations/ from; 08 recomputes it from its own
        # history. An older commit (which could hide a revocation merged since) FAILs.
        self.run_live()
        n = json.loads((self.out / "nodes.json").read_text())
        kept = n["revocations_main"]["commit"]
        self.assertIsNotNone(kept, n["revocations_main"])
        older = subprocess.run(["git", "-C", str(ROOT), "rev-parse", f"{kept}^"], capture_output=True,
                               text=True).stdout.strip()
        n["revocations_main"] = {"commit": older}
        (self.out / "nodes.json").write_text(json.dumps(n))
        self.rehash()
        f, v, entries = self.verify08()
        self.assertEqual(entries["05-deal"]["status"], "failed", f["detail"])
        self.assertIn("but origin/main held", f["detail"])

    def test_08_does_not_reproduce_a_kept_commit_its_own_main_does_not_hold(self):
        # 08's clone may be behind the run's fetch: not reproduced (fetch main's history), never a FAIL.
        self.run_live()
        n = json.loads((self.out / "nodes.json").read_text())
        n["revocations_main"] = {"commit": "ab" * 20}
        (self.out / "nodes.json").write_text(json.dumps(n))
        self.rehash()
        f, v, entries = self.verify08()
        self.assertEqual(entries["05-deal"]["status"], "not_reproduced", f["detail"])
        self.assertIn("fetch main's history", entries["05-deal"]["not_checked"]["why"])

    def test_08_does_not_reproduce_an_old_shape_live_05(self):
        shutil.copytree(ROOT / "findings" / "2026-10-31-live-8" / "05-deal", self.out)
        # It ran under an earlier versions.lock; this test is about its proof's shape, so its pins
        # are set to today's lock (pinned is not proof).
        lock = finding.pins()
        rec = json.loads((self.out / "finding.json").read_text())
        rec["pinned"] = {n: {"repository": lock[n][0], "ref": lock[n][1]} for n in rec["pinned"]}
        (self.out / "finding.json").write_text(json.dumps(rec, indent=2) + "\n")
        f, v, entries = self.verify08()
        self.assertEqual(entries["05-deal"]["status"], "not_reproduced")
        self.assertIn("its proof holds no nodes.json", entries["05-deal"]["not_checked"]["why"])

    def ledger(self):
        return self.lab / "nodes" / "lab-a" / "data" / "ledger"

    def seal(self, body, key_id):
        r = subprocess.run([str(CROSS), "seal"], input=json.dumps(body).encode(), capture_output=True, check=True)
        return dict(json.loads(r.stdout), key_id=key_id)

    def lines(self):
        return [json.loads(ln) for ln in (self.ledger() / "capsules.jsonl").read_text().splitlines() if ln.strip()]

    def write_lines(self, records):
        (self.ledger() / "capsules.jsonl").write_text(
            "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records))

    def body(self, record):
        return copy.deepcopy({k: v for k, v in record.items() if k not in ("capsule_id", "signature", "key_id", "chain")})

    def citing(self):
        return next(r for r in self.lines()
                    if any(x.get("citation_purpose") == "counterparty_half" for x in r.get("references") or []))

    def make_current(self):
        """Lab-a as the current build seals it: its own half of the exchange (nonce n, requested), a citing
        record naming it, and a counterparty_inclusion record citing the checkpoint the half was pushed under."""
        inc_path = self.ledger() / "received-inclusion.jsonl"
        inc = json.loads(inc_path.read_text())
        cp_file, proof_file = Path(self.tmp.name) / "cp.json", Path(self.tmp.name) / "proof.json"
        cp_file.write_text(json.dumps(inc["checkpoint"]))
        proof_file.write_text(json.dumps(inc["inclusion_proof"]))
        held = json.loads((self.ledger() / "received-capsules.jsonl").read_text())
        r = subprocess.run([str(BIN), "held", "--record-id", held["capsule_id"], "--key-id", held["key_id"],
                            "--checkpoint", str(cp_file), "--inclusion", str(proof_file)], capture_output=True, text=True)
        self.digest = json.loads(r.stdout)["checkpoint_digest"]
        inc["checkpoint_digest"] = self.digest
        inc_path.write_text(json.dumps(inc) + "\n")
        citing = self.citing()
        key_id = citing["key_id"]
        ca = citing["model_attestation"]["compute_attestation"]
        # the fixture's citing record carries a copy of the half's attestation; the plugin's has only these
        citing = self.seal({**self.body(citing), "model_attestation": {**citing["model_attestation"], "compute_attestation": {
            "received_half": ca["received_half"], "store_nonce": ca.get("store_nonce", "00" * 32)}}}, key_id)
        half = json.loads((self.ledger() / "received-capsules.jsonl").read_text())
        own_body = self.body(citing)
        own_body["action_id"] = "mesh-poc/own-half"
        own_body["references"] = []
        own_body["model_attestation"] = {"model_id": "m", "provider": "mesh-llm", "compute_attestation": {
            "agent_output_digest": half["model_attestation"]["compute_attestation"]["agent_output_digest"],
            "x-mesh-poc-v1": {"client_nonce": "n", "role": "requested"}}}
        self.own = self.seal(own_body, key_id)
        incl_body = self.body(citing)
        incl_body["action_id"] = "mesh-poc/counterparty-inclusion-citation/" + half["capsule_id"]
        incl_body["model_attestation"] = {"model_id": "n/a", "provider": "mesh-llm", "compute_attestation": {
            "counterparty_inclusion": {"half_capsule_id": half["capsule_id"]}}}
        incl_body["references"] = [{"type": "cll-checkpoint", "digest_alg": "SHA-256", "digest": self.digest,
                                    "citation_purpose": "counterparty_inclusion"}]
        self.write_lines([citing, self.own, self.seal(incl_body, key_id)])
        self.reseal_citing(own_half_capsule_id=self.own["capsule_id"])

    def reseal_citing(self, **received_half):
        """Seal lab-a's citing record again with these received_half members changed (None drops one)."""
        records = self.lines()
        old = self.citing()
        body = self.body(old)
        rh = body["model_attestation"]["compute_attestation"]["received_half"]
        for k, v in received_half.items():
            rh.pop(k, None) if v is None else rh.__setitem__(k, v)
        self.write_lines([self.seal(body, old["key_id"]) if r == old else r for r in records])
        return old

    def set_own(self, mutate):
        """Re-seal the own half after `mutate(body)` and point the citing record at it."""
        body = self.body(self.own)
        mutate(body)
        new = self.seal(body, self.own["key_id"])
        self.write_lines([new if r == self.own else r for r in self.lines()])
        self.own = new
        self.reseal_citing(own_half_capsule_id=new["capsule_id"])

    def acceptance_entries(self, f):
        return [n for n in f["not_checked"] if n["what"] in (step.ACCEPTANCE_WHAT, step.LIVE_UNBOUND_WHAT)]

    def test_live_held_halves_check_and_the_rest_is_not_checked_under_its_issues(self):
        f = self.run_live()
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        issues = {n["issue"] for n in f["not_checked"]}
        self.assertIn(step.LIVE_UNBOUND_ISSUE, issues)
        # authored and accepted, but lab-b has no nodes/ listing: the key is only self-attested, so it is listed
        [c] = json.loads((self.out / "lab-a" / "checks.json").read_text())["held"]
        self.assertEqual(c["signature"]["state"], "unlisted")
        self.assertIn("no nodes/<node_id>.json listing", self.names(f)[
            "the held half's producer signature, in a second language"]["why"])
        self.assertIn("producer signature that verifies in a second language", f["asserts"])
        # the closed refs are gone, and a tied, bound-or-unbound lab is not a plugin gap
        closed = json.loads((ROOT / "tests" / "fixtures" / "closed_issues.json").read_text())["closed"]
        self.assertEqual(issues & set(closed), set())
        self.assertIn("each node seals records naming both halves and citing the other side's checkpoint", f["asserts"])
        self.assertNotIn(step.LIVE_NEVER_TIED, {n["what"] for n in f["not_checked"]})
        self.assertIn("1 tied to the node's own half", f["detail"])
        checks = json.loads((self.out / "lab-a" / "checks.json").read_text())["held"]
        self.assertEqual(len(checks), 1)
        self.assertTrue(all(v["ok"] for v in checks[0].values() if isinstance(v, dict)), checks)
        self.assertEqual(checks[0]["accepted"]["via"], "host_announced")
        self.assertIn("node lab-b: holds no pushed half", f["detail"])
        self.assertIn("accepted the half's key via host_announced", f["detail"])
        self.assertIn({"what": "lab-b: a pushed half held", "why": "the node holds no pushed half from the lab "
                       "run's exchanges", "issue": step.live.ISSUE}, f["not_checked"])
        default = next(n for n in f["not_checked"] if n["what"] == step.LIVE_DEFAULT_ABSENT["what"])
        self.assertIn("lab-a", default["why"])
        [acc] = self.acceptance_entries(f)
        self.assertEqual(acc["what"], step.LIVE_UNBOUND_WHAT)
        self.assertEqual(acc["issue"], step.LIVE_UNBOUND_ISSUE)
        self.assertIn("1 current-build halves accepted via host_announced", acc["why"])
        self.assertIn("the sender's missing or stale published binding, not a plugin gap", acc["why"])
        self.assertIn("node lab-c: not checked", f["detail"])
        self.assertIn("would spend 0 USD", f["detail"])
        self.assertEqual(finding.check(self.findings), [])

    def test_live_a_half_from_a_sender_outside_the_population_is_counted_not_judged(self):
        # loop#174: lab-4, the twins-only node, is in no population; its halves are out of the step's scope.
        prov = self.ledger() / "received-provenance.jsonl"
        prov.write_text(prov.read_text().replace("b1" * 32, "4d" * 32))
        f = self.run_live()
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        doc = json.loads((self.out / "lab-a" / "checks.json").read_text())
        self.assertEqual(doc["held"], [])
        self.assertEqual([o["received_from"] for o in doc["outside_population"]], ["4d" * 32])
        self.assertIn("lab-a/checks.json", [p["path"] for p in f["proof"]])
        self.assertIn("node lab-a: holds no pushed half from a node of the population; 1 held halves from senders "
                      "outside the population, not judged (evidence keys 9e4480104975505a (1))", f["detail"])
        whats = {n["what"] for n in f["not_checked"]}
        self.assertNotIn("the held half's producer signature, in a second language", whats)
        self.assertNotIn(step.LIVE_UNBOUND_WHAT, whats)
        self.assertIn("only halves from senders outside it", self.names(f)["lab-a: a pushed half held"]["why"])
        self.assertNotIn("nonce n", f["detail"])
        self.assertFalse((self.out / "lab-a" / "held-1.json").exists())
        self.assertEqual(finding.check(self.findings), [])

    def test_live_a_half_whose_provenance_names_no_well_formed_sender_is_still_judged(self):
        prov = self.ledger() / "received-provenance.jsonl"
        good = prov.read_text()
        for sender in (None, "", ["x"], "lab-b"):
            doc = json.loads(good)
            doc["received_from"] = sender
            prov.write_text(json.dumps(doc) + "\n")
            f = self.run_live()
            doc = json.loads((self.out / "lab-a" / "checks.json").read_text())
            self.assertEqual([c["held"] for c in doc["held"]], ["held-1"], sender)
            self.assertEqual(doc["outside_population"], [], sender)
            self.assertNotIn("outside the population", f["detail"])

    def test_live_an_outside_half_is_listed_in_checks_beside_the_judged_ones(self):
        # a second held half, from outside the population, next to lab-b's
        held = self.ledger() / "received-capsules.jsonl"
        half = json.loads(held.read_text())
        other = dict(half, capsule_id="ee" * 32)
        held.write_text(held.read_text() + json.dumps(other, separators=(",", ":")) + "\n")
        prov = self.ledger() / "received-provenance.jsonl"
        prov.write_text(prov.read_text() + json.dumps({"capsule_id": "ee" * 32, "received_from": "4d" * 32,
                                                       "via": "push"}) + "\n")
        f = self.run_live()
        doc = json.loads((self.out / "lab-a" / "checks.json").read_text())
        self.assertEqual([c["held"] for c in doc["held"]], ["held-1"])
        self.assertEqual(doc["outside_population"], [{"held": "held-2", "capsule_id": "ee" * 32,
                                                      "received_from": "4d" * 32, "key_id": half["key_id"]}])
        self.assertIn("1 held halves from senders outside the population, not judged", f["detail"])
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        self.assertEqual(finding.check(self.findings), [])

    def test_live_an_unverified_commitment_with_no_binding_stays_an_unbound_sender(self):
        # loop#187, Sherlock2's condition: "no binding" counts only from a commitment verified under the listed key
        self.publish_binding(None)  # an unsigned {} that carries no binding
        f = self.run_live()
        [acc] = self.acceptance_entries(f)
        self.assertEqual(acc["what"], step.LIVE_UNBOUND_WHAT)
        [c] = json.loads((self.out / "lab-a" / "checks.json").read_text())["held"]
        self.assertNotIn("optional", c["accepted"])
        self.assertIs(c["accepted"]["commitment"]["verified"], False)
        self.assertNotIn("loop#187", f["detail"])

    def test_live_a_verified_commitment_with_no_binding_is_accepted_on_its_listed_key(self):
        # the half's key has no private key in the fixtures, so the verified commitment is stood in for here;
        # Step05PublishedCommitmentTest verifies a real one
        self.publish_binding(None)
        self.list_sender()
        verified = (None, {"path": "nodes/commitments/x.json", "sha256": "00" * 32, "verified": True})
        env = {"LAB_DIR": str(self.lab), "LOOP_NODES_DIR": str(self.nodes)}
        with mock.patch.dict(os.environ, env), mock.patch.object(step, "published_binding", return_value=verified):
            self.assertEqual(step.main(["--population", str(self.lab), "--out", str(self.out)]), 0)
        f = json.loads((self.out / "finding.json").read_text())
        self.assertEqual(self.acceptance_entries(f), [])
        [c] = json.loads((self.out / "lab-a" / "checks.json").read_text())["held"]
        self.assertIs(c["accepted"]["optional"], True)
        self.assertEqual(c["signature"]["state"], "ok")
        self.assertIn("1 current-build halves accepted on the evidence key their sender lists in nodes/, from "
                      "senders whose published commitment carries no controller binding (the binding is optional, "
                      "loop#187)", f["detail"])
        self.assertEqual(finding.check(self.findings), [])

    def test_live_a_sender_that_publishes_a_binding_the_record_does_not_name_stays_unbound(self):
        self.publish_binding("cd" * 32)
        f = self.run_live()
        [acc] = self.acceptance_entries(f)
        self.assertEqual(acc["what"], step.LIVE_UNBOUND_WHAT)
        [c] = json.loads((self.out / "lab-a" / "checks.json").read_text())["held"]
        self.assertNotIn("optional", c["accepted"])
        self.assertNotIn("loop#187", f["detail"])

    def test_live_a_held_half_whose_bytes_do_not_match_its_digest_fails(self):
        held = self.lab / "nodes" / "lab-a" / "data" / "ledger" / "received-capsules.jsonl"
        capsule = json.loads(held.read_text())
        capsule["model_attestation"]["model_id"] = "another-model"
        held.write_text(json.dumps(capsule, separators=(",", ":")) + "\n")
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("held-1 held_by_digest", f["detail"])
        self.assertEqual(finding.check(self.findings), [])

    def test_live_a_held_half_no_record_cites_fails(self):
        (self.lab / "nodes" / "lab-a" / "data" / "ledger" / "capsules.jsonl").write_text("")
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("held-1 cited", f["detail"])
        self.assertEqual(finding.check(self.findings), [])

    # acceptance, read from the citing record the pinned plugin seals (capsules-plugin PR #77)
    def test_live_a_citing_record_accepting_another_key_than_the_halfs_fails(self):
        self.reseal_citing(sender_key_id="ab" * 32)
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("held-1 accepted (the citing record accepted key " + "ab" * 32, f["detail"])
        self.assertEqual(finding.check(self.findings), [])

    def test_live_an_unknown_accepted_via_fails(self):
        self.reseal_citing(accepted_via="transport_id")
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("unknown accepted_via 'transport_id'", f["detail"])

    def test_live_acceptance_under_a_declaration_leaves_only_the_declarations_signatures(self):
        self.reseal_citing(accepted_via="controller_declaration", controller_binding_digest="cd" * 32,
                           controller_anchor_key_id="ef" * 32)
        f = self.run_live()
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        self.assertEqual(self.acceptance_entries(f), [])
        self.assertIn(step.LIVE_DECLARATION_WHAT, self.names(f))
        self.assertNotIn(step.LIVE_UNBOUND_ISSUE, {n["issue"] for n in f["not_checked"]})
        self.assertEqual(finding.check(self.findings), [])

    def test_live_a_host_announced_key_with_the_published_binding_digest_counts_as_bound(self):
        self.publish_binding("cd" * 32)
        self.reseal_citing(controller_binding_digest="cd" * 32, same_controller_as_receiver=False)
        f = self.run_live()
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        self.assertEqual(self.acceptance_entries(f), [])
        self.assertIn(step.LIVE_DECLARATION_WHAT, self.names(f))
        self.assertIn("accepted the half's key via host_announced", f["detail"])
        checks = json.loads((self.out / "lab-a" / "checks.json").read_text())["held"]
        self.assertTrue(checks[0]["accepted"]["bound"] and checks[0]["accepted"]["ok"])
        [node_file] = (self.nodes / "commitments").glob("*.json")
        self.assertEqual(checks[0]["accepted"]["commitment"],
                         {"path": f"nodes/commitments/{node_file.name}",
                          "sha256": hashlib.sha256(node_file.read_bytes()).hexdigest(), "verified": False})
        self.assertEqual(finding.check(self.findings), [])

    def bind_sender(self, text=None, file=True):
        """lab-b, the sender (a node of the population, node_id b1..), holds this binding as its
        controller-binding.json (None: no file); the citing record names the binding's digest."""
        self.publish_binding("cd" * 32)
        if text is not None:
            (self.lab / "nodes" / "lab-b" / "data" / step.BINDING_FILE).write_text(text)
            self.reseal_citing(accepted_via="controller_declaration", controller_binding_digest=binding_digest(text))
        else:
            self.reseal_citing(accepted_via="controller_declaration", controller_binding_digest="cd" * 32)

    def test_live_a_cited_binding_the_senders_data_dir_does_not_hold_stays_not_checked(self):
        self.bind_sender()
        f = self.run_live()
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        e = self.names(f)[step.LIVE_DECLARATION_WHAT]
        self.assertIn("lab-b has no readable controller-binding.json", e["why"])
        self.assertEqual(e["issue"], step.live.ISSUE)
        [c] = json.loads((self.out / "lab-a" / "checks.json").read_text())["held"]
        self.assertEqual(c["binding"]["state"], "unread")
        self.assertEqual(finding.check(self.findings), [])

    def test_live_an_unread_senders_binding_is_copied_cited_and_re_derived(self):
        self.bind_sender(BINDINGS["valid-same-controller"]["binding"])
        shutil.rmtree(self.lab / "nodes" / "lab-b" / "data" / "ledger")  # lab-b's ledger is not readable
        f = self.run_live()  # run_live re-derives from the proof copy
        self.assertIn(f"lab-b/{step.BINDING_FILE}", [p["path"] for p in f["proof"]])
        self.assertFalse(json.loads((self.out / "nodes.json").read_text())["read"]["lab-b"])

    def test_live_a_malformed_nodes_json_fails_the_judge(self):
        self.run_live()
        loader = importlib.machinery.SourceFileLoader("step05_bad", str(STEP))
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader("step05_bad", loader))
        loader.exec_module(mod)
        pop = json.loads((self.lab / "population.json").read_text())
        for doc in ({"sealed_since": "yesterday", "missing": [], "read": {}}, {"sealed_since": None, "missing": {}},
                    []):
            (self.out / "nodes.json").write_text(json.dumps(doc))
            r = mod.judge_live(pop, self.out, "2026-10-31T00:00:00Z")
            self.assertEqual(r["result"], "FAIL")
            self.assertIn("nodes.json is not readable", r["detail"])

    def test_live_a_node_name_that_is_not_one_path_component_stops_the_step(self):
        pop = json.loads((self.lab / "population.json").read_text())
        pop["nodes"][0]["name"] = "../x"
        (self.lab / "population.json").write_text(json.dumps(pop))
        r = run_step(self.lab, self.out, env=dict(os.environ, LAB_DIR=str(self.lab), LOOP_NODES_DIR=str(self.nodes)))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertFalse((self.out / "finding.json").exists())

    def test_live_an_unreadable_ledger_file_stops_the_step_as_before(self):
        p = self.ledger() / "capsules.jsonl"
        p.chmod(0)
        try:
            if os.access(p, os.R_OK):
                self.skipTest("running as a user who can read a mode-0 file")
            r = run_step(self.lab, self.out, env=dict(os.environ, LAB_DIR=str(self.lab),
                                                      LOOP_NODES_DIR=str(self.nodes)))
        finally:
            p.chmod(0o644)  # before tearDown removes the lab
        self.assertNotEqual(r.returncode, 0)

    def test_live_a_cited_binding_that_does_not_name_the_halfs_key_fails(self):
        self.bind_sender(BINDINGS["valid-same-controller"]["binding"])
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("binding", f["detail"])
        self.assertIn("does not name the half's key", f["detail"])

    def test_live_another_binding_than_the_cited_one_is_not_a_fail(self):
        self.bind_sender(BINDINGS["valid-same-controller"]["binding"])
        self.reseal_citing(controller_binding_digest="cd" * 32)
        f = self.run_live()
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        self.assertIn("not the " + "cd" * 32, self.names(f)[step.LIVE_DECLARATION_WHAT]["why"])

    def test_live_a_bound_key_with_no_same_controller_member_still_counts(self):
        self.publish_binding("cd" * 32)
        self.reseal_citing(controller_binding_digest="cd" * 32)
        self.assertEqual(self.acceptance_entries(self.run_live()), [])

    def test_live_a_host_announced_key_with_no_digest_stays_not_checked(self):
        self.publish_binding("cd" * 32)
        f = self.run_live()
        self.assertEqual(len(self.acceptance_entries(f)), 1)

    def test_live_a_digest_the_commitment_does_not_carry_stays_not_checked(self):
        self.publish_binding("ab" * 32)
        self.reseal_citing(controller_binding_digest="cd" * 32)
        f = self.run_live()
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        self.assertEqual(len(self.acceptance_entries(f)), 1)
        [c] = json.loads((self.out / "lab-a" / "checks.json").read_text())["held"]
        self.assertFalse(c["accepted"]["bound"])
        self.assertIn("which the sender's published commitment does not carry", c["accepted"]["detail"])

    def test_live_a_digest_with_no_published_binding_or_commitment_stays_not_checked(self):
        self.reseal_citing(controller_binding_digest="cd" * 32)
        self.assertEqual(len(self.acceptance_entries(self.run_live())), 1)
        self.publish_binding(None)
        self.assertEqual(len(self.acceptance_entries(self.run_live())), 1)

    def test_live_a_counterparty_bound_to_the_receivers_own_controller_fails(self):
        self.publish_binding("cd" * 32)
        self.reseal_citing(controller_binding_digest="cd" * 32, same_controller_as_receiver=True)
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("a counterparty whose key is bound to the receiver's own controller", f["detail"])

    def test_live_a_same_controller_counterparty_fails_even_with_no_accepted_via(self):
        self.reseal_citing(accepted_via=None, same_controller_as_receiver=True)
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("a counterparty whose key is bound to the receiver's own controller", f["detail"])

    def test_live_a_node_entry_with_no_commitment_file_stays_not_checked(self):
        self.publish_binding("cd" * 32)
        for f in (self.nodes / "commitments").glob("*.json"):
            f.unlink()
        self.reseal_citing(controller_binding_digest="cd" * 32)
        self.assertEqual(len(self.acceptance_entries(self.run_live())), 1)

    def test_live_a_declaration_with_a_digest_and_no_anchor_key_is_accepted(self):
        self.reseal_citing(accepted_via="controller_declaration", controller_binding_digest="cd" * 32)
        f = self.run_live()
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        self.assertEqual(self.acceptance_entries(f), [])
        self.assertIn(step.LIVE_DECLARATION_WHAT, self.names(f))

    def test_live_a_declaration_with_a_malformed_anchor_key_fails(self):
        self.reseal_citing(accepted_via="controller_declaration", controller_binding_digest="cd" * 32,
                           controller_anchor_key_id="zz")
        self.assertEqual(self.run_live()["result"], "FAIL")

    def test_live_a_declaration_anchored_on_the_nodes_own_key_fails(self):
        own = self.citing()["key_id"]
        self.reseal_citing(accepted_via="controller_declaration", controller_binding_digest="cd" * 32,
                           controller_anchor_key_id=own)
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("a declaration anchored on the node's own key", f["detail"])

    def test_live_a_declaration_without_its_digest_fails(self):
        self.reseal_citing(accepted_via="controller_declaration", controller_anchor_key_id="ef" * 32)
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("no binding digest or a malformed anchor key", f["detail"])

    def test_live_a_citing_record_with_no_accepted_via_is_not_checked_never_a_pass(self):
        self.reseal_citing(accepted_via=None, sender_key_id=None)
        f = self.run_live()
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        [acc] = self.acceptance_entries(f)
        self.assertEqual(acc["issue"], step.LIVE_NO_VIA)
        self.assertIn("sealed by a build before the pinned one", acc["why"])

    # the producer signature, in a second language (cross_language signature, AAC#463)
    def held_path(self):
        return self.ledger() / "received-capsules.jsonl"

    def list_sender(self, key=None):
        """lab-b, the node lab-a received the half from, publishes a listing with this key (default the half's)."""
        key = key or json.loads(self.held_path().read_text())["key_id"]
        (self.nodes / f"{'b1' * 32}.json").write_text(json.dumps({"node_id": "b1" * 32, "key_id": key}))

    def test_live_a_half_authored_under_the_key_its_sender_publishes_is_ok(self):
        self.list_sender()
        f = self.run_live()
        [c] = json.loads((self.out / "lab-a" / "checks.json").read_text())["held"]
        self.assertEqual(c["signature"]["state"], "ok")
        self.assertNotIn("the held half's producer signature, in a second language", self.names(f))

    def test_live_a_half_authored_under_another_key_than_its_sender_publishes_fails(self):
        self.list_sender("cd" * 32)
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("not the key " + "cd" * 32 + " the sender publishes", f["detail"])

    def test_live_a_half_the_verifier_cannot_decode_is_bad_not_a_stopped_step(self):
        old = Path(self.tmp.name) / "strict-verifier"
        old.write_text("#!/bin/sh\necho 'invalid UTF-8 in input' >&2\nexit 2\n")
        os.chmod(old, 0o755)
        real = step.cross_language
        step.cross_language = lambda: old
        try:
            state, detail = step.signature_check(self.held_path().read_text(), "00" * 32, None)
        finally:
            step.cross_language = real
        self.assertEqual(state, "bad")
        self.assertIn("cannot read the record", detail)

    def test_live_an_invalid_producer_signature_fails(self):
        held = json.loads(self.held_path().read_text())
        sig = held["signature"]
        held["signature"] = sig[:-4] + ("0000" if not sig.endswith("0000") else "1111")
        self.held_path().write_text(json.dumps(held, separators=(",", ":")) + "\n")
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("held-1 signature", f["detail"])

    def test_live_a_half_authored_under_another_key_than_the_accepted_one_fails(self):
        self.reseal_citing(sender_key_id="ab" * 32)
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("authored under key", f["detail"])
        self.assertIn("not the sender key the node accepted", f["detail"])

    def test_live_an_unclaimed_half_is_not_checked_never_a_pass(self):
        held = json.loads(self.held_path().read_text())
        held.pop("signature"); held.pop("key_id")
        self.held_path().write_text(json.dumps(held, separators=(",", ":")) + "\n")
        f = self.run_live()
        self.assertEqual(json.loads((self.out / "lab-a" / "checks.json").read_text())["held"][0]["signature"]["state"],
                         "unclaimed")
        gap = self.names(f)["the held half's producer signature, in a second language"]
        self.assertIn("1 are unclaimed", gap["why"])

    def test_live_an_uncited_half_still_has_its_signature_checked(self):
        records = [r for r in self.lines() if "received_half" not in (r["model_attestation"].get("compute_attestation") or {})]
        self.write_lines(records)
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("held-1 cited", f["detail"])
        [c] = json.loads((self.out / "lab-a" / "checks.json").read_text())["held"]
        self.assertEqual(c["signature"]["state"], "uncited")
        self.assertNotIn("the held half's producer signature, in a second language", self.names(f))

    def test_live_a_half_with_no_accepted_sender_key_is_unkeyed_not_a_pass(self):
        self.reseal_citing(accepted_via=None, sender_key_id=None)
        f = self.run_live()
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        gap = self.names(f)["the held half's producer signature, in a second language"]
        self.assertIn("names no sender_key_id", gap["why"])

    def test_live_a_verifier_without_the_signature_mode_stops_the_step(self):
        old = Path(self.tmp.name) / "old-verifier"
        old.write_text("#!/bin/sh\necho usage >&2\nexit 2\n")
        os.chmod(old, 0o755)
        real = step.cross_language
        step.cross_language = lambda: old
        try:
            with self.assertRaises(RuntimeError) as e:
                step.signature_check(self.held_path().read_text(), "00" * 32, None)
        finally:
            step.cross_language = real
        self.assertIn("steps/01-seal.setup.sh", str(e.exception))

    # the tie, loop#107: a record naming both halves, the checkpoint cited, "held, never tied"
    def names(self, f):
        return {n["what"]: n for n in f["not_checked"]}

    def test_live_a_half_tied_to_its_own_half_leaves_no_untied_gap(self):
        f = self.run_live()
        self.assertNotIn(step.LIVE_NEVER_TIED, self.names(f))
        self.assertNotIn("a tie that is ambiguous at verify time", self.names(f))
        [c] = json.loads((self.out / "lab-a" / "checks.json").read_text())["held"]
        self.assertEqual(c["tied"]["state"], "tied")
        self.assertTrue(c["inclusion_cited"]["ok"])
        self.assertIn("1 tied to the node's own half, 0 held, never tied, 0 ambiguous", f["detail"])

    def test_live_a_citing_record_with_no_own_half_is_held_never_tied(self):
        self.reseal_citing(own_half_capsule_id=None)
        f = self.run_live()
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        gap = self.names(f)[step.LIVE_NEVER_TIED]
        self.assertIn("1 current-build halves are cited by a record with no own_half_capsule_id", gap["why"])
        self.assertEqual(gap["issue"], "Mesh-LLM/mesh-llm#2747")
        self.assertIn("0 tied to the node's own half, 1 held, never tied", f["detail"])
        self.assertEqual(finding.check(self.findings), [])

    def test_live_result_is_pass_only_with_every_node_passing_and_no_entry_left(self):
        gap = [{"what": "w", "why": "y", "issue": "i"}]
        self.assertEqual(step.live_result(["PASS", "PASS", "PASS"], []), "PASS")
        self.assertEqual(step.live_result(["PASS", "PASS", "PASS"], gap), "NOT_CHECKED")
        self.assertEqual(step.live_result(["PASS", "NOT_CHECKED", "PASS"], []), "NOT_CHECKED")
        self.assertEqual(step.live_result(["PASS", "FAIL", "NOT_CHECKED"], gap), "FAIL")

    def test_live_a_tied_exchange_held_on_one_node_only_is_named_not_passed(self):
        f = self.run_live()
        gap = self.names(f)[step.LIVE_ONE_SIDED_WHAT]
        self.assertIn("1 current-build exchanges are tied on the node holding the counterparty's half", gap["why"])
        self.assertEqual(finding.check(self.findings), [])

    def test_live_a_node_reporting_the_push_off_is_named(self):
        (self.lab / "nodes" / "lab-a" / "data" / "sharing-effective.json").write_text(json.dumps(
            {"v": 1, "share_record_at_completion": "off", "source": "env", "written_at": "2026-10-30T01:00:00Z"}))
        f = self.run_live()
        self.assertTrue(any(n["what"] == step.LIVE_ONE_SIDED_WHAT and "lab-a report share_record_at_completion off"
                            in n["why"] for n in f["not_checked"]), f["not_checked"])

    def test_is_settlement_leg_needs_the_shape_no_nonce_and_a_record_naming_no_own_half(self):
        leg = {"model_attestation": {"model_id": "n/a-settlement-leg", "compute_attestation": {}}, "settlement": {}}
        record = lambda **rh: {"model_attestation": {"compute_attestation": {"received_half": rh}}}  # noqa: E731
        self.assertTrue(step.is_settlement_leg(leg, record(accepted_via="host_announced")))
        self.assertFalse(step.is_settlement_leg({**leg, "settlement": None}, record()))
        self.assertFalse(step.is_settlement_leg({**leg, "model_attestation": {"model_id": "lab-model"}}, record()))
        self.assertFalse(step.is_settlement_leg({}, record()))
        # a sender cannot mark a chat half as a leg: a nonce, or a record that names an own half, is tied; a binding
        # the record names does not make a leg a half (its binding is judged either way)
        nonce = {"model_attestation": {"model_id": "n/a-settlement-leg", "compute_attestation": {
            "x-mesh-poc-v1": {"client_nonce": "n", "role": "served"}}}, "settlement": {}}
        self.assertFalse(step.is_settlement_leg(nonce, record()))
        self.assertFalse(step.is_settlement_leg(leg, record(own_half_capsule_id="ab" * 32)))
        self.assertTrue(step.is_settlement_leg(leg, record(controller_binding_digest="cd" * 32)))

    def run_in_process(self):
        with mock.patch.dict(os.environ, LAB_DIR=str(self.lab), LOOP_NODES_DIR=str(self.nodes)):
            code = step.main(["--population", str(self.lab), "--out", str(self.out)])
        self.assertEqual(code, 0)
        return json.loads((self.out / "finding.json").read_text())

    def test_live_a_settlement_leg_is_counted_not_tied_and_not_an_unbound_sender(self):
        # The fixture's held half is a chat half; the predicate is what separates a leg, so it is forced for the run.
        self.reseal_citing(own_half_capsule_id=None)
        with mock.patch.object(step, "is_settlement_leg", lambda capsule, record: True):
            f = self.run_in_process()
        self.assertNotIn(step.LIVE_NEVER_TIED, self.names(f))
        self.assertNotIn(step.LIVE_UNBOUND_WHAT, self.names(f))
        self.assertIn("0 halves, 0 tied to the node's own half, 0 held, never tied, 0 ambiguous", f["detail"])
        self.assertIn("1 settlement legs besides", f["detail"])
        self.assertNotIn("carries no client_nonce", f["detail"])
        # only legs were held: no tie was read, and the step says so
        self.assertIn(step.ACCEPTANCE_WHAT, self.names(f))
        [c] = json.loads((self.out / "lab-a" / "checks.json").read_text())["held"]
        self.assertTrue(c["leg"])
        self.assertNotIn("tied", c)
        self.assertTrue(c["signature"]["ok"] and c["pushed_under_checkpoint"]["ok"] and c["accepted"]["ok"])

    def test_live_a_settlement_leg_accepted_under_another_key_still_fails_its_acceptance(self):
        self.reseal_citing(own_half_capsule_id=None, sender_key_id="ab" * 32)
        with mock.patch.object(step, "is_settlement_leg", lambda capsule, record: True):
            f = self.run_in_process()
        self.assertEqual(f["result"], "FAIL")
        [c] = json.loads((self.out / "lab-a" / "checks.json").read_text())["held"]
        self.assertTrue(c["leg"])
        self.assertFalse(c["accepted"]["ok"], c["accepted"])
        self.assertIn("held-1 accepted (", f["detail"])

    def test_live_a_tie_naming_an_own_half_the_chain_does_not_hold_fails(self):
        self.reseal_citing(own_half_capsule_id="ab" * 32)
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("which the node's chain does not hold", f["detail"])

    def test_live_a_tie_to_an_own_half_of_the_same_role_or_another_nonce_fails(self):
        def role(body):
            body["model_attestation"]["compute_attestation"]["x-mesh-poc-v1"]["role"] = "served"
        self.set_own(role)
        self.assertIn("not the opposite role", self.run_live()["detail"])
        self.setUp()
        def nonce(body):
            body["model_attestation"]["compute_attestation"]["x-mesh-poc-v1"]["client_nonce"] = "other"
        self.set_own(nonce)
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("not the opposite role", f["detail"])

    def test_live_a_second_own_half_under_the_nonce_makes_the_tie_ambiguous(self):
        twin = self.body(self.own)
        twin["action_id"] = "mesh-poc/own-half-twin"
        self.write_lines(self.lines() + [self.seal(twin, self.own["key_id"])])
        f = self.run_live()
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        gap = self.names(f)["a tie that is ambiguous at verify time"]
        self.assertIn("1 citing records name an own half", gap["why"])
        self.assertIn("1 ambiguous", f["detail"])

    def test_live_a_second_held_half_under_the_nonce_makes_the_tie_ambiguous(self):
        held = self.ledger() / "received-capsules.jsonl"
        first = json.loads(held.read_text())
        second = self.body(first)
        second["action_id"] = "mesh-poc/second"
        sealed = self.seal(second, first["key_id"])
        held.write_text(held.read_text() + json.dumps(sealed, separators=(",", ":")) + "\n")
        f = self.run_live()
        self.assertIn("a tie that is ambiguous at verify time", self.names(f))

    def test_live_an_inclusion_line_with_a_digest_the_checkpoint_does_not_have_fails(self):
        path = self.ledger() / "received-inclusion.jsonl"
        inc = json.loads(path.read_text())
        inc["checkpoint_digest"] = "ee" * 32
        path.write_text(json.dumps(inc) + "\n")
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("recomputed from the held checkpoint", f["detail"])

    def test_live_an_inclusion_record_altered_after_sealing_fails(self):
        records = self.lines()
        for r in records:
            if "counterparty_inclusion" in (r["model_attestation"].get("compute_attestation") or {}):
                r["action_id"] += "-altered"
        self.write_lines(records)
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("held-1 inclusion_cited", f["detail"])
        self.assertIn("recomputed", f["detail"])

    def test_live_a_driver_that_prints_no_checkpoint_digest_stops_the_step(self):
        old = self.tmp.name + "/old-driver"
        Path(old).write_text("#!/bin/sh\ncase $1 in held) echo '{\"ok\": true, \"detail\": \"x\"}';; esac\n")
        os.chmod(old, 0o755)
        r = run_step(self.lab, self.out, env=dict(os.environ, LAB_DIR=str(self.lab), LOOP_NODES_DIR=str(self.nodes),
                                                  LOOP_STEP05_DEAL_BIN=old))
        self.assertEqual(r.returncode, 2)
        self.assertIn("steps/05-deal.setup.sh", r.stderr)

    def test_live_an_unknown_role_is_not_the_opposite_of_served(self):
        def role(body):
            body["model_attestation"]["compute_attestation"]["x-mesh-poc-v1"]["role"] = "observer"
        self.set_own(role)
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("not the opposite role", f["detail"])

    def test_live_a_cutoff_that_is_not_an_rfc3339_time_with_a_zone_stops_the_step(self):
        for bad in ("yesterday", "2026-10-30T00:00:00", ""):
            r = run_step(self.lab, self.out, env=dict(os.environ, LAB_DIR=str(self.lab), LOOP_NODES_DIR=str(self.nodes),
                                                      LOOP_SEALED_SINCE=bad))
            self.assertEqual(r.returncode, 2, bad)
            self.assertIn("LOOP_SEALED_SINCE", r.stderr)

    def test_live_a_cutoff_with_seconds_is_taken_at_its_minute(self):
        stamp = self.citing()["timestamp"]  # a whole minute, as the plugin seals
        self.assertTrue(stamp.endswith(":00Z") or ":00." in stamp, stamp)
        f = self.run_live(LOOP_SEALED_SINCE=stamp.replace(":00Z", ":30Z"))
        self.assertIn("1 halves, 1 tied", f["detail"])

    def test_live_a_half_sealed_exactly_at_the_cutoff_is_current(self):
        stamp = self.citing()["timestamp"]
        f = self.run_live(LOOP_SEALED_SINCE=stamp)
        self.assertIn("1 halves, 1 tied", f["detail"])

    def test_live_no_counterparty_inclusion_record_for_a_current_half_fails(self):
        self.write_lines([r for r in self.lines() if "counterparty_inclusion" not in
                          (r["model_attestation"].get("compute_attestation") or {})])
        f = self.run_live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("held-1 inclusion_cited", f["detail"])

    # judged only under the current build
    def test_live_an_earlier_half_is_counted_and_not_judged_for_acceptance_or_tie(self):
        self.reseal_citing(own_half_capsule_id=None)
        stamp = self.citing()["timestamp"]
        f = self.run_live(LOOP_SEALED_SINCE="2099-01-01T00:00:00Z")
        self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
        names = self.names(f)
        self.assertNotIn(step.LIVE_NEVER_TIED, names)
        [acc] = self.acceptance_entries(f)
        self.assertEqual(acc["what"], step.ACCEPTANCE_WHAT)
        self.assertIn("0 halves, 0 tied", f["detail"])
        self.assertIn("1 sealed earlier, not judged", f["detail"])
        self.assertIn("no node holds a current-build exchange half", acc["why"])
        self.assertIn("sealed since 2099-01-01T00:00:00Z", f["detail"])
        self.assertEqual(json.loads((self.out / "lab-a" / "checks.json").read_text())["sealed_since"], "2099-01-01T00:00:00Z")
        # the same half is judged when the build started before it was sealed
        f = self.run_live(LOOP_SEALED_SINCE="2000-01-01T00:00:00Z")
        self.assertIn(step.LIVE_NEVER_TIED, self.names(f))
        self.assertTrue(stamp)

    def test_live_an_earlier_half_still_fails_on_its_bytes(self):
        held = self.ledger() / "received-capsules.jsonl"
        capsule = json.loads(held.read_text())
        capsule["model_attestation"]["model_id"] = "another-model"
        held.write_text(json.dumps(capsule, separators=(",", ":")) + "\n")
        self.assertEqual(self.run_live(LOOP_SEALED_SINCE="2099-01-01T00:00:00Z")["result"], "FAIL")

    # the node's own report of its sharing policy, loop#109
    def test_live_sharing_effective_is_read_as_node_reported_unsigned(self):
        (self.lab / "nodes" / "lab-a" / "data" / "sharing-effective.json").write_text(json.dumps(
            {"v": 1, "share_record_at_completion": "counterparty", "source": "default",
             "written_at": "2026-10-30T01:00:00Z"}))
        f = self.run_live()
        self.assertIn("node lab-a reports (node-reported, unsigned) share_record_at_completion counterparty from "
                      "default at 2026-10-30T01:00:00Z", f["detail"])
        self.assertIn("the push itself is judged from the sealed records", f["detail"])
        self.assertIn("lab-a/sharing-effective.json", [p["path"] for p in f["proof"]])
        self.assertEqual(self.names(f)[step.LIVE_DEFAULT_ABSENT["what"]]["why"].count("lab-b"), 1)
        self.assertNotIn("lab-a", self.names(f)[step.LIVE_DEFAULT_ABSENT["what"]]["why"])
        self.assertEqual(finding.check(self.findings), [])

    def test_live_an_unknown_value_the_plugin_resolved_to_off_shows_its_raw_value(self):
        (self.lab / "nodes" / "lab-a" / "data" / "sharing-effective.json").write_text(json.dumps(
            {"v": 1, "share_record_at_completion": "off", "source": "console", "written_at": "t", "raw_value": "everyone"}))
        self.assertIn("raw value 'everyone' is not one the plugin knows", self.run_live()["detail"])

    def test_live_an_unknown_sharing_value_is_not_read_as_a_report(self):
        (self.lab / "nodes" / "lab-a" / "data" / "sharing-effective.json").write_text(
            json.dumps({"v": 1, "share_record_at_completion": "everyone"}))
        self.assertNotIn("node-reported", self.run_live()["detail"])


if __name__ == "__main__":
    unittest.main()
