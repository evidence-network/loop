"""steps/13-ask-across: an evidence request answered across ecosystems (goose <-> mesh), judged with step 07's judge.

Needs the pinned capsulectl and capsulectl-book (steps/07-ask.setup.sh); without them these tests SKIP and say why, or, with
LOOP_REQUIRE_TOOLS=1 (CI, after the setups ran), fail to load. The fixtures (tests/fixtures/step13/, made by
make_fixtures.py) are real runs of scripts/cross_ask_session.py with rehearsal keys; each planted failure there was made by
a misbehaving asker or responder during the run, except "another request", which swaps two asks' request files in a copy of
the good run. Step 08's re-derivation of every row is tested here as well, and the CI check scripts/check_cross_run.py.
"""
import copy
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

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "step13"
sys.path.insert(0, str(ROOT / "scripts"))
import check_cross_run  # noqa: E402
import check_nodes  # noqa: E402


def load_step(base):
    loader = importlib.machinery.SourceFileLoader(f"t_{base.replace('-', '_')}", str(ROOT / "steps" / base))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


step = load_step("13-ask-across")
verify = load_step("08-verify")
finding = step.finding
CAPSULECTL = step.step07.tools()
NO_TOOL = f"capsulectl not installed at {CAPSULECTL}: run steps/07-ask.setup.sh (CI does)"


def needs_capsulectl(test):
    if CAPSULECTL.is_file():
        return test
    if os.environ.get("LOOP_REQUIRE_TOOLS") == "1":
        raise RuntimeError(f"LOOP_REQUIRE_TOOLS=1: {NO_TOOL}")
    print(f"\nSKIP {test.__name__}: {NO_TOOL}", file=sys.stderr)
    return unittest.skip(NO_TOOL)(test)


def keys_of(run):
    return {n: v["key_id"] for n, v in json.loads((Path(run) / "nodes.json").read_text()).items()}


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def copy(self, name, to=None):
        dest = self.root / (to or name)
        shutil.copytree(FIX / name, dest)
        return dest

    def decide(self, run):
        return step.decide(run, keys_of(run), CAPSULECTL, self.root / "w", finding.now())

    @staticmethod
    def row(d, asker, responder):
        (r,) = [r for r in d["rows"] if (r["asker"], r["responder"]) == (asker, responder)]
        return r


@needs_capsulectl
class RehearsalTest(Tmp):
    def test_the_good_run_passes_in_both_directions(self):
        d = self.decide(self.copy("good"))
        self.assertEqual(d["result"], "PASS", d["detail"])
        self.assertEqual({(r["asker"], r["responder"]) for r in d["rows"]}, {("goose", "mesh"), ("mesh", "goose")})
        self.assertEqual({r["result"] for r in d["rows"]}, {"PASS"})
        self.assertEqual(d["not_checked"], [])

    def test_a_signed_refusal_under_the_counterparties_policy_is_a_correct_answer(self):
        # The exchange ask comes from an asker that is not the responder's counterparty: both nodes advertise
        # disclosure.policy counterparties, so each answers it with a signed refusal, and that is a PASS cell.
        d = self.decide(self.copy("good"))
        for r in d["rows"]:
            self.assertEqual(r["result"], "PASS", r["detail"])
            self.assertIn("exchange refusal", r["detail"], r["detail"])
        run = self.copy("good", "again")
        for entry in (run / "entries").glob("*.json"):
            self.assertEqual(json.loads(entry.read_text())["disclosure"]["policy"], "counterparties")

    def test_the_late_reply_is_an_absence_and_the_ask_is_not_counted_as_answered(self):
        d = self.decide(self.copy("good"))
        self.assertTrue(all("exchange_late recorded_absence" in r["detail"] for r in d["rows"]), d["detail"])

    def test_an_answer_signed_by_a_key_other_than_the_listed_one_is_a_fail(self):
        d = self.decide(self.copy("wrong-key"))
        self.assertEqual(d["result"], "FAIL")
        r = self.row(d, "mesh", "goose")
        self.assertEqual(r["result"], "FAIL")
        self.assertIn("not the listed key", r["detail"])
        self.assertEqual(self.row(d, "goose", "mesh")["result"], "PASS")  # one bad cell does not fail the other

    def test_an_answer_for_another_request_is_a_fail(self):
        run = self.copy("good")
        a, b = (run / "mesh" / "asks" / "goose" / n / "request.json" for n in ("exchange", "history_card"))
        a_bytes, b_bytes = a.read_bytes(), b.read_bytes()
        a.write_bytes(b_bytes)
        b.write_bytes(a_bytes)
        d = self.decide(run)
        self.assertEqual(d["result"], "FAIL")
        self.assertIn("the outcome is for another request", self.row(d, "mesh", "goose")["detail"])

    def test_a_late_answer_counted_as_answered_is_a_fail(self):
        d = self.decide(self.copy("late-counted"))
        self.assertEqual(d["result"], "FAIL")
        self.assertIn("after the deadline", self.row(d, "mesh", "goose")["detail"])

    def test_an_unsigned_refusal_is_not_an_answer(self):
        # The goose node cannot record a refusal without a signature as an answer, so it records an absence; the reply
        # was delivered by the deadline, which the judge refuses. (The asker's own verifier refuses to record it, so a
        # recorded refusal the verifier then disowns is not reachable with either asker.)
        d = self.decide(self.copy("unsigned-refusal"))
        self.assertEqual(d["result"], "FAIL")
        self.assertIn("delivered by the deadline", self.row(d, "goose", "mesh")["detail"])
        self.assertEqual(self.row(d, "mesh", "goose")["result"], "PASS")

    def test_a_request_without_its_own_deadline_is_a_fail(self):
        run = self.copy("good")
        ask = run / "goose" / "asks" / "mesh" / "history_card"
        req = json.loads((ask / "request.json").read_bytes())
        req.pop("deadline")
        (ask / "request.json").write_bytes(step.step07.check_commitment.canonical(req))
        d = self.decide(run)
        self.assertEqual(d["result"], "FAIL")
        self.assertIn("deadline", self.row(d, "goose", "mesh")["detail"])

    def test_an_entry_on_the_first_consent_statement_is_skipped_not_failed(self):
        run = self.copy("good")
        key = keys_of(run)["mesh"]
        e = json.loads((run / "entries" / f"{key}.json").read_text())
        e["consent"] = check_nodes.CONSENT
        (run / "entries" / f"{key}.json").write_text(json.dumps(e))
        d = self.decide(run)
        r = self.row(d, "goose", "mesh")
        self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertIn("not opted in", r["not_checked"][0]["why"])
        self.assertEqual(self.row(d, "mesh", "goose")["result"], "PASS")  # the other node still carries the new statement
        self.assertEqual(d["result"], "NOT_CHECKED")  # one NOT_CHECKED cell never sits under a PASS

    def test_an_entry_whose_route_disagrees_with_its_commitment_is_a_fail(self):
        run = self.copy("good")
        key = keys_of(run)["goose"]
        e = json.loads((run / "entries" / f"{key}.json").read_text())
        e["ask_route"] = "mesh"  # the signed commitment names ask.nostr
        (run / "entries" / f"{key}.json").write_text(json.dumps(e))
        d = self.decide(run)
        self.assertEqual(d["result"], "FAIL")
        self.assertIn("ask_route is mesh", self.row(d, "mesh", "goose")["detail"])

    def test_the_finding_rolls_up_to_the_worst_cell(self):
        rows = lambda *results: [{"result": r} for r in results]  # noqa: E731
        self.assertEqual(step.roll_up(rows("PASS", "PASS")), "PASS")
        self.assertEqual(step.roll_up(rows("PASS", "NOT_CHECKED")), "NOT_CHECKED")
        self.assertEqual(step.roll_up(rows("NOT_CHECKED", "FAIL", "PASS")), "FAIL")
        self.assertEqual(step.roll_up([]), "NOT_CHECKED")

    def test_a_pair_emptied_of_its_asks_is_a_fail_row(self):
        run = self.copy("good")
        pair = run / "mesh" / "asks" / "goose"
        for d in [x for x in pair.iterdir() if x.is_dir()]:
            shutil.rmtree(d)
        (pair / "carrier.jsonl").write_text("")
        d = self.decide(run)
        self.assertEqual(d["result"], "FAIL")
        self.assertIn("holds no ask", self.row(d, "mesh", "goose")["detail"])

    def test_a_missing_ask_is_a_fail_when_the_population_names_the_asks(self):
        run = self.copy("good")
        shutil.rmtree(run / "mesh" / "asks" / "goose" / "exchange_late")
        d = step.decide(run, keys_of(run), CAPSULECTL, self.root / "w", finding.now(),
                        asks=["exchange", "exchange_late", "history_card"])
        self.assertIn("the pair holds no exchange_late ask", self.row(d, "mesh", "goose")["detail"])

    def test_a_responder_commitment_under_another_key_than_the_listed_one_is_a_fail(self):
        run = self.copy("good")
        keys = keys_of(run)
        keys["goose"] = "ab" * 32  # the listing names another key than the one that signed the commitment and the answers
        d = step.decide(run, keys, CAPSULECTL, self.root / "w", finding.now())
        self.assertIn("not its listed key", self.row(d, "mesh", "goose")["detail"])

    def test_a_pair_the_proof_lost_is_a_fail_row(self):
        run = self.copy("good")
        shutil.rmtree(run / "goose" / "asks")
        d = self.decide(run)
        self.assertEqual(d["result"], "FAIL")
        self.assertIn("holds no asks of this pair", self.row(d, "goose", "mesh")["detail"])


class EcosystemMapTest(unittest.TestCase):
    def card(self, community):
        return {"profile": "evidence-commitment/1", "history_cards": [{"stream": "s", "community": community}]}

    def test_the_one_map_names_each_ecosystem(self):
        self.assertEqual(step.ECOSYSTEM_OF_COMMUNITY, {"mesh-llm": "mesh", "muse": "muse", "goose": "goose"})
        self.assertEqual(step.ecosystem_of(self.card("mesh-llm")), "mesh")
        self.assertEqual(step.ecosystem_of(self.card("goose")), "goose")

    def test_a_slash_zero_card_reads_as_mesh(self):
        # evidence-commitment/0 staples one card that names no community: history_cards reads it as mesh-llm.
        self.assertEqual(step.ecosystem_of({"profile": "evidence-commitment/0", "history_card": {"bytes": "e30="}}), "mesh")

    def test_a_community_the_map_does_not_list_is_refused_not_guessed(self):
        with self.assertRaises(ValueError):
            step.ecosystem_of(self.card("other"))

    def test_the_map_covers_every_community_a_card_may_carry(self):
        self.assertEqual(set(step.ECOSYSTEM_OF_COMMUNITY), set(step.history_cards.COMMUNITIES))

    def test_the_step_hard_codes_no_carrier(self):
        entry = {"ask_route": "mesh"}
        self.assertTrue(step.route_errors(entry, {"ask": {"nostr": {}}}))
        self.assertEqual(step.route_errors(entry, {"ask": {"endpoint": "book:x"}}), [])
        self.assertTrue(step.route_errors({"ask_route": "nostr"}, {"ask": {"endpoint": "book:x"}}))
        self.assertTrue(step.route_errors({"ask_route": "a2a"}, {"ask": {}}))


class LiveTest(Tmp):
    def test_each_live_cell_is_not_checked_naming_its_carrier_gap(self):
        rows = step.live_rows(ROOT / "nodes")
        cells = {(r["asker"], r["responder"]): r for r in rows}
        self.assertEqual(set(cells), {("goose", "mesh"), ("mesh", "goose")})
        for r in rows:
            self.assertEqual(r["result"], "NOT_CHECKED")
        self.assertIn("evidence-network/loop#442", [x["issue"] for x in cells["goose", "mesh"]["not_checked"]])
        self.assertIn("evidence-network/loop#443", [x["issue"] for x in cells["mesh", "goose"]["not_checked"]])

    def test_an_old_statement_entry_is_named_as_not_opted_in(self):
        rows = step.live_rows(ROOT / "nodes")
        why = [x["why"] for r in rows for x in r["not_checked"]]
        self.assertTrue(any(w.startswith("not opted in") for w in why))


class Setup(Tmp):
    """A finding directory for step 13 on a run, and the population step 08 takes the listed keys from."""

    def make(self, name="good", population="rehearsal", asks=("exchange", "exchange_late", "history_card"), edit=None):
        run = self.copy(name) if population == "rehearsal" else None
        if run and edit:
            edit(run)
        asks = list(asks) if name == "good" else None
        pop = self.root / "pop"
        pop.mkdir()
        doc = {"population": population}
        if run:
            doc["cross"] = {"run": str(run), "asks": asks, "nodes": {n: {"key_id": k} for n, k in keys_of(run).items()}}
        (pop / "population.json").write_text(json.dumps(doc))
        findings = self.root / "findings"
        (findings / "13-ask-across").mkdir(parents=True)
        stage = ["13", "08"]
        (findings / "run.json").write_text(json.dumps({"stage": "cross", "population": population, "ecosystem": "cross",
                                                       "started": finding.now(), "steps": stage}))
        os.environ["LOOP_NODES_DIR"] = str(ROOT / "nodes")
        self.addCleanup(os.environ.pop, "LOOP_NODES_DIR", None)
        self.assertEqual(step.main(["--population", str(pop), "--out", str(findings / "13-ask-across")]), 0)
        return pop, findings

    def run08(self, pop, findings):
        self.assertEqual(verify.main(["--population", str(pop), "--out", str(findings / "08-verify")]), 0)
        return json.loads((findings / "08-verify" / "verified.json").read_text())

    def retell(self, findings, mutate):
        """What a publisher who rewrote pairs.json and then the finding's digest would leave: 08 must still catch it."""
        d = findings / "13-ask-across"
        rec = json.loads((d / "finding.json").read_text())
        pairs = json.loads((d / "pairs.json").read_text())
        mutate(pairs)
        (d / "pairs.json").write_text(json.dumps(pairs, indent=2, sort_keys=True) + "\n")
        finding.write(d, result=rec["result"], population=rec["population"], asserts=rec["asserts"],
                      pinned=list(rec["pinned"]), proof=[p["path"] for p in rec["proof"]], detail=rec["detail"],
                      not_checked=rec["not_checked"], started=rec["started"])

    @staticmethod
    def entry13(manifest):
        (e,) = [e for e in manifest["steps"] if e["step"] == "13"]
        return e


@needs_capsulectl
class Step08Test(Setup):
    def test_the_finding_is_reproduced_and_the_run_passes(self):
        pop, findings = self.make()
        self.assertEqual(json.loads((findings / "13-ask-across" / "finding.json").read_text())["result"], "PASS")
        m = self.run08(pop, findings)
        self.assertEqual(self.entry13(m)["status"], "reproduced", self.entry13(m))
        self.assertEqual(m["result"], "PASS")

    def test_an_entry_on_the_first_statement_is_reproduced_not_failed_by_08(self):
        def old(run):
            k = keys_of(run)["mesh"]
            e = json.loads((run / "entries" / f"{k}.json").read_text())
            e["consent"] = check_nodes.CONSENT
            (run / "entries" / f"{k}.json").write_text(json.dumps(e))
        pop, findings = self.make(edit=old)
        self.assertEqual(json.loads((findings / "13-ask-across" / "finding.json").read_text())["result"], "NOT_CHECKED")
        e = self.entry13(self.run08(pop, findings))
        self.assertEqual(e["status"], "reproduced", e)

    def test_swapping_two_rows_results_makes_08_fail(self):
        pop, findings = self.make("wrong-key")

        def swap(p):
            a, b = p["rows"][0], p["rows"][1]
            a["result"], b["result"] = b["result"], a["result"]
        self.retell(findings, swap)
        e = self.entry13(self.run08(pop, findings))
        self.assertEqual(e["status"], "failed")
        self.assertTrue(any("pairs.json" in p for p in e["problems"]), e["problems"])

    def test_deleting_a_direction_makes_08_fail(self):
        pop, findings = self.make()
        self.retell(findings, lambda p: p["rows"].pop())
        e = self.entry13(self.run08(pop, findings))
        self.assertEqual(e["status"], "failed")

    def test_editing_one_rows_gap_makes_08_fail(self):
        pop, findings = self.make("wrong-key")

        def edit(p):
            p["rows"][0]["detail"] = p["rows"][0]["detail"] + " (edited)"
        self.retell(findings, edit)
        e = self.entry13(self.run08(pop, findings))
        self.assertEqual(e["status"], "failed")

    def test_a_proof_file_changed_after_the_run_makes_08_fail(self):
        pop, findings = self.make()
        f = findings / "13-ask-across" / "mesh" / "asks" / "goose" / "carrier.jsonl"
        f.write_text(f.read_text() + "\n")
        e = self.entry13(self.run08(pop, findings))
        self.assertEqual(e["status"], "failed")

    def test_an_unreproducible_step_13_is_what_the_ci_check_refuses(self):
        pop, findings = self.make()
        self.run08(pop, findings)
        self.assertEqual(check_cross_run.problems(findings), [])
        pop2 = self.root / "pop2"
        pop2.mkdir()
        (pop2 / "population.json").write_text(json.dumps({"population": "rehearsal"}))  # names no cross run
        m = self.run08(pop2, findings)
        self.assertEqual(self.entry13(m)["status"], "not_reproduced")
        self.assertNotEqual(check_cross_run.problems(findings), [])

    def test_a_cross_run_whose_cell_is_not_pass_is_what_the_ci_check_refuses(self):
        pop, findings = self.make("wrong-key")
        self.run08(pop, findings)
        errs = check_cross_run.problems(findings)
        self.assertTrue(any("mesh -> goose" in e for e in errs), errs)


class LiveStep08Test(Setup):
    def test_live_13_is_reproduced_and_08_stays_not_checked_naming_the_gaps(self):
        pop, findings = self.make(population="live")
        d = json.loads((findings / "13-ask-across" / "finding.json").read_text())
        self.assertEqual(d["result"], "NOT_CHECKED")
        m = self.run08(pop, findings)
        self.assertEqual(self.entry13(m)["status"], "reproduced", self.entry13(m))
        issues = {x["issue"] for x in json.loads((findings / "08-verify" / "finding.json").read_text())["not_checked"]}
        self.assertTrue({"evidence-network/loop#442", "evidence-network/loop#443"} <= issues)

    def test_editing_a_live_gap_makes_08_fail(self):
        pop, findings = self.make(population="live")

        def edit(p):
            p["rows"][0]["not_checked"][0]["issue"] = "evidence-network/loop#443"
        self.retell(findings, edit)
        self.assertEqual(self.entry13(self.run08(pop, findings))["status"], "failed")


class StatusPageTest(Setup):
    def test_the_across_page_is_built_from_pairs_json(self):
        sys.path.insert(0, str(ROOT / "status"))
        import status_page
        pop, findings = self.make(population="live")
        run = self.root / "runs" / "2026-11-14-live"
        shutil.copytree(findings, run)
        out = self.root / "site"
        status_page.cross_page(self.root / "runs", out, None)
        page = (out / "index.html").read_text()
        for cell in (">goose<", ">mesh<", "NOT_CHECKED", "evidence-network/loop#442", "evidence-network/loop#443"):
            self.assertIn(cell, page)
        self.assertIn("pairs.json", page)

    def test_a_pairs_json_that_is_not_the_cited_file_is_not_a_result(self):
        sys.path.insert(0, str(ROOT / "status"))
        import status_page
        pop, findings = self.make(population="live")
        run = self.root / "runs" / "2026-11-14-live"
        shutil.copytree(findings, run)
        (run / "13-ask-across" / "pairs.json").write_text('{"rows": []}')
        self.assertIsNone(status_page.cross_cells(run)[0][2])


class LoopShTest(unittest.TestCase):
    def test_stage_cross_lists_13_then_08_and_08_reads_it(self):
        text = (ROOT / "loop.sh").read_text()
        self.assertIn('STAGE_cross="13 08"', text)
        self.assertEqual(verify.stages()["cross"], ["13", "08"])
        self.assertIn("[13]=ask-across", text)

    def test_stage_cross_needs_ecosystem_cross(self):
        r = subprocess.run(["bash", str(ROOT / "loop.sh"), "--stage", "cross", "--ecosystem", "mesh"],
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("--ecosystem cross", r.stderr)


if __name__ == "__main__":
    unittest.main()
