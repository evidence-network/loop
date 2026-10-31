"""steps/11-report: a page from 08's manifest and 10's Results, every number with a denominator, its digest sealed.

The base run is a copy of population/rehearsal/step10/run with step 10 run over it. The StandIn tests give 11 an
empty run, so it reports on the committed stand-in. Sealing and verifying need the pinned capsulectl and
capsulectl-book (steps/11-report.setup.sh); without them those tests SKIP, saying so, and LOOP_REQUIRE_TOOLS=1 makes
that an error. Each planted failure has a mutant check: with that check switched off, the plant goes through.
"""
import hashlib
import importlib.machinery
import importlib.util
import json
import os
import re
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
FIXTURES = ROOT / "tests" / "fixtures" / "step11"

sys.path.insert(0, str(STEPS))
sys.path.insert(0, str(ROOT / "status"))
sys.path.insert(0, str(ROOT / "scripts"))
import ecosystem_gate as gate  # noqa: E402
import finding  # noqa: E402

_loader = importlib.machinery.SourceFileLoader("step11_report", str(STEPS / "11-report"))
_spec = importlib.util.spec_from_loader("step11_report", _loader)
step = importlib.util.module_from_spec(_spec)
_loader.exec_module(step)

HAS_TOOLS = all(p.is_file() for p in step.judge10.tools())
NO_TOOLS = "SKIPPED LOUDLY: capsulectl and capsulectl-book are not installed; run steps/11-report.setup.sh"
if not HAS_TOOLS:
    if os.environ.get("LOOP_REQUIRE_TOOLS") == "1":
        raise RuntimeError(f"LOOP_REQUIRE_TOOLS=1: {NO_TOOLS}")
    print(f"\ntest_step11_report: {NO_TOOLS}", file=sys.stderr)


def load(path):
    return json.loads(Path(path).read_text())


def dump(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="test-step11-")
        self.run = Path(self.tmp.name) / "run"
        self.page = Path(self.tmp.name) / "report.html"  # the local copy; the finding commits only its sha256
        shutil.copytree(BASE, self.run)

    def tearDown(self):
        self.tmp.cleanup()

    def judge(self, population=POPULATION):
        step.judge10.main(["--population", str(population), "--out", str(self.run / "10-judge")])

    def report(self, population=POPULATION):
        out = self.run / "11-report"
        step.main(["--population", str(population), "--out", str(out), "--page", str(self.page)])
        f = load(out / "finding.json")
        self.assertEqual(finding.check_record(out, f), [], f["detail"])
        return f

    def statement(self):
        return step.page_statement(self.page)

    def summaries(self):
        return {c["id"]: c["presentation"]["summary"] for c in self.statement()["claims"]}

    def add_record(self, base, path, *objs):
        """Lines added to a step's primary record file, 08's manifest given its new digest and 10 run again, so 10's
        Results cite the new bytes. The stand-in's record definition (binding.json `records`) names the files that count."""
        f = self.run / base / path
        with f.open("a") as out:
            out.writelines(json.dumps(o, sort_keys=True) + "\n" for o in objs)
        v = self.run / "08-verify"
        m = load(v / "verified.json")
        next(x for e in m["steps"] if e["dir"] == base for x in e["files"] if x["path"] == path)["sha256"] = finding.sha256(f)
        dump(v / "verified.json", m)
        record = load(v / "finding.json")
        record["proof"] = [{"path": "verified.json", "sha256": finding.sha256(v / "verified.json")}]
        dump(v / "finding.json", record)
        self.judge()

    def change_record_digest(self, reseal):
        """Change one record's sha256 in 08's manifest, after 10 sealed its Results; optionally reseal 08's proof."""
        v = self.run / "08-verify"
        m = load(v / "verified.json")
        entry = next(e for e in m["steps"] if e["dir"] == "06-settle")
        next(x for x in entry["files"] if x["path"] == "ledger.jsonl")["sha256"] = "0" * 64
        dump(v / "verified.json", m)
        if reseal:
            f = load(v / "finding.json")
            f["proof"] = [{"path": "verified.json", "sha256": finding.sha256(v / "verified.json")}]
            dump(v / "finding.json", f)


class InputsTest(Base):
    """What 11 refuses before it renders; none of this needs capsulectl."""

    def test_a_run_with_08_but_no_10_is_not_checked(self):
        f = self.report()
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertIn("holds no 10-judge/", f["not_checked"][0]["why"])
        self.assertFalse(self.page.exists())

    def test_a_live_run_with_no_inputs_is_not_checked_and_never_uses_a_stand_in(self):
        shutil.rmtree(self.run / "08-verify")
        population = ROOT / "population" / "live"
        with mock.patch.object(step.judge10, "main") as ran_10:
            f = self.report(population)
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertEqual(f["population"], "live")
        self.assertIn("never fetches records through a peer", f["not_checked"][0]["why"])
        ran_10.assert_not_called()

    def plant_10(self, result, **fields):
        d = self.run / "10-judge"
        d.mkdir()
        dump(d / "finding.json", {"step": "10", "name": "judge", "result": result, "population": "rehearsal",
                                  "asserts": "a stand-in", "pinned": {}, "proof": [], "detail": "a stand-in",
                                  "not_checked": [], "started": "2026-10-02T09:00:00Z",
                                  "finished": "2026-10-02T09:01:00Z", **fields})

    def test_a_not_checked_10_is_not_checked_and_renders_nothing(self):
        self.plant_10("NOT_CHECKED", not_checked=[{"what": "the pack", "why": "all in the open month", "issue": "o/r#1"}])
        f = self.report()
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertIn("all in the open month", f["not_checked"][0]["why"])
        self.assertFalse(self.page.exists())

    def test_a_failed_10_is_refused(self):
        self.plant_10("FAIL")
        f = self.report()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("refused, nothing rendered: step 10's finding is FAIL", f["detail"])
        self.assertFalse(self.page.exists())

    def test_a_10_finding_that_does_not_hold_is_refused(self):
        self.plant_10("PASS", proof=[{"path": "gone.json", "sha256": "0" * 64}])
        f = self.report()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("step 10's finding does not hold", f["detail"])

    def test_a_passing_step_11_does_not_publish_a_countersigned_number(self):
        # Nothing is countersigned, so step 11's PASS must leave the number under that label at its placeholder.
        import status_page
        run = Path(self.tmp.name) / "findings" / "2026-10-30-rehearsal"
        d = run / "11-report"
        d.mkdir(parents=True)
        (d / "report.html").write_text("a stand-in\n")
        finding.write(d, result="PASS", population="rehearsal", asserts="a stand-in", pinned=[], proof=["report.html"],
                      detail="a stand-in", started="2026-10-30T00:00:00Z")
        dump(run / "run.json", {"stage": "1", "population": "rehearsal", "started": "2026-10-30T00:00:00Z"})
        status_page.build(run.parent, ROOT / "versions.lock", Path(self.tmp.name) / "site")
        page = (Path(self.tmp.name) / "site" / "index.html").read_text()
        row = page[page.index("Minutes to the first countersigned report"):]
        row = row[:row.index("</tr>")]
        self.assertEqual(re.findall(r'<td class="placeholder"[^>]*>([^<]*)</td>', row),
                         ["not measured yet (M3)", "not measured yet (M3)"])
        self.assertNotIn('class="measured"', row)

    def test_a_score_like_word_is_found_and_a_longer_word_is_not(self):
        self.assertEqual(step.score_like({"a": {"trust_score": 1}}), ["/a/trust_score"])
        self.assertEqual(step.score_like({"rank": 1, "b": [{"rating": 2}]}), ["/rank", "/b/0/rating"])
        self.assertEqual(len(step.score_like({"a": ["their rank is 2"]})), 1)
        self.assertEqual(step.score_like({"a": "generating a frank corroborating note"}), [])

    def test_a_number_needs_its_denominator(self):
        self.assertIsNone(step.bad_number("term-2: met in 1 of 1, from step 08 and 05-deal."))
        self.assertEqual(step.bad_number("3 paid exchanges"), "3")
        self.assertEqual(step.bad_number("5 of 3 records"), "5 of 3")


@unittest.skipUnless(HAS_TOOLS, NO_TOOLS)
class ReportTest(Base):
    @classmethod
    def setUpClass(cls):
        # 10's run is shared: each test copies its bundles in, since sealing them takes seconds per test.
        cls.judged = tempfile.TemporaryDirectory(prefix="test-step11-judged-")
        run = Path(cls.judged.name) / "run"
        shutil.copytree(BASE, run)
        step.judge10.main(["--population", str(POPULATION), "--out", str(run / "10-judge")])
        cls.judged_dir = run / "10-judge"

    @classmethod
    def tearDownClass(cls):
        cls.judged.cleanup()

    def setUp(self):
        super().setUp()
        shutil.copytree(self.judged_dir, self.run / "10-judge")

    def test_passes_offline_with_a_figure_for_each_part_of_the_page(self):
        def no_network(*a, **k):
            raise OSError("network disabled by the test")
        with mock.patch.object(socket, "socket", side_effect=no_network), \
                mock.patch.object(socket, "create_connection", side_effect=no_network), \
                mock.patch.object(socket, "getaddrinfo", side_effect=no_network):
            f = self.report()
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertEqual(f["not_checked"], [])
        self.assertEqual(sorted(f["pinned"]), ["capsulectl", "capsulectl-book"])
        self.assertEqual([i for _, i in finding.gaps(f["detail"])],
                         [step.PACK_ISSUE, step.VIEWER_ISSUE, step.PROVIDER_DEAL_ISSUE])
        self.assertNotIn("stand-in", f["detail"])
        s = self.summaries()
        self.assertIn("Deals judged: 1 of 1 deal records", s["count:deal"])
        self.assertIn("Settlements judged: 1 of 1", s["count:settlement"])
        self.assertIn("Asks judged: 6 of 6", s["count:ask"])
        self.assertIn("Disputes counted as found: 1 of 1", s["count:dispute"])
        self.assertIn("come_through.served_as_agreed (Served as agreed): met in 1 of 1",
                      s["term:come_through.served_as_agreed"])
        self.assertIn("not evaluable in 1 of 1", s["term:come_through.served_named_model"])
        self.assertIn("provider states", s["term:come_through.served_named_model"])
        self.assertIn("The roll-up cites 4 of 4 finer Results", s["decisions:rollup"])
        self.assertIn("Paid exchanges, each counted from the payer's sealed deal: 1 of 4 exchanges", s["exchanges:deals"])

    def test_each_figure_cites_its_records_by_digest(self):
        self.report()
        b = step.page_bundle(self.page)
        statement = b["disclosures"][b["root"]]["agent_input"]["statement"]
        deal = next(c for c in statement["claims"] if c["id"] == "count:deal")
        cited = [b["disclosures"][e["digest"]]["agent_input"] for e in deal["evidence"]]
        manifest = next(e for e in load(self.run / "08-verify" / "verified.json")["steps"] if e["dir"] == "05-deal")
        record = next(x for x in manifest["files"] if x["path"] == "deal.json")
        self.assertEqual({(c["path"], c["sha256"]) for c in cited}, {(record["path"], record["sha256"])})
        rollup = next(c for c in statement["claims"] if c["id"] == "decisions:rollup")
        payload = b["disclosures"][rollup["evidence"][0]["digest"]]["agent_input"]
        self.assertEqual(payload["sha256"], finding.sha256(self.run / "10-judge" / "bundles" / "rollup.bundle.json"))

    def test_the_seal_is_checkable_from_its_bundle(self):
        self.report()
        out = self.run / "11-report"
        seal = load(out / "seal.bundle.json")
        record = seal["disclosures"][seal["root"]]["agent_input"]
        self.assertEqual(record["record_type"], "report_page_seal")
        self.assertEqual(record["statement"]["page_digest"], "sha256:" + finding.sha256(self.page))
        self.assertEqual(load(out / "check.json")["check"]["page"], "match")

    def test_the_finding_commits_report_json_and_the_pages_sha256_not_the_page(self):
        f = self.report()
        out = self.run / "11-report"
        self.assertFalse((out / "report.html").exists())
        self.assertNotIn("report.html", [p["path"] for p in f["proof"]])
        record = load(out / "report.json")
        self.assertEqual(record["report_html_sha256"], finding.sha256(self.page))
        self.assertEqual(record["report_html_bytes"], self.page.stat().st_size)
        self.assertRegex(record["built_by"]["capsulectl"], r"^capsulectl v\S+ \(commit [0-9a-f]{40}\)$")
        self.assertRegex(record["built_by"]["capsulectl-book"], r"^capsulectl-book v\S+ \(commit [0-9a-f]{40}\)$")
        self.assertIn("capsulectl-book", record["built_by"]["viewer"])

    def test_the_page_rebuilds_to_the_same_bytes_twice_and_those_are_the_sealed_bytes(self):
        self.report()
        out = self.run / "11-report"
        built = {}
        real = step.Reporter.rebuild

        def keep(book, bundle, page):
            real(book, bundle, page)
            built[len(built)] = Path(page).read_bytes()
        with mock.patch.object(step.Reporter, "rebuild", keep):
            self.report()
        self.assertEqual(len(built), 2)
        self.assertEqual(built[0], built[1])
        sealed = load(out / "seal.bundle.json")
        sealed = sealed["disclosures"][sealed["root"]]["agent_input"]["statement"]["page_digest"]
        self.assertEqual(sealed, "sha256:" + hashlib.sha256(built[0]).hexdigest())

    def rebuild_that(self, change):
        real = step.Reporter.rebuild
        count = []

        def plant(book, bundle, page):
            real(book, bundle, page)
            count.append(page)
            change(len(count), Path(page))
        return mock.patch.object(step.Reporter, "rebuild", plant)

    def test_planted_two_rebuilds_that_differ_fail(self):
        def second_differs(n, page):
            if n == 2:
                page.write_bytes(page.read_bytes() + b"<!-- different -->")
        with self.rebuild_that(second_differs):
            f = self.report()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("two rebuilds of the page from result.bundle.json differ", f["detail"])

    def test_planted_rebuilds_that_are_not_the_sealed_bytes_fail(self):
        with self.rebuild_that(lambda n, page: page.write_bytes(page.read_bytes() + b"<!-- not sealed -->")):
            f = self.report()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("the rebuilt page is sha256:", f["detail"])
        self.assertNotIn("two rebuilds", f["detail"])

    def test_mutant_without_the_rebuild_check_the_unsealed_rebuild_passes(self):
        def second_differs(n, page):
            page.write_bytes(page.read_bytes() + b"<!-- different -->" * n)
        with self.rebuild_that(second_differs), mock.patch.object(step, "check_rebuilds", return_value=[]):
            self.assertEqual(self.report()["result"], "PASS")

    def test_a_free_exchange_stays_in_the_denominator_and_is_no_gap(self):
        # The stand-in settles four exchanges and seals one deal: the other three are free, and no gap.
        self.assertEqual(self.report()["result"], "PASS")
        text = self.summaries()["exchanges:deals"]
        self.assertIn("1 of 4 exchanges", text)
        self.assertIn("no gap", text)
        for word in ("missing", "absent", "lacks"):
            self.assertNotIn(word, json.dumps(self.statement()))

    def test_only_the_payers_deal_is_needed_and_no_provider_deal_is_expected(self):
        # The stand-in holds one deal.json and no provider-side deal: that is the whole record, not a gap.
        names = [x["path"] for e in load(self.run / "08-verify" / "verified.json")["steps"] for x in e["files"]
                 if Path(x["path"]).name == "deal.json"]
        self.assertEqual(names, ["deal.json"])
        f = self.report()
        self.assertEqual(f["result"], "PASS", f["detail"])
        statement = json.dumps(self.statement())
        for word in ("provider's deal", "provider-side", "both sides", "both copies", "missing"):
            self.assertNotIn(word, statement)
        self.assertIn(("a check that both sides' deals agree: only the payer seals a deal for now",
                       step.PROVIDER_DEAL_ISSUE), finding.gaps(f["detail"]))

    def test_the_count_comes_from_contents_not_names(self):
        self.add_record("06-settle", "ledger.jsonl", {"note": "named like a deal, and not one", "deal": "x"},
                        {"exchange_id": "fixture-ex-2"})
        self.assertEqual(self.report()["result"], "PASS")
        self.assertIn("sealed deal: 1 of 5 exchanges", self.summaries()["exchanges:deals"])

    def test_a_settlement_line_that_names_a_deal_seals_none(self):
        self.add_record("06-settle", "ledger.jsonl", {"deal_id": "deal-random-2", "exchange_id": "fixture-ex-2"})
        self.assertEqual(self.report()["result"], "PASS")
        self.assertNotIn("deal-random-2", json.dumps(self.statement()))
        self.assertIn("sealed deal: 1 of 5 exchanges", self.summaries()["exchanges:deals"])

    def test_live_05_halves_of_one_exchange_are_one_deal(self):
        # Each node holds the other's half of one exchange: paired as step 05 pairs them (one requested and one served
        # half of one client_nonce on two nodes), the two are one deal citing both halves (Sherlock2, loop#250). A
        # settlement leg is no deal record, and a half no other node pairs is named, not counted.
        live = ROOT / "findings" / "2026-10-31-live-8" / "05-deal"
        a, b = (json.loads((live / n / "held-1.json").read_text()) for n in ("stevenmih-1", "stevenmih-2"))
        lone = json.loads((live / "stevenmih-1" / "held-2.json").read_text())
        leg = {**a, "action_id": "mesh/settlement/terms"}
        kind = {"kind": "deal", "records": ["deal.json", "*/held-*[0-9].json"], "terms": [],
                "only": {"*/held-*[0-9].json": {"action_id_prefix": "mesh-poc/capsule-emit-mesh-host-served/",
                                                "path": "[^/]+/held-[0-9]+\\.json", "why": "w"}}}
        names = {"lab-a/held-1.json": a, "lab-b/held-1.json": b, "lab-a/held-2.json": lone, "lab-a/held-31.json": leg}
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp)
            for name, doc in names.items():
                (src / "05-deal" / name).parent.mkdir(parents=True, exist_ok=True)
                (src / "05-deal" / name).write_text(json.dumps(doc))
            manifest = {"steps": [{"dir": "05-deal", "step": "05", "status": "reproduced",
                                   "files": [{"path": n} for n in names]}]}
            cited = {f"05-deal/{n}": "s" for n in names if n != "lab-a/held-31.json"}
            found = step.count_exchanges(src, {}, {"kinds": {"05": kind}})
            figures = step.render_figures(manifest, {}, None, cited, {"kinds": {"05": kind}}, found, src)
            text = next(f["text"] for f in figures if f["id"] == "count:deal")
            self.assertIn("3 of 3 deal records step 08 lists", text)  # the settlement leg is no deal record
            self.assertIn("2 of 3 live halves pair into deals, one per exchange held on both nodes, each citing both halves "
                          "(1 of 3 halves pair with no other node's half)", text)
            self.assertEqual(found, {"deals": {}, "exchanges": {}})  # nonce pairing never mixes with 06's exchange ids
            (src / "05-deal" / "lab-a" / "held-2.json").unlink()
            with self.assertRaises(step.Refused):
                step.render_figures(manifest, {}, None, cited, {"kinds": {"05": kind}}, found, src)

    def test_a_nodes_own_chain_is_cited_but_never_counted(self):
        # loop#157: step 10 cites each node's own chain (binding.json `inputs`) for its evaluator; step 11 reads no
        # deal or exchange from it.
        pack = step.judge10.load_pack()
        cited = {"05-deal/lab-a/held-1.json": "a", "05-deal/lab-a/ledger/capsules.jsonl": "b", "06-settle/ledger.jsonl": "c"}
        refs = [r for r, _, _ in step.cited_records(Path("/nowhere"), cited, pack)]
        self.assertEqual(refs, ["05-deal/lab-a/held-1.json", "06-settle/ledger.jsonl"])

    def test_one_exchange_named_twice_counts_once(self):
        line = {"exchange_id": "fixture-ex-2"}
        self.add_record("06-settle", "ledger.jsonl", line, line)
        self.assertEqual(self.report()["result"], "PASS")
        self.assertIn("sealed deal: 1 of 5 exchanges", self.summaries()["exchanges:deals"])

    def test_planted_more_deals_than_exchanges_fails_the_denominator_check(self):
        with self.with_figure(text="5 of 4 exchanges"):
            f = self.report()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("'5 of 4' has no denominator", f["detail"])

    def uncited_deal(self):
        """A deal-like file and an exchange in the step dirs, which no Result cites and 08 does not list."""
        dump(self.run / "05-deal" / "extra-deal.json", {"deal_id": "uncited-deal"})
        dump(self.run / "06-settle" / "extra-exchange.json", {"exchange": "uncited-exchange"})

    def test_planted_an_uncited_deal_like_file_does_not_change_the_count(self):
        self.uncited_deal()
        self.assertEqual(self.report()["result"], "PASS")
        self.assertIn("sealed deal: 1 of 4 exchanges", self.summaries()["exchanges:deals"])
        self.assertNotIn("uncited", json.dumps(self.statement()))

    def test_mutant_counting_the_step_dirs_the_uncited_file_changes_the_count(self):
        self.uncited_deal()
        real = step.cited_records

        def every_file(source, cited, pack):
            listed = real(source, cited, pack)
            seen = {path for _, path, _ in listed}
            extra = [p for d in ("05-deal", "06-settle") for p in (source / d).rglob("*.json") if p not in seen]
            return listed + [(str(p.relative_to(source)), p, finding.sha256(p)) for p in extra]
        cited = {f"{e['dir']}/{x['path']}": x["sha256"] for e in load(self.run / "08-verify" / "verified.json")["steps"]
                 for x in e["files"]}
        pack = step.judge10.load_pack()
        counted = lambda: step.count_exchanges(self.run, cited, pack)  # noqa: E731
        found = counted()
        self.assertEqual((len(found["deals"]), len(found["exchanges"])), (1, 4))
        self.assertNotIn("uncited-deal", found["deals"])
        with mock.patch.object(step, "cited_records", side_effect=every_file):
            found = counted()
        self.assertIn("uncited-deal", found["deals"])
        self.assertIn("uncited-exchange", found["exchanges"])

    def change_cited_deal(self):
        dump(self.run / "05-deal" / "deal.json", {"deal_id": "fixture-deal-1", "provider": "mallory"})

    def test_planted_a_cited_deal_changed_after_10_is_refused(self):
        self.change_cited_deal()
        f = self.report()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("refused, nothing rendered: 05-deal/deal.json does not match the sha256", f["detail"])
        self.assertFalse(self.page.exists())

    def test_mutant_without_the_digest_check_the_changed_deal_passes(self):
        self.change_cited_deal()
        with mock.patch.object(step, "record_holds", return_value=True):
            self.assertEqual(self.report()["result"], "PASS")

    def test_a_step_outside_the_window_stays_in_the_denominator(self):
        d = self.run / "07-ask" / "finding.json"
        dump(d, {**load(d), "finished": "2026-10-01T00:00:00Z"})
        v = self.run / "08-verify"
        m = load(v / "verified.json")
        next(e for e in m["steps"] if e["dir"] == "07-ask")["files"][0]["sha256"] = finding.sha256(d)
        dump(v / "verified.json", m)
        f = load(v / "finding.json")
        f["proof"] = [{"path": "verified.json", "sha256": finding.sha256(v / "verified.json")}]
        dump(v / "finding.json", f)
        self.judge()
        self.assertEqual(self.report()["result"], "PASS")
        self.assertIn("Asks judged: 0 of 6 ask records", self.summaries()["count:ask"])
        self.assertIn("from 0 of 1 ask steps", self.summaries()["count:ask"])

    def test_a_10_bundle_altered_after_sealing_is_refused(self):
        j = self.run / "10-judge"
        p = j / "bundles" / "06-settle.bundle.json"
        b = load(p)
        b["disclosures"][b["root"]]["agent_input"]["statement"]["claims"][0]["verdict"] = "not_met"
        dump(p, b)
        f = self.report()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("step 10's finding does not hold", f["detail"])
        # With 10's proof digest resealed too, the bundle's own verdict refuses it.
        record = load(j / "finding.json")
        for x in record["proof"]:
            x["sha256"] = finding.sha256(j / x["path"])
        dump(j / "finding.json", record)
        f = self.report()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("06-settle.bundle.json does not verify", f["detail"])
        self.assertFalse(self.page.exists())

    # --- Planted failures, each with its mutant ---------------------------------------------------
    def page_changed_after_sealing(self):
        real = step.Reporter.seal_page

        def tamper(book, bundle, page):
            built = real(book, bundle, page)
            data = Path(page).read_bytes()
            Path(page).write_bytes(data.replace(b"</title>", b" </title>", 1))
            return built
        return mock.patch.object(step.Reporter, "seal_page", tamper)

    def test_planted_a_page_whose_bytes_differ_from_the_sealed_digest_fails(self):
        with self.page_changed_after_sealing():
            f = self.report()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("do not match the sealed digest: book report check says mismatch", f["detail"])
        self.assertIn("the disclosed seal record holds", f["detail"])

    def test_mutant_without_the_seal_check_the_changed_page_passes(self):
        with self.page_changed_after_sealing(), mock.patch.object(step, "check_seal", return_value=[]):
            self.assertEqual(self.report()["result"], "PASS")

    def test_planted_a_manifest_with_one_sha256_changed_is_refused(self):
        self.change_record_digest(reseal=True)
        f = self.report()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("refused, nothing rendered: 06-settle/ledger.jsonl is cited with sha256", f["detail"])
        self.assertFalse(self.page.exists())

    def test_planted_a_manifest_changed_without_its_proof_is_refused(self):
        self.change_record_digest(reseal=False)
        f = self.report()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("does not match its sha256", f["detail"])
        self.assertFalse(self.page.exists())

    def test_mutant_without_the_records_check_the_changed_manifest_passes(self):
        self.change_record_digest(reseal=True)
        with mock.patch.object(step, "check_records", return_value=[]):
            self.assertEqual(self.report()["result"], "PASS")

    def with_figure(self, **fields):
        real = step.render_figures

        def plant(*a, **k):
            figures = real(*a, **k)
            figures.append({**step.figure("plant:one", "outcomes", "A planted figure.", "met", []), **fields})
            return figures
        return mock.patch.object(step, "render_figures", side_effect=plant)

    def test_planted_a_number_with_no_denominator_fails(self):
        with self.with_figure(text="3 paid exchanges"):
            f = self.report()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("plant:one: '3' has no denominator", f["detail"])

    def test_mutant_without_the_denominator_check_the_bare_number_passes(self):
        with self.with_figure(text="3 paid exchanges"), mock.patch.object(step, "check_denominators", return_value=[]):
            self.assertEqual(self.report()["result"], "PASS")

    def test_planted_a_score_field_fails(self):
        with self.with_figure(score=9):
            f = self.report()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("a score-like field: page/", f["detail"])
        self.assertIn("/score", f["detail"])

    def test_planted_a_score_word_in_a_figure_fails(self):
        with self.with_figure(text="The node's rating is high."):
            f = self.report()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("a score-like field", f["detail"])

    def test_planted_enterprise_text_in_a_figure_fails(self):
        with self.with_figure(text="Upgrade to Enterprise: 1 of 1 plans."):
            f = self.report()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("paid-product text: 'Upgrade'", f["detail"])

    def test_mutant_without_the_paid_product_check_the_text_passes(self):
        with self.with_figure(text="Upgrade to Enterprise: 1 of 1 plans."), \
                mock.patch.object(step, "check_no_paid_product", return_value=[]):
            self.assertEqual(self.report()["result"], "PASS")

    def test_prices_and_payments_are_allowed_and_longer_words_do_not_match(self):
        self.assertEqual(step.PAID_WORD.findall("pricing, a payment of 5 USDC, upgraded, a subscribed key, aEnterpriseb"), [])
        self.assertEqual(step.PAID_WORD.findall("Contact sales for Premium"), ["Contact sales", "Premium"])

    def test_planted_a_report_result_bundle_that_does_not_verify_fails(self):
        real = step.judge10.verify_bundle

        def broken(capsulectl, path, tmp):
            return {**real(capsulectl, path, tmp), "verdict": "INVALID"} if Path(path).name == "result.bundle.json" \
                else real(capsulectl, path, tmp)
        with mock.patch.object(step.judge10, "verify_bundle", broken):
            f = self.report()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("the report Result's bundle does not verify: INVALID", f["detail"])

    def test_mutant_without_the_result_bundle_check_the_broken_bundle_passes(self):
        real = step.judge10.verify_bundle

        def broken(capsulectl, path, tmp):
            return {**real(capsulectl, path, tmp), "verdict": "INVALID"} if Path(path).name == "result.bundle.json" \
                else real(capsulectl, path, tmp)
        with mock.patch.object(step.judge10, "verify_bundle", broken), \
                mock.patch.object(step, "check_result_bundle", return_value=[]):
            self.assertEqual(self.report()["result"], "PASS")

    def test_a_copy_of_a_bundle_beside_10s_bundles_is_not_a_second_result(self):
        before = self.report()
        shutil.copy(self.run / "10-judge" / "bundles" / "06-settle.bundle.json",
                    self.run / "10-judge" / "bundles" / "06-settle-copy.bundle.json")
        figures = lambda: json.dumps(load(self.run / "11-report" / "report.json")["figures"])  # noqa: E731
        first = figures()
        self.assertEqual(self.report()["result"], "PASS")
        self.assertEqual(figures(), first)

    def test_planted_a_copied_bundle_named_in_10s_proof_fails(self):
        j = self.run / "10-judge"
        shutil.copy(j / "bundles" / "06-settle.bundle.json", j / "bundles" / "06-settle-copy.bundle.json")
        record = load(j / "finding.json")
        record["proof"].append({"path": "bundles/06-settle-copy.bundle.json",
                                "sha256": finding.sha256(j / "bundles" / "06-settle-copy.bundle.json")})
        dump(j / "finding.json", record)
        f = self.report()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("is a second bundle of a Result already read", f["detail"])

    def test_a_second_run_leaves_no_stale_standin_folder(self):
        (self.run / "11-report" / "standin").mkdir(parents=True)
        (self.run / "11-report" / "standin" / "old.json").write_text("{}")
        self.assertEqual(self.report()["result"], "PASS")
        self.assertFalse((self.run / "11-report" / "standin").exists())

    def test_the_real_page_holds_no_paid_product_text(self):
        self.assertEqual(self.report()["result"], "PASS")
        self.assertEqual(step.check_no_paid_product(self.page, load(self.run / "11-report" / "report.json")), [])

    def test_mutant_without_the_score_check_the_score_field_passes(self):
        with self.with_figure(score=9), mock.patch.object(step, "check_no_score", return_value=[]):
            self.assertEqual(self.report()["result"], "PASS")


@unittest.skipUnless(HAS_TOOLS, NO_TOOLS)
class StandInTest(unittest.TestCase):
    """The gate exactly: the repository's population, an empty run."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="test-step11-standin-")
        self.run = Path(self.tmp.name) / "run"
        self.run.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def report(self):
        out = self.run / "11-report"
        step.main(["--population", str(POPULATION), "--out", str(out)])
        self.assertEqual(finding.check(self.run), [])
        return load(out / "finding.json")

    def test_rehearsal_passes_on_the_committed_stand_in_and_says_so(self):
        f = self.report()
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertIn("the committed stand-in run population/rehearsal/step10/run (dated in a closed month), not this "
                      "run's records", f["detail"])
        out = self.run / "11-report"
        self.assertEqual(load(out / "report.json")["source"], "stand-in")
        self.assertTrue((out / "standin" / "10-judge" / "bundles" / "rollup.bundle.json").is_file())
        self.assertFalse((self.run / "10-judge").exists())

    def test_a_second_run_replaces_the_first(self):
        self.assertEqual(self.report()["result"], "PASS")
        self.assertEqual(self.report()["result"], "PASS")

    def test_status_page_row_11_reads_the_pass(self):
        run = Path(self.tmp.name) / "findings" / "2026-10-30-rehearsal"
        run.mkdir(parents=True)
        step.main(["--population", str(POPULATION), "--out", str(run / "11-report")])
        dump(run / "run.json", {"stage": "1", "population": "rehearsal", "started": "2026-10-30T00:00:00Z"})
        import status_page
        site = Path(self.tmp.name) / "site"
        rs = status_page.build(run.parent, ROOT / "versions.lock", site)
        cell = next(r for r in rs if r.step.n == 11).cells["rehearsal"]
        self.assertEqual(cell.label, "PASS", cell.reason if hasattr(cell, "reason") else "")
        self.assertTrue(any(path == "report.json" for path, _ in cell.proofs))


@unittest.skipUnless(HAS_TOOLS, NO_TOOLS)
class LiveFixtureTest(unittest.TestCase):
    """Fixtures only: a live population whose one lab node has a book made the way join.sh makes it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="test-step11-live-")
        root = Path(self.tmp.name)
        self.lab = root / "lab"
        self.population = root / "live"
        self.population.mkdir()
        shutil.copy(FIXTURES / "live-population.json", self.population / "population.json")
        # Live commits only the judge's public key; its private key is the lab's own, named by LOOP_JUDGE_KEY.
        (self.population / "step10" / "keys").mkdir(parents=True)
        shutil.copy(POPULATION / "step10" / "keys" / "judge.pub.pem", self.population / "step10" / "keys")
        judge_key = root / "judge" / "judge.pem"
        judge_key.parent.mkdir()
        shutil.copy(POPULATION / "step10" / "keys" / "judge.pem", judge_key)
        judge_key.chmod(0o600)
        patch = mock.patch.dict(os.environ, {"LOOP_TEST_LAB": str(self.lab), "LOOP_JUDGE_KEY": str(judge_key)})
        patch.start()
        self.addCleanup(patch.stop)
        keys = POPULATION / "keys" / "alice"
        data = self.lab / "lab-a" / "data" / "keys"
        data.mkdir(parents=True)
        shutil.copy(keys / "node-key.pub.pem", data / "node-key.pub.pem")
        self.home = self.lab / "lab-a" / "join-book"
        self.home.mkdir(parents=True)
        self.make_book(keys)
        self.run = root / "run"
        shutil.copytree(BASE, self.run)
        v = self.run / "08-verify"
        m = load(v / "verified.json")
        m["population"] = "live"
        dump(v / "verified.json", m)
        f = load(v / "finding.json")
        f["population"] = "live"
        f["proof"] = [{"path": "verified.json", "sha256": finding.sha256(v / "verified.json")}]
        dump(v / "finding.json", f)
        step.judge10.main(["--population", str(self.population), "--out", str(self.run / "10-judge")])

    def tearDown(self):
        self.tmp.cleanup()

    def make_book(self, keys):
        capsulectl = step.judge10.tools()[0]
        env = dict(os.environ, XDG_CONFIG_HOME=str(self.home / "config"),
                   CAPSULECTL_PLUGIN_ROOTS=str(capsulectl.parent))
        (self.home / "config").mkdir()

        def cap(*args):
            r = subprocess.run([str(capsulectl), *args], env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stderr)
            return r.stdout
        (self.home / "node.seed").write_text(step.judge10.seed_hex(keys / "node-key.pem"))
        (self.home / "node.seed").chmod(0o600)
        cap("key", "generate", "--output", str(self.home / "checkpoint.seed"))
        checkpoint = json.loads(cap("key", "show-public", str(self.home / "checkpoint.seed")))["public_key"]
        cap("profile", "create", "--name", "lab-a", "--type", "jsonl", "--jsonl-path", str(self.home / "book"),
            "--log-id", "lab-a-book", "--operator", "lab-a",
            "--trusted-key", step.judge10.pub_hex(keys / "node-key.pub.pem"),
            "--signing-key-file", str(self.home / "node.seed"),
            "--checkpoint-signing-key-file", str(self.home / "checkpoint.seed"),
            "--checkpoint-trusted-key", checkpoint)
        cap("store", "init", "--profile", "lab-a")

    def report(self):
        out = self.run / "11-report"
        step.main(["--population", str(self.population), "--out", str(out)])
        f = load(out / "finding.json")
        self.assertEqual(finding.check_record(out, f), [], f["detail"])
        return f

    def test_live_passes_on_the_lab_nodes_own_records_and_labels_them(self):
        f = self.report()
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertEqual(f["population"], "live")
        self.assertIn("operator-collected from the lab's own nodes", f["detail"])
        self.assertIn("this proves the report, not how an operator gets bundles from peers", f["detail"])
        self.assertIn(step.judge10.LIVE_JUDGE, f["detail"])
        self.assertNotIn("stand-in", f["detail"])
        # The seal is in the node's own book, under its own key.
        seal = load(self.run / "11-report" / "seal.bundle.json")
        self.assertEqual(seal["extensions"]["producer-key/v1"]["public_key"],
                         step.judge10.pub_hex(POPULATION / "keys" / "alice" / "node-key.pub.pem"))
        self.assertIn("report_page_seal", seal["disclosures"][seal["root"]]["agent_input"]["record_type"])

    def preview(self):
        """A live preview 10 then 11 over the run re-dated into October, in a preview directory outside the run."""
        PreviewTest.october(self)
        out = Path(self.tmp.name) / "preview"
        step.judge10.main(["--population", str(self.population), "--preview-open-month", "--run", str(self.run),
                           "--out", str(out / "10-judge")])
        step.main(["--population", str(self.population), "--preview-open-month", "--run", str(self.run),
                   "--out", str(out / "11-report")])
        f = load(out / "11-report" / "finding.json")
        self.assertEqual(finding.check_record(out / "11-report", f), [], f["detail"])
        return f, out

    def snapshot(self, root):
        return {p.relative_to(root).as_posix(): finding.sha256(p) for p in sorted(Path(root).rglob("*")) if p.is_file()}

    def test_a_live_preview_never_opens_or_writes_a_nodes_book(self):
        before = self.snapshot(self.lab)
        real = step.Reporter.__init__

        def guarded(book, home, *a, **k):
            if self.lab in Path(home).resolve().parents or Path(home).resolve() == self.lab:
                raise AssertionError(f"a preview opened a node's book: {home}")
            real(book, home, *a, **k)
        with mock.patch.object(step.Reporter, "__init__", guarded):
            f, out = self.preview()
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertTrue(f["detail"].startswith(finding.PREVIEW + ". "), f["detail"])
        self.assertIn("throwaway scratch book, not a node's book", f["detail"])
        self.assertEqual(self.snapshot(self.lab), before)
        # Sealed under a key made for the preview, not the node's.
        seal = load(out / "11-report" / "seal.bundle.json")
        self.assertNotEqual(seal["extensions"]["producer-key/v1"]["public_key"],
                            step.judge10.pub_hex(POPULATION / "keys" / "alice" / "node-key.pub.pem"))

    def test_a_live_preview_needs_no_nodes_data_dir_or_book_dir(self):
        shutil.rmtree(self.lab)
        f, _ = self.preview()
        self.assertEqual(f["result"], "PASS", f["detail"])

    def test_a_seal_under_another_key_fails(self):
        (self.lab / "lab-a" / "data" / "keys" / "node-key.pub.pem").write_bytes(
            (POPULATION / "keys" / "bob" / "node-key.pub.pem").read_bytes())
        f = self.report()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("not under the reporting book's key", f["detail"])
        self.assertIn(step.judge10.LIVE_JUDGE, f["detail"])

    def test_a_node_with_no_readable_book_is_not_checked(self):
        shutil.rmtree(self.home)
        f = self.report()
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertIn("book_dir not set or unreadable", f["not_checked"][0]["why"])
        self.assertIn(step.judge10.LIVE_JUDGE, f["detail"])


class PreviewTest(Base):
    """--preview-open-month (evidence-network/loop#242): 11 reports over 10's open-month preview, never a baseline
    result. It reads 08's manifest and the records from --run and 10's output beside --out, and writes only --out."""

    def setUp(self):
        super().setUp()
        self.preview = Path(self.tmp.name) / "preview"

    def preview_10(self, *extra):
        step.judge10.main(["--population", str(POPULATION), "--preview-open-month", "--run", str(self.run),
                           "--out", str(self.preview / "10-judge"), *extra])

    def preview_report(self):
        out = self.preview / "11-report"
        step.main(["--population", str(POPULATION), "--preview-open-month", "--run", str(self.run),
                   "--out", str(out), "--page", str(self.page)])
        f = load(out / "finding.json")
        self.assertEqual(finding.check_record(out, f), [], f["detail"])
        return f

    def october(self):
        """The base run's four steps finished in October 2026, the month 08 ran in; 08's manifest given the new digests."""
        v = self.run / "08-verify"
        m = load(v / "verified.json")
        for i, base in enumerate(("05-deal", "06-settle", "07-ask", "09-dispute")):
            f = load(self.run / base / "finding.json")
            f["finished"] = f"2026-10-0{i + 1}T10:00:30Z"
            dump(self.run / base / "finding.json", f)
            e = next(e for e in m["steps"] if e["dir"] == base)
            next(x for x in e["files"] if x["path"] == "finding.json")["sha256"] = finding.sha256(self.run / base / "finding.json")
        dump(v / "verified.json", m)
        record = load(v / "finding.json")
        record["proof"] = [{"path": "verified.json", "sha256": finding.sha256(v / "verified.json")}]
        dump(v / "finding.json", record)

    def plant_10(self, detail, window=None):
        d = self.preview / "10-judge"
        d.mkdir(parents=True)
        if window is not None:
            dump(d / "window.json", window)
        (d / "p.txt").write_text("p\n")
        dump(d / "finding.json", {"step": "10", "name": "judge", "result": "PASS", "population": "rehearsal",
                                  "asserts": "a planted step 10", "pinned": {}, "detail": detail,
                                  "proof": [{"path": "p.txt", "sha256": finding.sha256(d / "p.txt")}],
                                  "not_checked": [], "started": "2026-10-02T09:00:00Z",
                                  "finished": "2026-10-02T09:01:00Z"})

    def test_a_preview_with_no_preview_10_is_not_checked_and_never_runs_10(self):
        with mock.patch.object(step.judge10, "main") as ran_10:
            f = self.preview_report()
        ran_10.assert_not_called()
        self.assertEqual(f["result"], "NOT_CHECKED")
        self.assertTrue(f["detail"].startswith(finding.PREVIEW + ". "), f["detail"])
        self.assertIn("holds no 10-judge/ preview output", f["not_checked"][0]["why"])
        self.assertFalse(self.page.exists())

    def test_a_preview_refuses_a_10_that_is_not_a_preview(self):
        self.plant_10("a baseline step 10")
        f = self.preview_report()
        self.assertEqual(f["result"], "FAIL")
        self.assertTrue(f["detail"].startswith(finding.PREVIEW))
        self.assertIn("step 10's finding is not an open-month preview", f["detail"])
        self.assertFalse(self.page.exists())

    def test_a_preview_refuses_a_10_judged_from_another_manifest(self):
        for window in (None, {"preview": "open-month", "manifest_sha256": "0" * 64}, {"manifest_sha256": finding.sha256(
                self.run / "08-verify" / "verified.json")}):
            shutil.rmtree(self.preview, ignore_errors=True)
            self.plant_10(finding.PREVIEW + ". a preview", window)
            f = self.preview_report()
            self.assertEqual(f["result"], "FAIL", window)
            self.assertIn("step 10's preview", f["detail"])
            self.assertTrue(f["detail"].startswith(finding.PREVIEW))

    def test_a_preview_names_its_own_assertion(self):
        self.assertNotEqual(step.PREVIEW_ASSERTS, step.ASSERTS)

    def test_the_gate_refuses_what_a_preview_report_writes(self):
        f = self.preview_report()  # NOT_CHECKED: no preview 10; the marker is on whatever 11 writes
        out = self.preview / "11-report"
        f["population"] = "live"
        dump(out / "finding.json", f)
        steps, errs = gate.read_findings(self.preview)
        self.assertTrue(any("an open-month preview, not a baseline result" in e for e in errs), errs)

    def test_a_baseline_report_refuses_a_preview_10(self):
        # A preview 10-judge copied or left in the run is not a baseline's input.
        self.plant_10(finding.PREVIEW + ". a preview")
        shutil.copytree(self.preview / "10-judge", self.run / "10-judge")
        f = self.report()
        self.assertEqual(f["result"], "FAIL")
        self.assertFalse(f["detail"].startswith("PREVIEW"))
        self.assertIn("step 10's finding is an open-month preview, not a baseline result", f["detail"])
        self.assertFalse(self.page.exists())

    def test_a_preview_is_not_written_into_findings_or_baseline(self):
        findings = Path(self.tmp.name) / "findings"
        for out in (findings / "2026-10-31-live" / "11-report", ROOT / "baseline" / "x"):
            with self.assertRaises(SystemExit) as e, mock.patch.object(sys, "stderr"), \
                    mock.patch.dict(os.environ, {"LOOP_FINDINGS": str(findings)}):
                step.main(["--population", str(POPULATION), "--preview-open-month", "--run", str(self.run),
                           "--out", str(out)])
            self.assertEqual(e.exception.code, 2, out)
        self.assertFalse(findings.exists())

    def test_a_preview_needs_a_run_and_is_not_written_into_it(self):
        for argv in (["--preview-open-month"], ["--run", str(self.run)],
                     ["--preview-open-month", "--run", str(self.run)]):
            out = self.run / "11-report" if len(argv) == 3 else self.preview / "11-report"
            with self.assertRaises(SystemExit) as e, mock.patch.object(sys, "stderr"):
                step.main(["--population", str(POPULATION), "--out", str(out), *argv])
            self.assertEqual(e.exception.code, 2)
        self.assertFalse((self.run / "11-report").exists())

    @unittest.skipUnless(HAS_TOOLS, NO_TOOLS)
    def test_a_preview_10_of_a_manifest_that_changed_since_is_refused(self):
        self.october()
        self.preview_10()
        v = self.run / "08-verify"
        (v / "verified.json").write_text((v / "verified.json").read_text() + "\n")
        record = load(v / "finding.json")
        record["proof"] = [{"path": "verified.json", "sha256": finding.sha256(v / "verified.json")}]
        dump(v / "finding.json", record)
        f = self.preview_report()
        self.assertEqual(f["result"], "FAIL")
        self.assertIn("was not judged from --run's 08 manifest", f["detail"])
        self.assertFalse(self.page.exists())

    @unittest.skipUnless(HAS_TOOLS, NO_TOOLS)
    def test_a_preview_reports_the_open_month_and_leaves_the_run_alone(self):
        self.october()
        before = sorted(p.relative_to(self.run).as_posix() for p in self.run.rglob("*"))
        self.preview_10()
        self.assertEqual(load(self.preview / "10-judge" / "finding.json")["result"], "PASS")
        f = self.preview_report()
        self.assertEqual(f["result"], "PASS", f["detail"])
        self.assertTrue(f["detail"].startswith(finding.PREVIEW + ". "), f["detail"])
        self.assertIn("open-month preview Results", f["asserts"])
        self.assertEqual(load(self.preview / "11-report" / "report.json")["preview"], "open-month")
        self.assertTrue(self.page.is_file())
        self.assertEqual(sorted(p.relative_to(self.run).as_posix() for p in self.run.rglob("*")), before)


if __name__ == "__main__":
    unittest.main()
