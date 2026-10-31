"""steps/10-judge: a deterministic pack over a window of whole closed months, its Results sealed and verified offline.

The base run is population/rehearsal/step10/run, the committed stand-in run: four stand-in step findings (05, 06,
07, 09) dated in September 2026 and step 08's manifest showing each reproduced, with 08 finished in October 2026.
Most tests copy it in as a run of their own (it has 08's manifest, so 10 judges it as that run); the StandIn tests
give 10 a run without one, so it judges the population's stand-in. Sealing and verifying need
the pinned capsulectl and capsulectl-book (steps/10-judge.setup.sh); without them those tests SKIP, saying so,
and LOOP_REQUIRE_TOOLS=1 makes that an error. Each planted failure runs on a copy of the base run, and its mutant
check shows which part of 10 catches it: with that part of 10 switched off, the plant goes through.
"""
import datetime as dt
import fnmatch
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
BASE = POPULATION / "step10" / "run"

sys.path.insert(0, str(STEPS))
sys.path.insert(0, str(ROOT / "scripts"))
import ecosystem_gate as gate  # noqa: E402
import finding  # noqa: E402

_loader = importlib.machinery.SourceFileLoader("step10_judge", str(STEPS / "10-judge"))
_spec = importlib.util.spec_from_loader("step10_judge", _loader)
step = importlib.util.module_from_spec(_spec)
_loader.exec_module(step)

TOOLS_DIR = Path(os.environ.get("LOOP_TOOLS") or Path.home() / ".cache" / "evidence-network-loop")
def _has_pack():
    try:
        step.load_pack()
        return True
    except step.NotChecked:
        return False


HAS_TOOLS = all(p.is_file() for p in step.tools()) and _has_pack()
NO_TOOLS = ("SKIPPED LOUDLY: capsulectl, capsulectl-book and the pinned inference-deals pack are not installed; "
            "run steps/10-judge.setup.sh")
if not HAS_TOOLS:
    if os.environ.get("LOOP_REQUIRE_TOOLS") == "1":
        raise RuntimeError(f"LOOP_REQUIRE_TOOLS=1: {NO_TOOLS}")
    print(f"\ntest_step10_judge: {NO_TOOLS}", file=sys.stderr)

WINDOW_STEPS = ("05-deal", "06-settle", "07-ask", "09-dispute")


def load(path):
    return json.loads(Path(path).read_text())


def dump(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")


def utc(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="test-step10-")
        self.run = Path(self.tmp.name) / "run"
        shutil.copytree(BASE, self.run)

    def tearDown(self):
        self.tmp.cleanup()

    def judge(self, population=POPULATION):
        """Run 10 in this process on the run; its finding."""
        out = self.run / "10-judge"
        step.main(["--population", str(population), "--out", str(out)])
        f = load(out / "finding.json")
        self.assertEqual(finding.check_record(out, f), [], f["detail"])
        return f

    def manifest(self):
        return load(self.run / "08-verify" / "verified.json")

    def reseal_manifest(self, m):
        """Write 08's manifest and its finding's proof digest again, as 08 would have."""
        v = self.run / "08-verify"
        dump(v / "verified.json", m)
        f = load(v / "finding.json")
        f["proof"] = [{"path": "verified.json", "sha256": finding.sha256(v / "verified.json")}]
        dump(v / "finding.json", f)

    def edit_step(self, base, **fields):
        """Change a step's finding and give 08's manifest its new digest."""
        f = load(self.run / base / "finding.json")
        f.update(fields)
        dump(self.run / base / "finding.json", f)
        m = self.manifest()
        for e in m["steps"]:
            if e["dir"] == base:
                e["files"][0]["sha256"] = finding.sha256(self.run / base / "finding.json")
        self.reseal_manifest(m)

    def window_records(self):
        """{dir/path: sha256} of every record file (binding.json `records`) 08 lists for the base run's four steps."""
        return records_of(self.manifest())

    def cited(self):
        """{dir/path: sha256} the finer bundles cite, read from their disclosed record capsules."""
        out = {}
        for path in (self.run / "10-judge" / "bundles").glob("*.bundle.json"):
            if path.name == "rollup.bundle.json":
                continue
            b = load(path)
            statement = b["disclosures"][b["root"]]["agent_input"]["statement"]
            for c in statement["claims"]:
                for e in c["evidence"]:
                    p = b["disclosures"][e["digest"]]["agent_input"]
                    out[f"{p['step']}/{p['path']}"] = p["sha256"]
        return out


class WindowTest(unittest.TestCase):
    def test_twelve_whole_closed_months_before_the_as_of_month(self):
        self.assertEqual(step.window_of(utc("2026-10-02T09:01:00Z")),
                         (utc("2025-10-01T00:00:00Z"), utc("2026-10-01T00:00:00Z")))
        self.assertEqual(step.window_of(utc("2027-01-31T23:59:59Z")),
                         (utc("2026-01-01T00:00:00Z"), utc("2027-01-01T00:00:00Z")))

    def test_never_rolling(self):
        # Any instant in one month gives the same window: it moves only when a month closes.
        first = step.window_of(utc("2026-10-01T00:00:00Z"))
        self.assertEqual(first, step.window_of(utc("2026-10-31T23:59:59Z")))
        self.assertNotEqual(first, step.window_of(utc("2026-11-01T00:00:00Z")))


@unittest.skipUnless(HAS_TOOLS, NO_TOOLS)
class ManifestTest(Base):
    """What 10 reads before it seals anything; none of this needs capsulectl."""

    def test_no_manifest_and_no_stand_in_is_not_checked(self):
        # A population with no committed stand-in run (as the live one has none), and a run without 08.
        population = Path(self.tmp.name) / "rehearsal"
        shutil.copytree(POPULATION, population)
        shutil.rmtree(population / "step10" / "run")
        shutil.rmtree(self.run / "08-verify")
        f = self.judge(population)
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertIn("08-verify/verified.json", f["not_checked"][0]["why"])

    def test_a_manifest_edited_after_08_sealed_it_fails(self):
        m = self.manifest()
        m["steps"][0]["rederived"]["result"] = "FAIL"
        dump(self.run / "08-verify" / "verified.json", m)
        f = self.judge()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("does not match its sha256", f["detail"])

    def test_a_record_changed_after_08_fails(self):
        (self.run / "05-deal" / "deal.json").write_text('{"deal": "another"}\n')
        f = self.judge()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("05-deal/deal.json does not match the sha256 08's manifest gives it", f["detail"])

    def test_a_manifest_of_another_population_fails(self):
        m = self.manifest()
        m["population"] = "live"
        self.reseal_manifest(m)
        self.assertEqual(self.judge()["result"], "FAIL")

    def test_todays_records_are_outside_the_window(self):
        # 08 ran in the month the records were made: no record is in a closed month yet.
        f = load(self.run / "08-verify" / "finding.json")
        f["finished"] = "2026-09-30T23:00:00Z"
        dump(self.run / "08-verify" / "finding.json", f)
        f = self.judge()
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertEqual(json.loads((self.run / "10-judge" / "window.json").read_text())["records"], [])
        for base in WINDOW_STEPS:
            self.assertIn(f"{base}: ", f["detail"])
        self.assertIn("outside the window", f["detail"])
        self.assertFalse((self.run / "10-judge" / "bundles").exists())


def records_of(manifest):
    """{dir/path: sha256} of the primary record files of each kind that the manifest lists."""
    kinds = json.loads((ROOT / "steps" / "10-judge.d" / "binding.json").read_text())["kinds"]
    return {f"{e['dir']}/{x['path']}": x["sha256"] for e in manifest["steps"] if e["step"] in kinds
            for x in e["files"] if any(fnmatch.fnmatchcase(x["path"], g) for g in kinds[e["step"]]["records"])}


@unittest.skipUnless(HAS_TOOLS, NO_TOOLS)
class PinnedPackTest(Base):
    """The terms, tiers and window are the pinned pack's, read from where 10-judge.setup.sh fetches it."""

    def test_terms_tiers_and_window_come_from_the_pinned_pack(self):
        pack = step.load_pack()
        repo, ref = step.pin("inference-deals")
        self.assertEqual(pack["pin"], f"{repo} {ref}")
        self.assertEqual(pack["window"]["periods"], 12)
        terms = {t["id"]: t for k in pack["kinds"].values() for t in k["terms"] if not t.get("own")}
        self.assertEqual(sorted(terms), ["come_through.paid_as_agreed", "come_through.served_as_agreed",
                                         "come_through.served_named_model"])
        self.assertTrue(all(t["must_have"] for t in terms.values()))

    def test_no_copy_of_the_pack_is_left_in_the_step(self):
        for p in (ROOT / "steps").glob("10-judge*"):
            for x in ([p] if p.is_file() else [q for q in p.rglob("*") if q.is_file()]):
                self.assertNotIn("Paid as agreed", x.read_text(), x)
                if x.suffix == ".json":
                    self.assertNotIn("served the model it named", x.read_text().lower(), x)

    def pack_dir(self):
        repo, ref = step.pin("inference-deals")
        return step.tools_dir() / "packs" / f"inference-deals-{ref}"

    def with_pack(self, edit):
        """A scratch LOOP_TOOLS holding a copy of the fetched pack that `edit(dir)` has changed."""
        tools = Path(self.tmp.name) / "tools"
        shutil.copytree(self.pack_dir(), tools / "packs" / self.pack_dir().name)
        edit(tools / "packs" / self.pack_dir().name)
        return mock.patch.dict(os.environ, {"LOOP_TOOLS": str(tools)})

    def test_a_term_the_binding_does_not_bind_is_not_checked(self):
        def add(d):
            f = d / "contracts" / "inference-deals" / "0.1.0.json"
            c = load(f)
            c["requirements"].append({**c["requirements"][0], "id": "come_through.new_term"})
            dump(f, c)
        with self.with_pack(add), self.assertRaises(step.NotChecked) as e:
            step.load_pack()
        self.assertIn("come_through.new_term", e.exception.args[1])

    def test_a_term_that_is_not_must_have_is_not_checked(self):
        def soften(d):
            f = d / "contracts" / "inference-deals" / "0.1.0.json"
            c = load(f)
            c["requirements"][0]["tier"] = "informational"
            dump(f, c)
        with self.with_pack(soften), self.assertRaises(step.NotChecked) as e:
            step.load_pack()
        self.assertIn("must_have", e.exception.args[1])

    def test_the_loops_own_claims_do_not_carry_the_packs_contract(self):
        pack = step.load_pack()
        self.judge()
        refs = {}
        for path in (self.run / "10-judge" / "bundles").glob("*.bundle.json"):
            b = load(path)
            for c in b["disclosures"][b["root"]]["agent_input"]["statement"]["claims"]:
                refs[c["requirement_ref"]] = c["contract_ref"]
        self.assertEqual(refs["come_through.paid_as_agreed"], pack["contract_ref"])
        self.assertEqual(refs["ask-answered"], pack["own_contract_ref"])
        self.assertNotEqual(pack["own_contract_ref"], pack["contract_ref"])

    def test_a_pack_not_fetched_is_not_checked_not_a_pass(self):
        with tempfile.TemporaryDirectory() as empty, mock.patch.dict(os.environ, {"LOOP_TOOLS": empty}):
            f = self.judge()
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertIn("10-judge.setup.sh", json.dumps(f["not_checked"]))


@unittest.skipUnless(HAS_TOOLS, NO_TOOLS)
class JudgeTest(Base):
    def test_passes_offline_citing_every_record_in_the_window(self):
        def no_network(*a, **k):
            raise OSError("network disabled by the test")
        with mock.patch.object(socket, "socket", side_effect=no_network), \
                mock.patch.object(socket, "create_connection", side_effect=no_network), \
                mock.patch.object(socket, "getaddrinfo", side_effect=no_network):
            f = self.judge()
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertEqual(f["not_checked"], [])
        self.assertEqual(sorted(f["pinned"]), ["capsulectl", "capsulectl-book", "inference-deals"])
        self.assertEqual(self.cited(), self.window_records())
        out = self.run / "10-judge"
        names = sorted(p.name for p in (out / "bundles").iterdir())
        self.assertEqual(names, [f"{b}.bundle.json" for b in WINDOW_STEPS] + ["rollup.bundle.json"])
        for p in (out / "verify").iterdir():
            self.assertEqual(load(p)["verdict"], "VALID", p.name)
        # The roll-up cites exactly the four finer Results.
        rollup = load(out / "bundles" / "rollup.bundle.json")
        cites = {e["digest"] for c in rollup["disclosures"][rollup["root"]]["agent_input"]["statement"]["claims"]
                 for e in c["evidence"]}
        self.assertEqual(cites, {load(out / "bundles" / f"{b}.bundle.json")["root"] for b in WINDOW_STEPS})
        self.assertEqual(finding.gaps(f["detail"])[0][1], step.GAP_ISSUE)

    def test_term_3_reads_provider_states_without_model_attestation(self):
        self.judge()
        b = load(self.run / "10-judge" / "bundles" / "05-deal.bundle.json")
        claims = {c["requirement_ref"]: c for c in b["disclosures"][b["root"]]["agent_input"]["statement"]["claims"]}
        self.assertEqual(claims["come_through.served_as_agreed"]["verdict"], "met")
        self.assertEqual(claims["come_through.served_named_model"]["verdict"], "not_evaluable")
        self.assertEqual(claims["come_through.served_named_model"]["grade"], "self-attested")
        self.assertIn("provider states", claims["come_through.served_named_model"]["presentation"]["narrative"])
        # Never a judged match: not SATISFIED, and in no met or not_met bucket.
        self.assertNotEqual(claims["come_through.served_named_model"]["sufficiency"], "SATISFIED")
        buckets = b["disclosures"][b["root"]]["agent_input"]["statement"]["aggregate"]["buckets"]
        self.assertIn("05-deal:come_through.served_named_model", buckets["not_evaluable"])

    def test_term_3_with_model_attestation_is_not_checked(self):
        d = self.run / "05-deal" / "deal.json"
        dump(d, {**load(d), "model_attestation": {"model": "stand-in"}})
        m = self.manifest()
        for e in m["steps"]:
            for x in e["files"]:
                if e["dir"] == "05-deal" and x["path"] == "deal.json":
                    x["sha256"] = finding.sha256(d)
        self.reseal_manifest(m)
        f = self.judge()
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertEqual([x["issue"] for x in f["not_checked"]], [step.GAP_ISSUE])
        self.assertIn("come_through.served_named_model", f["not_checked"][0]["what"])

    def test_a_reproduced_step_with_no_record_file_fails(self):
        m = self.manifest()
        e05 = next(e for e in m["steps"] if e["dir"] == "05-deal")
        e05["files"] = [x for x in e05["files"] if x["path"] != "deal.json"]
        self.reseal_manifest(m)
        f = self.judge()
        self.assertEqual(f["result"], "FAIL", f["detail"])
        self.assertIn("05-deal: 08 lists no deal record (deal.json, */held-*[0-9].json)", f["detail"])

    def live_05(self, settlement_leg=True):
        """The base run's 05 as live 05 keeps it (loop#225): two nodes' held halves of one exchange (copied from
        findings/2026-10-31-live-8), their citing and inclusion files, and, with `settlement_leg`, a settlement leg
        a node also holds. No deal.json. Returns 08's manifest, resealed."""
        m = self.manifest()
        e05 = next(e for e in m["steps"] if e["dir"] == "05-deal")
        d = self.run / "05-deal"
        (d / "deal.json").unlink()
        e05["files"] = [x for x in e05["files"] if x["path"] != "deal.json"]
        live = ROOT / "findings" / "2026-10-31-live-8" / "05-deal"
        files = {"lab-a/held-1.json": live / "stevenmih-1" / "held-1.json",
                 "lab-a/held-1-citing.json": live / "stevenmih-1" / "held-1-citing.json",
                 "lab-a/held-1-inclusion.json": live / "stevenmih-1" / "held-1-inclusion.json",
                 "lab-b/held-1.json": live / "stevenmih-2" / "held-1.json"}
        for name, src in files.items():
            (d / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, d / name)
        if settlement_leg:
            leg = {**load(live / "stevenmih-1" / "held-1.json"), "action_id": "mesh/settlement/terms"}
            dump(d / "lab-a" / "held-31.json", leg)
        for name in sorted(str(p.relative_to(d)) for p in d.rglob("held-*.json")):
            e05["files"].append({"path": name, "sha256": finding.sha256(d / name)})
        self.reseal_manifest(m)
        return m

    def test_a_live_05_of_settlement_legs_only_has_no_deal_record_and_says_why(self):
        m = self.live_05()
        # A 05 whose held files are all settlement legs has no deal record, and says why.
        for p in (self.run / "05-deal").rglob("held-1.json"):
            dump(p, {**load(p), "action_id": "mesh/settlement/terms"})
        for x in m["steps"]:
            if x["dir"] == "05-deal":
                for f in x["files"]:
                    if f["path"].endswith("held-1.json"):
                        f["sha256"] = finding.sha256(self.run / "05-deal" / f["path"])
        self.reseal_manifest(m)
        _, _, _, problems = step.select(self.run, m, *step.window_of(utc(load(
            self.run / "08-verify" / "finding.json")["finished"])), step.load_pack())
        self.assertTrue(any("the 3 files matching those names are not deal records (binding.json `only`)" in x
                            for x in problems), problems)

    def test_live_05s_host_served_halves_are_its_deal_records_and_settlement_legs_are_not(self):
        # Sherlock2 (loop#250): a held half is a deal record by what it is (a host-served capsule), never by its
        # file name; a settlement leg held beside it is not a deal.
        m = self.live_05()
        records, refused, _, problems = step.select(self.run, m, *step.window_of(utc(load(
            self.run / "08-verify" / "finding.json")["finished"])), step.load_pack())
        self.assertEqual(problems, [])
        self.assertEqual(sorted(r["path"] for r in records if r["dir"] == "05-deal"),
                         ["lab-a/held-1.json", "lab-b/held-1.json"])
        self.assertTrue(any("05-deal/lab-a/held-31.json: not a deal record" in x for x in refused), refused)
        kind = step.load_pack()["kinds"]["05"]
        for name in ("a/b/held-1.json", "lab-a/held-1-2.json", "lab-a/held-x1.json", "lab-a/held-1-citing.json"):
            self.assertFalse(step.is_record(kind, name, self.run / "05-deal" / "lab-a" / "held-1.json"), name)
        if HAS_TOOLS:  # sealed and verified: each half cited by digest; the live halves carry model_attestation
            f = self.judge()
            self.assertEqual(f["result"], "NOT_CHECKED", f["detail"])
            self.assertEqual({x["issue"] for x in f["not_checked"]}, {step.GAP_ISSUE})
            cited = {k for k in self.cited() if k.startswith("05-deal/")}
            self.assertEqual(cited, {"05-deal/lab-a/held-1.json", "05-deal/lab-b/held-1.json"})

    def test_a_verdict_comes_from_the_result_08_rederived(self):
        m = self.manifest()
        e06 = next(e for e in m["steps"] if e["dir"] == "06-settle")
        e06["rederived"]["result"] = "FAIL"
        self.reseal_manifest(m)
        f = self.judge()
        self.assertEqual(f["result"], "PASS", f["detail"])  # a not_met deal is a judged outcome, not a broken judge
        n06 = sum(1 for k in records_of(m) if k.startswith("06-settle/"))
        self.assertIn(f"06-settle: 1 claim(s), {n06} records cited, come_through.paid_as_agreed not_met", f["detail"])
        rollup = load(self.run / "10-judge" / "bundles" / "rollup.bundle.json")
        claims = rollup["disclosures"][rollup["root"]]["agent_input"]["statement"]["claims"]
        self.assertEqual({c["id"]: c["verdict"] for c in claims}["window:06-settle"], "not_met")

    def test_a_step_08_did_not_reproduce_is_outside_the_window(self):
        d = self.run / "12-join"
        d.mkdir()
        (d / "finding.json").write_text("{}\n")
        m = self.manifest()
        m["steps"].append({"step": "12", "name": "join", "dir": "12-join", "status": "not_reproduced",
                           "files": [{"path": "finding.json", "sha256": finding.sha256(d / "finding.json")}],
                           "recorded": {"result": "PASS", "not_checked": []}, "rederived": None, "problems": [],
                           "not_checked": {"what": "step 12", "why": "not reproduced", "issue": "evidence-network/loop#188"}})
        self.reseal_manifest(m)
        f = self.judge()
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertEqual(len(f["not_checked"]), 1)
        self.assertIn("step 12", f["not_checked"][0]["what"])
        self.assertEqual(f["not_checked"][0]["issue"], "evidence-network/loop#188")  # 08's issue for the step
        self.assertIn("12-join: 1 files, not_reproduced by 08", f["detail"])
        # The records that are in the window are still sealed, cited and VALID.
        self.assertEqual(self.cited(), {k: v for k, v in self.window_records().items()
                                        if not k.startswith("12-join/")})
        for p in (self.run / "10-judge" / "verify").iterdir():
            self.assertEqual(load(p)["verdict"], "VALID", p.name)

    def test_a_step_outside_the_window_is_refused_not_read(self):
        self.edit_step("07-ask", finished="2026-10-01T00:00:00Z")
        n07 = len(next(e for e in self.manifest()["steps"] if e["dir"] == "07-ask")["files"])  # files 08 lists, not records
        with mock.patch.object(step, "attested", wraps=step.attested) as read:
            f = self.judge()
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertIn(f"07-ask: {n07} files, finished 2026-10-01T00:00:00Z, outside the window", f["detail"])
        self.assertFalse(any(k.startswith("07-ask/") for k in self.cited()))
        self.assertFalse(any("07-ask" in str(c.args[0]) for c in read.call_args_list))
        self.assertFalse((self.run / "10-judge" / "bundles" / "07-ask.bundle.json").exists())

    def test_a_file_not_in_the_manifest_is_refused_not_read(self):
        (self.run / "06-settle" / "extra.json").write_text('{"not": "listed"}\n')
        f = self.judge()
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertIn("06-settle/extra.json: not in 08's manifest", f["detail"])
        self.assertNotIn("06-settle/extra.json", self.cited())

    # --- Planted failures, each with its mutant ---------------------------------------------------
    def omit_one(self):
        real = step.judge_records

        def omit(*a, **k):
            docs = real(*a, **k)
            for c in docs["07-ask"]["claims"]:
                c["evidence"] = [e for e in c["evidence"] if e != "07-ask/alice/asks/bob/exchange/request.json"]
            return docs
        return mock.patch.object(step, "judge_records", side_effect=omit)

    def test_planted_a_result_that_omits_one_record_fails(self):
        with self.omit_one():
            f = self.judge()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("no Result cites 07-ask/alice/asks/bob/exchange/request.json", f["detail"])

    def test_mutant_without_the_complete_check_the_omission_passes(self):
        with self.omit_one(), mock.patch.object(step, "check_complete", return_value=[]):
            self.assertEqual(self.judge()["result"], "PASS")

    def cite_twice(self):
        """05's Result cites deal.json with a wrong digest first, then the right one."""
        real = step.open_bundle

        def twice(path, verdict, key):
            root, statement, disclosed, problems = real(path, verdict, key)
            if path.name == "05-deal.bundle.json":
                fake = "ff" * 32
                disclosed = {**disclosed, fake: {"step": "05-deal", "path": "deal.json", "sha256": "0" * 64}}
                claim = statement["claims"][0]
                statement = {**statement, "claims": [{**claim, "evidence": [{"digest": fake}, *claim["evidence"]]},
                                                     *statement["claims"][1:]]}
            return root, statement, disclosed, problems
        return mock.patch.object(step, "open_bundle", side_effect=twice)

    def test_planted_a_record_cited_with_two_digests_fails(self):
        with self.cite_twice():
            f = self.judge()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("05-deal/deal.json is cited with two different sha256 digests", f["detail"])

    def test_mutant_without_the_cited_once_check_the_wrong_digest_passes(self):
        def last_wins(cited, key, sha, where):  # what the check replaced: a dict that keeps the last digest
            cited[key] = sha
            return []
        with self.cite_twice(), mock.patch.object(step, "check_cited_once", side_effect=last_wins):
            self.assertEqual(self.judge()["result"], "PASS")

    def cite_outside(self):
        # 07 finished outside the window; the planted pack cites its finding.json anyway.
        self.edit_step("07-ask", finished="2026-10-01T00:00:00Z")
        outside = {"dir": "07-ask", "step": "07", "path": "finding.json", "kind": "ask", "result": "PASS",
                   "sha256": finding.sha256(self.run / "07-ask" / "finding.json"), "finished": "2026-10-01T00:00:00Z"}
        real_judge, real_seal = step.judge_records, step.seal

        def extra(*a, **k):
            docs = real_judge(*a, **k)
            docs["06-settle"]["claims"][0]["evidence"].append("07-ask/finding.json")
            return docs

        def seal(book, records, *a, **k):
            return real_seal(book, records + [outside], *a, **k)
        return mock.patch.multiple(step, judge_records=mock.DEFAULT, seal=mock.DEFAULT), extra, seal

    def test_planted_a_result_that_cites_a_record_outside_the_window_fails(self):
        patch, extra, seal = self.cite_outside()
        with patch as m:
            m["judge_records"].side_effect, m["seal"].side_effect = extra, seal
            f = self.judge()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("a Result cites 07-ask/finding.json", f["detail"])
        self.assertIn("outside the window", f["detail"])

    def test_mutant_without_the_exact_check_the_outside_record_passes(self):
        patch, extra, seal = self.cite_outside()
        with patch as m, mock.patch.object(step, "check_exact", return_value=[]):
            m["judge_records"].side_effect, m["seal"].side_effect = extra, seal
            self.assertEqual(self.judge()["result"], "PASS")

    def stray_result(self):
        """The roll-up also cites a Result sealed in the book that no VALID bundle is rooted on."""
        real = step.Book.result

        def result(book, doc, name):
            if name == "rollup":
                stray = json.loads(json.dumps(doc["claims"][0]))
                twin = step.result_doc([{**stray, "id": "stray", "evidence": stray["evidence"]}],
                                       step.load_pack(), *step.window_of(utc("2026-10-02T09:01:00Z")))
                twin["generated_at"] = "2026-10-02T00:00:00Z"
                rid = real(book, twin, "stray")
                doc["claims"].append({**doc["claims"][0], "id": "window:stray",
                                      "evidence": [{"digest_alg": "SHA-256", "digest": rid}]})
                doc["claims"][-1]["presentation"] = {**doc["claims"][0]["presentation"],
                                                     "evidence": doc["claims"][-1]["evidence"]}
                doc["aggregate"]["coverage"]["evaluated_population"] += 1
                doc["aggregate"]["buckets"][doc["claims"][-1]["verdict"]].append("window:stray")
            return real(book, doc, name)
        return mock.patch.object(step.Book, "result", result)

    def test_planted_a_rollup_that_cites_a_result_that_does_not_verify_fails(self):
        with self.stray_result():
            f = self.judge()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("a Result that does not verify", f["detail"])
        self.assertNotIn("no Result cites", f["detail"])

    def test_planted_a_rollup_over_a_finer_result_altered_after_sealing_fails(self):
        real = step.seal

        def tamper(book, records, docs, pack, start, end, out):
            sealed = real(book, records, docs, pack, start, end, out)
            p = out / "bundles" / "09-dispute.bundle.json"
            b = load(p)
            b["disclosures"][b["root"]]["agent_input"]["statement"]["claims"][0]["verdict"] = "not_met"
            dump(p, b)
            return sealed
        with mock.patch.object(step, "seal", side_effect=tamper):
            f = self.judge()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("09-dispute.bundle.json does not verify", f["detail"])
        self.assertIn("the roll-up cites", f["detail"])
        self.assertIn("a Result that does not verify", f["detail"])

    def test_mutant_without_the_rollup_check_the_stray_result_passes(self):
        with self.stray_result(), mock.patch.object(step, "check_rollup", return_value=[]):
            self.assertEqual(self.judge()["result"], "PASS")

    def alter_rollup(self):
        real = step.seal

        def tamper(book, records, docs, pack, start, end, out):
            sealed = real(book, records, docs, pack, start, end, out)
            p = out / "bundles" / "rollup.bundle.json"
            b = load(p)
            b["disclosures"][b["root"]]["agent_input"]["statement"]["claims"][0]["verdict"] = "not_met"
            dump(p, b)
            return sealed
        return mock.patch.object(step, "seal", side_effect=tamper)

    def test_planted_a_result_altered_after_sealing_fails(self):
        with self.alter_rollup():
            f = self.judge()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("rollup.bundle.json does not verify: INVALID", f["detail"])
        self.assertIn("disclosure_mismatch", f["detail"])

    def test_mutant_without_the_verifier_the_altered_result_passes(self):
        with self.alter_rollup(), mock.patch.object(step, "verify_bundle", return_value={"verdict": "VALID"}):
            self.assertEqual(self.judge()["result"], "PASS")

    def test_a_bundle_under_another_key_fails(self):
        # The Results are sealed under the judge key; the judge is told the repository's key is alice's.
        real_judge = step.judge
        alice = step.pub_hex(POPULATION / "keys" / "alice" / "node-key.pub.pem")
        with mock.patch.object(step, "judge", side_effect=lambda out, records, cap, key, tmp:
                               real_judge(out, records, cap, alice, tmp)):
            f = self.judge()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("is not under the repository's judge key", f["detail"])


def lab_key(src, dst):
    """A copy of a rehearsal TEST key at dst, mode 600, standing in for the lab's own key."""
    shutil.copy(src, dst)
    os.chmod(dst, 0o600)
    return Path(dst)


class LiveJudgeKeyTest(Base):
    """Live: the private key is the lab's own (LOOP_JUDGE_KEY, default $LAB_DIR/judge/judge.pem), never in the
    tree; the tree holds only its public key, population/live/step10/keys/judge.pub.pem (evidence-network/loop#189).
    The rehearsal TEST judge key stands in for the lab's."""

    def setUp(self):
        super().setUp()
        self.population = Path(self.tmp.name) / "live"
        (self.population / "step10" / "keys").mkdir(parents=True)
        dump(self.population / "population.json", {"population": "live", "nodes": [], "rail": None})
        self.lab = Path(self.tmp.name) / "lab"
        (self.lab / "judge").mkdir(parents=True)
        self.key = lab_key(POPULATION / "step10" / "keys" / "judge.pem", self.lab / "judge" / "judge.pem")
        shutil.copy(POPULATION / "step10" / "keys" / "judge.pub.pem", self.population / "step10" / "keys")
        m = self.manifest()
        m["population"] = "live"
        self.reseal_manifest(m)
        env = mock.patch.dict(os.environ, {"LOOP_JUDGE_KEY": str(self.key)})
        env.start()
        self.addCleanup(env.stop)

    def live(self):
        return self.judge(self.population)

    def not_checked(self):
        """judge_keys on the live population: the NotChecked it raises, as (what, why, issue). It runs before
        sealing and needs neither the pinned pack nor capsulectl, so these cases run wherever the tests do."""
        with self.assertRaises(step.NotChecked) as e:
            step.judge_keys("live", self.population / "step10" / "keys")
        self.assertEqual(e.exception.args[2], step.JUDGE_KEY_ISSUE)
        return e.exception.args

    @unittest.skipUnless(HAS_TOOLS, NO_TOOLS)
    def test_live_seals_under_the_lab_key_and_says_who_holds_it(self):
        f = self.live()
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertIn(step.LIVE_JUDGE, f["detail"])
        want = step.pub_hex(self.population / "step10" / "keys" / "judge.pub.pem")
        for b in (self.run / "10-judge" / "bundles").glob("*.bundle.json"):
            self.assertEqual(load(b)["extensions"]["producer-key/v1"]["public_key"], want, b.name)

    @unittest.skipUnless(HAS_TOOLS, NO_TOOLS)
    def test_the_key_defaults_to_the_lab_dir(self):
        with mock.patch.dict(os.environ, {"LAB_DIR": str(self.lab)}):
            del os.environ["LOOP_JUDGE_KEY"]
            self.assertEqual(self.live()["result"], "PASS")

    def test_the_lab_key_signs_and_the_committed_key_is_trusted(self):
        keys = step.judge_keys("live", self.population / "step10" / "keys")
        pub = self.population / "step10" / "keys" / "judge.pub.pem"
        self.assertEqual(keys, {"judge": (self.key, pub), "checkpoint": (self.key, pub)})

    def test_no_key_named_is_not_checked(self):
        with mock.patch.dict(os.environ, {}):
            del os.environ["LOOP_JUDGE_KEY"]
            os.environ.pop("LAB_DIR", None)
            self.assertIn("neither LOOP_JUDGE_KEY nor LAB_DIR is set", self.not_checked()[1])

    def test_a_key_not_on_this_host_is_not_checked(self):
        self.key.unlink()
        self.assertIn("is not a readable file here", self.not_checked()[1])

    def test_a_key_others_can_read_is_not_checked(self):
        os.chmod(self.key, 0o644)
        self.assertIn("must be mode 600", self.not_checked()[1])

    def test_no_committed_public_key_is_not_checked(self):
        (self.population / "step10" / "keys" / "judge.pub.pem").unlink()
        self.assertIn("lists no judge public key", self.not_checked()[1])

    @unittest.skipUnless(HAS_TOOLS, NO_TOOLS)
    def test_a_judge_key_not_on_the_host_leaves_the_step_not_checked(self):
        self.key.unlink()
        f = self.live()
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertEqual([n["issue"] for n in f["not_checked"]], [step.JUDGE_KEY_ISSUE])
        self.assertIn(step.LIVE_JUDGE, f["detail"])

    @unittest.skipUnless(HAS_TOOLS, NO_TOOLS)
    def test_a_key_that_is_not_the_committed_one_fails(self):
        other = lab_key(POPULATION / "step10" / "keys" / "checkpoint.pem", Path(self.tmp.name) / "other.pem")
        os.environ["LOOP_JUDGE_KEY"] = str(other)
        f = self.live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("not authorized by the profile trusted_keys", f["detail"])

    def test_a_node_key_is_refused(self):
        kid = step.pub_hex(self.population / "step10" / "keys" / "judge.pub.pem")
        empty = Path(self.tmp.name) / "no-nodes"
        empty.mkdir()
        self.assertEqual(step.node_key_problems({"nodes": []}, kid, empty), [])
        self.assertEqual(step.node_key_problems({"nodes": [{"node_id": kid}]}, kid, empty),
                         [f"the judge key {kid[:12]} is a node's key"])
        (empty / "n.json").write_text(json.dumps({"key_id": kid}))
        self.assertEqual(len(step.node_key_problems({"nodes": []}, kid, empty)), 1)

    @unittest.skipUnless(HAS_TOOLS, NO_TOOLS)
    def test_a_node_key_as_judge_key_fails(self):
        kid = step.pub_hex(self.population / "step10" / "keys" / "judge.pub.pem")
        dump(self.population / "population.json",
             {"population": "live", "nodes": [{"name": "n", "node_id": kid}], "rail": None})
        f = self.live()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("is a node's key", f["detail"])

    def test_rehearsal_ignores_the_live_key(self):
        os.environ["LOOP_JUDGE_KEY"] = str(Path(self.tmp.name) / "nowhere.pem")
        keys = step.judge_keys("rehearsal", POPULATION / "step10" / "keys")
        self.assertEqual(keys["judge"][0], POPULATION / "step10" / "keys" / "judge.pem")


class LiveTreeTest(unittest.TestCase):
    def test_the_live_population_commits_no_private_key(self):
        live = ROOT / "population" / "live"
        for f in live.rglob("*"):
            if f.is_file():
                self.assertNotIn(b"PRIVATE KEY", f.read_bytes(), f)
        self.assertFalse((live / "step10" / "keys" / "judge.pem").exists())

    def test_the_committed_live_judge_key_is_no_nodes_key(self):
        pub = ROOT / "population" / "live" / "step10" / "keys" / "judge.pub.pem"
        if not pub.is_file():
            self.skipTest("SKIPPED LOUDLY: no live judge public key is committed yet (lab/lab.sh judge-key)")
        self.assertEqual(step.node_key_problems(load(ROOT / "population" / "live" / "population.json"),
                                                step.pub_hex(pub)), [])


@unittest.skipUnless(HAS_TOOLS, NO_TOOLS)
class StandInTest(unittest.TestCase):
    """A run without 08's manifest: 10 judges the population's committed stand-in run, and says so."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="test-step10-standin-")
        self.population = Path(self.tmp.name) / "rehearsal"
        shutil.copytree(POPULATION, self.population)
        self.run = Path(self.tmp.name) / "run"
        self.run.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def judge(self):
        out = self.run / "10-judge"
        step.main(["--population", str(self.population), "--out", str(out)])
        f = load(out / "finding.json")
        self.assertEqual(finding.check(self.run), [], f["detail"])
        return f

    def test_rehearsal_passes_on_the_committed_stand_in(self):
        f = self.judge()
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertIn("the committed stand-in run population/rehearsal/step10/run (dated in a closed month), not "
                      "this run's records", f["detail"])
        self.assertEqual(load(self.run / "10-judge" / "window.json")["source"], "stand-in")
        manifest = load(POPULATION / "step10" / "run" / "08-verify" / "verified.json")
        records = load(self.run / "10-judge" / "window.json")["records"]
        self.assertEqual(len(records), len(records_of(manifest)))
        # Counts are of deals, settlements, asks and disputes, not of the bundles and proof files beside them.
        by_step = {}
        for r in records:
            by_step[r["step"]] = by_step.get(r["step"], 0) + 1
        self.assertEqual(by_step, {"05": 1, "06": 1, "07": 6, "09": 1})
        self.assertEqual(finding.gaps(f["detail"]), [("the pinned pack's own evaluator does not run on the rehearsal stand-ins: "
                                                      "this step applies the pack's terms to the results 08 re-derived",
                                                      step.GAP_ISSUE)])

    @unittest.skipUnless((TOOLS_DIR / "05-deal" / "bin" / "loop-step05-deal").is_file(),
                         "SKIPPED LOUDLY: step 05's driver is not installed; run steps/05-deal.setup.sh")
    def test_the_stand_in_is_what_the_real_08_reproduces(self):
        """08 to 10: the committed manifest is the one the real 08 gives the committed records, not a hand-written one."""
        run = Path(self.tmp.name) / "run"  # named as the committed one: the manifest carries the run's name
        run.mkdir(exist_ok=True)
        shutil.copytree(POPULATION / "step10" / "run", run / "stand-in", dirs_exist_ok=True)
        again = run / "stand-in"
        committed = POPULATION / "step10" / "run" / "08-verify"
        shutil.rmtree(again / "08-verify")
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        r = subprocess.run([str(ROOT / "steps" / "08-verify"), "--population", str(POPULATION),
                            "--out", str(again / "08-verify")], env=env, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(load(again / "08-verify" / "finding.json")["result"], "NOT_CHECKED", r.stderr)  # steps of stage 1 not in the stand-in
        got = load(again / "08-verify" / "verified.json")
        want = load(committed / "verified.json")
        self.assertEqual({**got, "run": None}, {**want, "run": None})

    def test_the_repository_stand_in_passes(self):
        # The gate exactly: the repository's population, an empty run.
        out = self.run / "10-judge"
        step.main(["--population", str(POPULATION), "--out", str(out)])
        self.assertEqual(load(out / "finding.json")["result"], "PASS")
        self.assertEqual(finding.check(self.run), [])

    def test_planted_a_stand_in_record_changed_fails(self):
        (self.population / "step10" / "run" / "06-settle" / "ledger.jsonl").write_text('{"settlement": "other"}\n')
        f = self.judge()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("06-settle/ledger.jsonl does not match the sha256 08's manifest gives it", f["detail"])

    def test_planted_a_result_over_the_stand_in_that_omits_one_record_fails(self):
        real = step.judge_records

        def omit(*a, **k):
            docs = real(*a, **k)
            for c in docs["07-ask"]["claims"]:
                c["evidence"] = [e for e in c["evidence"] if e != "07-ask/alice/asks/bob/exchange/request.json"]
            return docs
        with mock.patch.object(step, "judge_records", side_effect=omit):
            f = self.judge()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("no Result cites 07-ask/alice/asks/bob/exchange/request.json", f["detail"])


class PreviewTest(Base):
    """--preview-open-month (evidence-network/loop#242): the open month, a look and never a baseline result. The
    baseline window above is untouched; a preview reads --run, writes only --out, and marks every detail."""

    def setUp(self):
        super().setUp()
        self.preview = Path(self.tmp.name) / "preview"

    def october(self):
        """The base run's four steps, finished in October 2026: records of the month 08 ran in."""
        for i, base in enumerate(WINDOW_STEPS):
            self.edit_step(base, finished=f"2026-10-0{i + 1}T10:00:30Z")

    def run_preview(self, *extra, population=POPULATION):
        out = self.preview / "10-judge"
        step.main(["--population", str(population), "--preview-open-month", "--run", str(self.run),
                   "--out", str(out), *extra])
        f = load(out / "finding.json")
        self.assertEqual(finding.check_record(out, f), [], f["detail"])
        return f

    def test_the_open_month_is_the_as_of_month_and_the_baseline_window_does_not_move(self):
        self.assertEqual(step.open_month_of(utc("2026-10-02T09:01:00Z")),
                         (utc("2026-10-01T00:00:00Z"), utc("2026-11-01T00:00:00Z")))
        self.assertEqual(step.open_month_of(utc("2026-12-31T23:59:59Z")),
                         (utc("2026-12-01T00:00:00Z"), utc("2027-01-01T00:00:00Z")))
        self.assertEqual(step.window_of(utc("2026-10-02T09:01:00Z")),
                         (utc("2025-10-01T00:00:00Z"), utc("2026-10-01T00:00:00Z")))

    def test_the_marker_is_the_exact_text_the_coordinator_ruled(self):
        self.assertEqual(finding.PREVIEW, "PREVIEW: open month, not a baseline result")

    def test_a_preview_finding_starts_with_the_marker_even_when_nothing_ran(self):
        with mock.patch.object(step, "load_pack", side_effect=step.NotChecked("the pinned inference-deals pack", "w")):
            f = self.run_preview()
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertTrue(f["detail"].startswith(finding.PREVIEW + ". "), f["detail"])
        self.assertIn("open calendar month", f["asserts"])

    def test_the_gate_refuses_what_a_preview_writes(self):
        # Even a preview of a live run, whose population the gate would take, is refused for what it is.
        with mock.patch.object(step, "load_pack", side_effect=step.NotChecked("the pinned inference-deals pack", "w")):
            self.run_preview()
        f = load(self.preview / "10-judge" / "finding.json")
        f["population"] = "live"
        dump(self.preview / "10-judge" / "finding.json", f)
        steps, errs = gate.read_findings(self.preview)
        self.assertTrue(any("an open-month preview, not a baseline result" in e for e in errs), errs)
        self.assertNotIn("population is", " ".join(errs))

    def test_a_preview_reads_the_run_and_writes_only_out(self):
        with mock.patch.object(step, "load_pack", side_effect=step.NotChecked("the pinned inference-deals pack", "w")):
            self.run_preview()
        self.assertFalse((self.run / "10-judge").exists())
        self.assertEqual(sorted(p.name for p in self.preview.iterdir()), ["10-judge"])

    def test_a_preview_needs_a_run_and_a_run_needs_the_flag(self):
        out = str(self.preview / "10-judge")
        for argv in (["--preview-open-month"], ["--run", str(self.run)]):
            with self.assertRaises(SystemExit) as e, mock.patch.object(sys, "stderr"):
                step.main(["--population", str(POPULATION), "--out", out, *argv])
            self.assertEqual(e.exception.code, 2)
        self.assertFalse(self.preview.exists())

    def test_a_preview_is_not_written_into_the_run_findings_or_baseline(self):
        findings = Path(self.tmp.name) / "findings"
        for out in (self.run / "10-judge", self.run, findings / "2026-10-31-live" / "10-judge", ROOT / "baseline" / "x"):
            with self.assertRaises(SystemExit) as e, mock.patch.object(sys, "stderr"), \
                    mock.patch.dict(os.environ, {"LOOP_FINDINGS": str(findings)}):
                step.main(["--population", str(POPULATION), "--preview-open-month", "--run", str(self.run),
                           "--out", str(out)])
            self.assertEqual(e.exception.code, 2, out)
        self.assertFalse((self.run / "10-judge").exists())
        self.assertFalse(findings.exists())

    def test_a_preview_never_judges_the_stand_in(self):
        shutil.rmtree(self.run / "08-verify")
        f = self.run_preview()
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertIn("08-verify/verified.json", f["not_checked"][0]["why"])
        self.assertTrue(f["detail"].startswith(finding.PREVIEW))
        self.assertNotIn("stand-in", f["detail"])

    @unittest.skipUnless(HAS_TOOLS, NO_TOOLS)
    def test_a_preview_judges_the_open_month_with_the_same_pack(self):
        self.october()
        f = self.run_preview()
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertTrue(f["detail"].startswith(finding.PREVIEW + ". "), f["detail"])
        self.assertIn("window 2026-10-01T00:00:00Z to 2026-11-01T00:00:00Z (the open month)", f["detail"])
        out = self.preview / "10-judge"
        w = load(out / "window.json")
        self.assertEqual(w["manifest_sha256"], finding.sha256(self.run / "08-verify" / "verified.json"))
        self.assertEqual((w["preview"], w["start"], w["end"], w["periods"]),
                         ("open-month", "2026-10-01T00:00:00Z", "2026-11-01T00:00:00Z", 1))
        self.assertEqual({r["dir"] for r in w["records"]}, set(WINDOW_STEPS))
        rollup = load(out / "bundles" / "rollup.bundle.json")
        xw = rollup["disclosures"][rollup["root"]]["agent_input"]["statement"]["x-window"]
        self.assertEqual((xw["preview"], xw["periods"], xw["pack"]),
                         ("open-month", 1, step.load_pack()["pack"]))
        self.assertFalse((self.run / "10-judge").exists())

    @unittest.skipUnless(HAS_TOOLS, NO_TOOLS)
    def test_the_same_run_without_the_flag_still_judges_closed_months_only(self):
        self.october()
        f = self.judge()
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertFalse(f["detail"].startswith("PREVIEW"))
        self.assertNotIn("preview", load(self.run / "10-judge" / "window.json"))
        self.assertEqual(load(self.run / "10-judge" / "window.json")["records"], [])
        self.assertEqual(f["asserts"], step.ASSERTS)

    @unittest.skipUnless(HAS_TOOLS, NO_TOOLS)
    def test_a_september_run_has_nothing_in_the_open_month(self):
        # The base run's records finished in September; 08 ran in October, so the open month holds none of them.
        f = self.run_preview()
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertTrue(f["detail"].startswith(finding.PREVIEW))
        self.assertEqual(load(self.preview / "10-judge" / "window.json")["records"], [])
        self.assertEqual(f["not_checked"][0]["issue"], step.PREVIEW_ISSUE)  # not the closed-month window's gap


if __name__ == "__main__":
    unittest.main()
