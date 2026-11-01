"""Step 03 Advertise: the commitment is published, fetched back and verified; the CI check refuses counts.

Fixtures: tests/fixtures/step03/ (made by its make_fixtures.py). The keys there are TEST keys.
Run: python3 -B -m unittest tests.test_step03_advertise -v
"""
import base64
import importlib.machinery
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "step03"
POPULATION = FIXTURES / "population" / "rehearsal"
ALICE_KEY = POPULATION / "keys" / "alice" / "node-key.pem"
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "steps"))
import check_commitment as cc  # noqa: E402
import finding  # noqa: E402

TOOLS = shutil.which("openssl") and shutil.which("git")
SKIP_WHY = "SKIPPED LOUDLY: step 03 signs and verifies with the openssl command and publishes with git; one is missing"


def load_step():
    loader = importlib.machinery.SourceFileLoader("step03_advertise", str(ROOT / "steps" / "03-advertise"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def alice_node_id():
    pem = (POPULATION / "keys" / "alice" / "node-key.pub.pem").read_text()
    return cc.key_id_of(cc.spki_from_pem(pem))


def on_alice(change):
    """A publish hook applying change(commitment_path, policy_path) to alice's files only."""
    node_id = alice_node_id()

    def hook(carrier):
        tree = carrier / "nodes" / "commitments"
        path = tree / f"{node_id}.json"
        if path.exists() and not getattr(hook, "done", False):
            hook.done = True
            change(path, tree / "policies" / f"{node_id}.json")
    return hook


def add_record_count(path, _policy):
    doc = json.loads(path.read_text())
    doc["record_count"] = 12
    path.write_text(json.dumps(cc.sign(doc, ALICE_KEY), indent=2))  # re-signed: only the count is wrong


def flip_signature_byte(path, _policy):
    doc = json.loads(path.read_text())
    raw = bytearray(cc.unb64url(doc["sig"]["value"]))
    raw[0] ^= 0x01
    doc["sig"]["value"] = cc.b64url(bytes(raw))
    path.write_text(json.dumps(doc, indent=2))


def alter_policy(_path, policy):
    doc = json.loads(policy.read_text())
    doc["policy"]["stranger_safe"] = ["checkpoints", "history_card/1", "selections"]
    policy.write_text(json.dumps(doc, indent=2))  # the commitment's digest no longer matches


@unittest.skipUnless(TOOLS, SKIP_WHY)
class StepTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.step = load_step()

    def run_step(self, hook=None):
        self.step.PUBLISH_HOOK = hook
        out = self.tmp / "findings" / "03-advertise"
        self.assertEqual(self.step.main(["--population", str(POPULATION), "--out", str(out)]), 0)
        return json.loads((out / "finding.json").read_text()), out

    def nodes(self, out):
        return {n["name"]: n for n in json.loads((out / "verification.json").read_text())["nodes"]}

    def test_rehearsal_passes_and_the_finding_checks(self):
        f, out = self.run_step()
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertEqual(finding.check(out.parent), [])
        nodes = self.nodes(out)
        self.assertEqual({n["result"] for n in nodes.values()}, {"PASS"})
        self.assertIn("HEAD:nodes/commitments/", nodes["alice"]["carrier"])
        self.assertEqual(nodes["alice"]["inputs"], "advertise/alice")
        self.assertEqual(nodes["bob"]["inputs"], "stand-in")
        # The staple's signature and the checkpoint are outside the assertion: named in detail, never passed.
        self.assertEqual(f["not_checked"], [])
        self.assertEqual(finding.gaps(f["detail"]), [
            ("the stapled history_card/1 signature (alice, bob) is not verified, as no released history_card/1 "
             "verifier is callable from the loop", "action-state-group/agent-action-capsule#430")])
        self.assertIn("The stapled checkpoint attestation is step 02's assertion", f["detail"])
        self.assertIn("The staple of bob is a rehearsal stand-in.", f["detail"])
        # The bytes fetched back are the bytes in the carrier bundle's HEAD.
        repo = self.tmp / "clone"
        subprocess.run(["git", "-c", "advice.detachedHead=false", "clone", "-q", str(out / "carrier.bundle"), str(repo)], check=True)
        path = f"nodes/commitments/{alice_node_id()}.json"
        shown = subprocess.run(["git", "-C", str(repo), "show", f"HEAD:{path}"], capture_output=True, check=True).stdout
        self.assertEqual(shown, (out / "alice.commitment.json").read_bytes())
        doc = json.loads(shown)
        self.assertEqual(doc["human_gated"], True)
        self.assertEqual(doc["answers_within"], "PT24H")
        self.assertEqual(cc.verify(doc, lambda uri: (out / "alice.policy.json").read_bytes()), ([], []))

    def test_planted_record_count_is_refused(self):
        f, out = self.run_step(on_alice(add_record_count))
        self.assertEqual(f["result"], "FAIL")
        alice = self.nodes(out)["alice"]
        self.assertIn("record_count: a key named as a count; a commitment carries no counts", alice["errors"])
        self.assertNotIn("signature does not verify under spki", alice["errors"])
        self.assertEqual(self.nodes(out)["bob"]["result"], "PASS")
        self.assertEqual(finding.check(out.parent), [])

    def test_planted_flipped_signature_byte_fails(self):
        f, out = self.run_step(on_alice(flip_signature_byte))
        self.assertEqual(f["result"], "FAIL")
        self.assertEqual(self.nodes(out)["alice"]["errors"], ["signature does not verify under spki"])
        self.assertEqual(finding.check(out.parent), [])

    def test_planted_policy_digest_mismatch_fails(self):
        f, out = self.run_step(on_alice(alter_policy))
        self.assertEqual(f["result"], "FAIL")
        self.assertEqual(self.nodes(out)["alice"]["errors"],
                         ["lcap.policy_digest is not the sha256 of the policy bytes at lcap.policy_uri"])
        self.assertEqual(finding.check(out.parent), [])


def goose_commitment(key_pem, out_dir):
    """A /1 commitment as G1's loop_advertise writes it (node book goose/node, community goose), signed by key_pem,
    with its signed policy: {commitment, policy} paths under out_dir. TEST keys only."""
    import hashlib
    spki = cc.spki_from_pem(Path(str(key_pem).replace("node-key.pem", "node-key.pub.pem")).read_text())
    key_id = cc.key_id_of(spki)
    statement = b"goose node book checkpoint statement"
    artifact = json.dumps({"evidence_stream": "goose/node", "history": []}, separators=(",", ":")).encode()
    card = json.dumps({"profile": cc.STAPLE_PROFILE, "subject": {"full_history": None}, "derivation": "history_card/1",
                       "anchor": {"digest": hashlib.sha256(statement).hexdigest()}, "responder": key_id,
                       "artifact_digest": hashlib.sha256(artifact).hexdigest(), "artifact": cc.b64url(artifact),
                       "issued_at": "2026-11-01T00:00:00Z", "sig": "test"}, sort_keys=True).encode()
    policy = cc.sign({"profile": cc.POLICY_PROFILE, "key_id": key_id,
                      "policy": {"stranger_safe": ["history_card/1"], "share_deal_half": "counterparty"}}, key_pem)
    policy_bytes = (json.dumps(policy, indent=2, sort_keys=True) + "\n").encode()
    b64 = lambda raw: base64.b64encode(raw).decode()
    doc = cc.sign({
        "profile": cc.PROFILE_1, "node_id": key_id, "key_id": key_id, "spki": b64(spki),
        "issued_at": "2026-11-01T00:00:00Z",
        "ask": {"endpoint": "book:goose/node", "bindings": ["evidencebook/respond"]},
        "answers": {"subjects": ["full_history"], "derivations": ["history_card/1"]},
        "lcap": {"policy_digest": hashlib.sha256(policy_bytes).hexdigest(), "policy_uri": f"policies/{key_id}.json",
                 "predicate_classes": ["performed:counterparty_half"]},
        "checkpoint": {"sha256": hashlib.sha256(statement).hexdigest(), "bytes": b64(statement)},
        "human_gated": True, "answers_within": "PT24H",
        "history_cards": [{"stream": "goose/node", "community": "goose", "derivation": "history_card/1",
                           "sha256": hashlib.sha256(card).hexdigest(), "bytes": b64(card)}],
    }, key_pem)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "commitment.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    (out_dir / "policy.json").write_bytes(policy_bytes)
    return {"commitment": str(out_dir / "commitment.json"), "policy": str(out_dir / "policy.json")}


@unittest.skipUnless(TOOLS, SKIP_WHY)
class NodeSignedTest(unittest.TestCase):
    """The Goose rehearsal (scripts/goose_rehearsal.py): a node's commitment is the one it signed, published as is."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.step = load_step()
        shutil.copytree(FIXTURES / "population", self.tmp / "population")
        self.pop = self.tmp / "population" / "rehearsal"
        self.keys = self.pop / "keys"

    def run_with(self, signed, rev=None):
        doc = json.loads((self.pop / "population.json").read_text())
        doc["goose"] = {"commitments": signed, "rev": rev or finding.pins()["goose-g1"][1]}
        (self.pop / "population.json").write_text(json.dumps(doc))
        out = self.tmp / "findings" / "03-advertise"
        self.assertEqual(self.step.main(["--population", str(self.pop), "--out", str(out)]), 0)
        nodes = {n["name"]: n for n in json.loads((out / "verification.json").read_text())["nodes"]}
        return json.loads((out / "finding.json").read_text()), nodes, out

    def test_the_node_signed_commitment_is_published_byte_for_byte_and_passes(self):
        signed = goose_commitment(self.keys / "alice" / "node-key.pem", self.tmp / "alice-signed")
        f, nodes, out = self.run_with({"alice": signed})
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertEqual(finding.check(out.parent), [])
        self.assertEqual(nodes["alice"]["inputs"], "signed by the node")
        self.assertIn("never the harness", nodes["alice"]["signed_by"])
        self.assertEqual((out / "alice.commitment.json").read_bytes(), Path(signed["commitment"]).read_bytes())
        self.assertEqual([c["stream"] for c in nodes["alice"]["history_cards"]], ["goose/node"])
        self.assertEqual(nodes["bob"]["inputs"], "stand-in")  # a node without one is signed by the harness as before
        # The Goose rehearsal's commitments are G1's: the finding pins goose-g1, and the key they must be under is kept.
        self.assertEqual(list(f["pinned"]), ["goose-g1"])
        self.assertIn("alice.node-key.pub.pem", [x["path"] for x in f["proof"]])
        self.assertEqual((out / "alice.node-key.pub.pem").read_bytes(),
                         (self.keys / "alice" / "node-key.pub.pem").read_bytes())

    def test_a_goose_checkout_other_than_the_pin_is_not_checked(self):
        signed = goose_commitment(self.keys / "alice" / "node-key.pem", self.tmp / "alice-signed")
        f, nodes, out = self.run_with({"alice": signed}, rev="0" * 40)
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertEqual([x["what"] for x in f["not_checked"] if x["what"] == "goose-g1"], ["goose-g1"])
        self.assertIn("goose-g1 " + "0" * 40, nodes["alice"]["signed_by"])
        self.assertEqual(finding.check(out.parent), [])

    def test_a_commitment_under_another_key_than_the_nodes_fails(self):
        signed = goose_commitment(self.keys / "bob" / "node-key.pem", self.tmp / "bob-signed")
        f, nodes, out = self.run_with({"alice": signed})
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("the commitment is not under this node's key", nodes["alice"]["errors"])
        self.assertEqual(finding.check(out.parent), [])

    def test_a_hostile_or_missing_node_id_is_published_under_the_key_and_refused(self):
        key = self.keys / "alice" / "node-key.pem"
        key_id = cc.key_id_of(cc.spki_from_pem((self.keys / "alice" / "node-key.pub.pem").read_text()))
        for change in (lambda d: d.__setitem__("node_id", "../../escape"), lambda d: d.pop("node_id")):
            with self.subTest(change=change):
                shutil.rmtree(self.tmp / "findings", ignore_errors=True)
                signed = goose_commitment(key, self.tmp / "alice-signed")
                path = Path(signed["commitment"])
                doc = json.loads(path.read_text())
                change(doc)
                path.write_text(json.dumps(cc.sign(doc, key)))
                f, nodes, out = self.run_with({"alice": signed})
                self.assertEqual(f["result"], "FAIL")
                self.assertEqual(nodes["alice"]["node_id"], key_id)
                self.assertTrue(any("node_id" in e for e in nodes["alice"]["errors"]), nodes["alice"]["errors"])
                repo = self.tmp / "clone"
                shutil.rmtree(repo, ignore_errors=True)
                subprocess.run(["git", "clone", "-q", str(out / "carrier.bundle"), str(repo)], check=True)
                files = sorted(str(p.relative_to(repo)) for p in repo.rglob("*.json"))
                self.assertTrue(all(p.startswith("nodes/commitments/") for p in files), files)
                self.assertIn(f"nodes/commitments/{key_id}.json", files)

    def test_a_node_signed_commitment_with_an_extra_member_fails(self):
        signed = goose_commitment(self.keys / "alice" / "node-key.pem", self.tmp / "alice-signed")
        path = Path(signed["commitment"])
        doc = json.loads(path.read_text())
        doc["deals"] = "several"
        path.write_text(json.dumps(cc.sign(doc, self.keys / "alice" / "node-key.pem")))
        f, nodes, _ = self.run_with({"alice": signed})
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("unknown field deals", nodes["alice"]["errors"])


@unittest.skipUnless(TOOLS, SKIP_WHY)
class LiveTest(unittest.TestCase):
    """Live: the harness never signs; it reads a commitment the node published, or reports NOT_CHECKED on the
    gap still open: the pinned plugin makes the commitment (capsules-plugin PR #75), nobody has published it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.step = load_step()
        self.step.PUBLISH_HOOK = None

    def run_live(self, population, carrier):
        def never(*_a, **_k):
            raise AssertionError("the harness signed or published for a live node")
        self.step.build = self.step.publish = never
        out = self.tmp / "findings" / "03-advertise"
        argv = ["--population", str(FIXTURES / "population" / population), "--out", str(out), "--carrier", str(carrier)]
        self.assertEqual(self.step.main(argv), 0)
        self.assertEqual(finding.check(out.parent), [])
        record = json.loads((out / "finding.json").read_text())
        # judge_live on a copy of only the cited files gives the same finding: what step 08 checks (loop#188).
        pop = json.loads((FIXTURES / "population" / population / "population.json").read_text())
        work = self.tmp / "again" / "03-advertise"
        shutil.rmtree(work.parent, ignore_errors=True)
        for p in record["proof"]:
            (work / p["path"]).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(out / p["path"], work / p["path"])
        again = self.step.judge_live(pop, work, record["started"])
        self.assertEqual((again["result"], again["not_checked"], again["detail"]),
                         (record["result"], record["not_checked"], record["detail"]))
        return record, out

    def published_carrier(self, key_id=None):
        """A repository where alice's test key, playing the node, published its commitment, listed under key_id."""
        out = self.tmp / "rehearsal" / "03-advertise"
        self.step.main(["--population", str(POPULATION), "--out", str(out)])
        repo = self.tmp / "carrier"
        subprocess.run(["git", "clone", "-q", str(out / "carrier.bundle"), str(repo)], check=True)
        node_id = alice_node_id()
        (repo / "nodes" / f"{node_id}.json").write_text(json.dumps({"node_id": node_id, "key_id": key_id or node_id}))
        subprocess.run(["git", "-C", str(repo), *self.step.GIT_ID, "add", "nodes"], check=True)
        subprocess.run(["git", "-C", str(repo), *self.step.GIT_ID, "commit", "-qm", "list"], check=True)
        return repo

    def test_live_today_is_not_checked_on_the_node_side_issue(self):
        f, out = self.run_live("live", ROOT)
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertEqual({x["issue"] for x in f["not_checked"]}, {self.step.LIVE_ISSUE})
        self.assertEqual(self.step.LIVE_ISSUE, "no open issue yet (to be filed)")
        for x in f["not_checked"]:
            self.assertIn("capsules-plugin PR #75, in the pin", x["why"])
            self.assertIn("not been published to nodes/commitments/", x["why"])
        self.assertFalse(list(out.glob("*.commitment.json")))
        self.assertNotIn("carrier.bundle", [p["path"] for p in f["proof"]])

    def test_live_node_without_a_published_commitment_is_not_checked(self):
        f, _ = self.run_live("live-published", ROOT)  # this repository's HEAD lists no commitments yet
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertEqual([x["issue"] for x in f["not_checked"]], [self.step.LIVE_ISSUE])
        self.assertIn(self.step.LIVE_GAP, f["not_checked"][0]["why"])

    def test_live_node_signed_commitment_verifies(self):
        carrier = self.published_carrier()
        f, out = self.run_live("live-published", carrier)
        self.assertEqual(f["result"], "PASS", f["detail"])
        node = json.loads((out / "verification.json").read_text())["nodes"][0]
        self.assertEqual(node["signed_by"], "the node (never the harness)")

    def test_live_commitment_under_another_key_than_listed_fails(self):
        f, out = self.run_live("live-published", self.published_carrier(key_id="ab" * 32))
        self.assertEqual(f["result"], "FAIL")
        node = json.loads((out / "verification.json").read_text())["nodes"][0]
        self.assertEqual(node["errors"], ["the commitment is not under this node's key"])


    def commit_change(self, repo, path, edit):
        doc = json.loads((repo / path).read_text())
        edit(doc)
        (repo / path).write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
        subprocess.run(["git", "-C", str(repo), *self.step.GIT_ID, "commit", "-qam", "change"], check=True)

    def test_a_commitment_the_checker_stops_on_still_cites_its_policy(self):
        # A commitment missing a required field: the checker returns before it reads the policy, but the policy
        # collect_live kept is cited, so step 08 re-derives the FAIL from the proof (run_live re-runs judge_live).
        carrier = self.published_carrier()
        self.commit_change(carrier, f"nodes/commitments/{alice_node_id()}.json", lambda d: d.pop("answers"))
        f, out = self.run_live("live-published", carrier)
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("lab-1.policy.json", [p["path"] for p in f["proof"]])

    def test_collect_reads_every_path_at_the_one_commit_it_records(self):
        carrier = self.published_carrier()
        first = subprocess.run(["git", "-C", str(carrier), "rev-parse", "HEAD"], capture_output=True,
                               text=True).stdout.strip()
        real, moved = self.step.fetch, []

        def fetch_then_move(repo, path, ref="HEAD"):
            got = real(repo, path, ref)
            if not moved:  # the carrier moves on after the first read
                moved.append(1)
                self.commit_change(carrier, f"nodes/commitments/{alice_node_id()}.json",
                                   lambda d: d.update(answers_within="PT9H"))
            return got
        self.step.fetch = fetch_then_move
        try:
            f, out = self.run_live("live-published", carrier)
        finally:
            self.step.fetch = real
        self.assertEqual(json.loads((out / "carrier.json").read_text())["head"], first)
        at_first = subprocess.run(["git", "-C", str(carrier), "show", f"{first}:nodes/commitments/{alice_node_id()}.json"],
                                  capture_output=True).stdout
        self.assertEqual((out / "lab-1.commitment.json").read_bytes(), at_first)
        self.assertEqual(f["result"], "PASS", f["detail"])


@unittest.skipUnless(TOOLS, SKIP_WHY)
class CheckCommitmentTest(unittest.TestCase):
    """scripts/check_commitment.py, as CI runs it on a nodes/commitments directory."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        step = load_step()
        step.PUBLISH_HOOK = None
        out = self.tmp / "f" / "03-advertise"
        step.main(["--population", str(POPULATION), "--out", str(out)])
        self.dir = self.tmp / "nodes" / "commitments"
        (self.dir / "policies").mkdir(parents=True)
        self.node_id = alice_node_id()
        self.path = self.dir / f"{self.node_id}.json"
        shutil.copy(out / "alice.commitment.json", self.path)
        shutil.copy(out / "alice.policy.json", self.dir / "policies" / f"{self.node_id}.json")

    def cli(self):
        return subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "check_commitment.py"), str(self.dir)],
                              capture_output=True, text=True)

    def edit(self, change, resign=True):
        doc = json.loads(self.path.read_text())
        change(doc)
        self.path.write_text(json.dumps(cc.sign(doc, ALICE_KEY) if resign else doc, indent=2))

    def test_a_good_commitment_is_accepted(self):
        r = self.cli()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1 commitment ok", r.stdout)

    def test_record_count_is_refused(self):
        self.edit(lambda d: d.__setitem__("record_count", 12))
        r = self.cli()
        self.assertEqual(r.returncode, 1)
        self.assertIn("record_count: a key named as a count", r.stderr)

    def test_flipped_signature_is_refused(self):
        def flip(d):
            raw = bytearray(cc.unb64url(d["sig"]["value"]))
            raw[-1] ^= 0x80
            d["sig"]["value"] = cc.b64url(bytes(raw))
        self.edit(flip, resign=False)
        r = self.cli()
        self.assertEqual(r.returncode, 1)
        self.assertIn("signature does not verify under spki", r.stderr)

    def test_missing_field_is_refused(self):
        for field in ("human_gated", "answers_within", "history_card", "checkpoint", "lcap"):
            with self.subTest(field=field):
                self.setUp()
                self.edit(lambda d: d.pop(field))
                r = self.cli()
                self.assertEqual(r.returncode, 1)
                self.assertIn(f"missing {field}", r.stderr)

    def test_a_controller_binding_digest_is_accepted_and_optional(self):
        digest = "ab" * 32
        self.edit(lambda d: d.__setitem__("controller_binding", {"digest": digest}))
        r = self.cli()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("1 commitment ok", r.stdout)
        self.edit(lambda d: d.pop("controller_binding"))
        self.assertEqual(self.cli().returncode, 0)

    def test_a_malformed_controller_binding_is_refused(self):
        for value, says in [({"digest": "AB" * 32}, "controller_binding.digest is not a sha256"),
                            ({"digest": "ab" * 32 + "\n"}, "controller_binding.digest is not a sha256"),
                            ({}, "missing controller_binding.digest"),
                            ({"digest": "ab" * 32, "keys": ["ab" * 32]}, "unknown field controller_binding.keys"),
                            ("ab" * 32, "controller_binding must be an object")]:
            with self.subTest(value=value):
                self.setUp()
                self.edit(lambda d: d.__setitem__("controller_binding", value))
                r = self.cli()
                self.assertEqual(r.returncode, 1)
                self.assertIn(says, r.stderr)

    def test_missing_policy_field_is_refused(self):
        self.edit(lambda d: d["lcap"].pop("policy_digest"))
        r = self.cli()
        self.assertEqual(r.returncode, 1)
        self.assertIn("missing lcap.policy_digest", r.stderr)

    def test_policy_digest_must_match_the_policy_bytes(self):
        policy = self.dir / "policies" / f"{self.node_id}.json"
        policy.write_bytes(policy.read_bytes() + b"\n")
        r = self.cli()
        self.assertEqual(r.returncode, 1)
        self.assertIn("lcap.policy_digest is not the sha256", r.stderr)

    def test_a_staple_presented_by_another_key_is_refused(self):
        def swap(d):
            card = json.loads(base64.b64decode(d["history_card"]["bytes"]))
            card["responder"] = "0" * 63 + "1"
            raw = json.dumps(card).encode()
            d["history_card"] = {"derivation": "history_card/1", "sha256": cc.hashlib.sha256(raw).hexdigest(),
                                 "bytes": base64.b64encode(raw).decode()}
        self.edit(swap)
        r = self.cli()
        self.assertEqual(r.returncode, 1)
        self.assertIn("not presented by its subject", r.stderr)

    def test_check_nodes_still_reads_only_the_entries(self):
        # nodes/commitments/ sits beside the entries; check_nodes globs nodes/*.json only.
        nodes = self.tmp / "nodes"
        for f in (ROOT / "tests" / "nodes" / "good").glob("*.json"):
            shutil.copy(f, nodes / f.name)
        r = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "check_nodes.py"), str(nodes)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


class CountRuleTest(unittest.TestCase):
    """The count rule on its own: names and values."""

    def test_count_names_are_refused(self):
        for key in ("record_count", "count", "totalDeals", "nodes-total", "n_answers", "answer.tally", "num_records",
                    "size", "dealCount"):
            with self.subTest(key=key):
                self.assertTrue(cc.count_errors({"ask": {key: "x"}}), key)

    def test_count_values_are_refused(self):
        for value in (3, 0, 2.5, "12", ["7"]):
            with self.subTest(value=value):
                self.assertTrue(cc.count_errors({"answers": {"subjects": value}}))

    def test_schema_names_and_values_are_not_counts(self):
        doc = {"answers_within": "PT24H", "human_gated": False, "answers": {"subjects": ["record"]},
               "lcap": {"predicate_classes": ["performed:counterparty_half"], "policy_uri": "policies/x.json"},
               "account": "a", "country": "c", "discount": "d"}
        self.assertEqual(cc.count_errors(doc), [])

    def test_the_opaque_staple_and_checkpoint_bytes_are_not_scanned(self):
        doc = {"history_card": {"bytes": "123", "sha256": "ab"}, "checkpoint": {"bytes": "456"}}
        self.assertEqual(cc.count_errors(doc), [])
        self.assertTrue(cc.count_errors({"history_card": {"sha256": "123"}}))

    def test_canonical_form_is_jcs_for_this_schema(self):
        self.assertEqual(cc.canonical({"b": [1, True, None], "a": "é\n"}), '{"a":"é\\n","b":[1,true,null]}'.encode())
        with self.assertRaises(ValueError):
            cc.canonical({"a": 1.5})


if __name__ == "__main__":
    unittest.main()
