"""scripts/live_retention.py (evidence-network/loop#312): which live runs main may reduce to finding.json + digests.
Run: python3 -B -m unittest tests.test_live_retention -v
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "steps"))
sys.path.insert(0, str(ROOT / "scripts"))
import finding  # noqa: E402
import live_retention  # noqa: E402

DIGEST = "a" * 64


def make_run(findings, name, archive=True):
    for step in ("04-admit", "06-settle"):
        d = findings / name / step
        (d / "sub").mkdir(parents=True)
        (d / "counts.json").write_text(f"proof {step} {name}\n")
        (d / "sub" / "rows.json").write_text("rows\n")
        finding.write(d, result="PASS", population="live", asserts="stub", pinned=[], proof=["counts.json", "sub/rows.json"],
                      detail="stub", not_checked=[], started="2026-11-01T00:00:00Z")
    (findings / name / "run.json").write_text("{}\n")
    (findings / name / "index.md").write_text("# run\n")
    if archive:
        url = f"https://github.com/evidence-network/loop/releases/download/live-{name}/{name}.tgz"
        (findings / name / finding.ARCHIVE).write_text(json.dumps({"asset": f"{name}.tgz", "sha256": DIGEST, "url": url}))


class RetentionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.findings = Path(self.tmp.name) / "findings"
        self.addCleanup(self.tmp.cleanup)

    def actions(self, baseline=None):
        return {e["run"]: e["action"] for e in live_retention.plan(self.findings, baseline)}

    def test_newest_kept_older_archived_pruned(self):
        for n in ("2026-10-31-live-9", "2026-10-31-live-10", "2026-11-01-live-2"):
            make_run(self.findings, n)
        self.assertEqual(self.actions(), {"2026-11-01-live-2": "keep", "2026-10-31-live-10": "prune", "2026-10-31-live-9": "prune"})

    def test_numeric_not_lexical_order(self):
        for n in ("2026-10-31-live-9", "2026-10-31-live-10"):
            make_run(self.findings, n)
        self.assertEqual(self.actions()["2026-10-31-live-10"], "keep")

    def test_run_without_archive_is_held(self):
        make_run(self.findings, "2026-10-30-live", archive=False)
        make_run(self.findings, "2026-11-01-live")
        e = {x["run"]: x for x in live_retention.plan(self.findings)}["2026-10-30-live"]
        self.assertEqual((e["action"], e["files"]), ("hold", []))

    def test_invalid_archive_is_held(self):
        make_run(self.findings, "2026-10-30-live")
        make_run(self.findings, "2026-11-01-live")
        (self.findings / "2026-10-30-live" / finding.ARCHIVE).write_text(json.dumps({"asset": "x.tgz"}))
        self.assertEqual(self.actions()["2026-10-30-live"], "hold")

    def test_baseline_run_is_kept(self):
        for n in ("2026-10-30-live", "2026-11-01-live"):
            make_run(self.findings, n)
        base = Path(self.tmp.name) / "live.json"
        base.write_text(json.dumps({"ecosystems": {"mesh": {"findings": "findings/2026-10-30-live"}}}))
        self.assertEqual(self.actions(base)["2026-10-30-live"], "keep")

    def test_other_populations_are_outside_the_rule(self):
        make_run(self.findings, "2026-10-30-demo")
        make_run(self.findings, "2026-10-30-rehearsal")
        (self.findings / "goose").mkdir()
        make_run(self.findings, "2026-11-01-live")
        self.assertEqual(list(self.actions()), ["2026-11-01-live"])

    def test_prune_keeps_findings_and_run_records_and_names_the_rest(self):
        make_run(self.findings, "2026-10-30-live")
        make_run(self.findings, "2026-11-01-live")
        e = {x["run"]: x for x in live_retention.plan(self.findings)}["2026-10-30-live"]
        self.assertEqual(e["files"], ["04-admit/counts.json", "04-admit/sub/rows.json", "06-settle/counts.json", "06-settle/sub/rows.json"])
        self.assertEqual(e["bytes"], sum(len(f"proof {x} 2026-10-30-live\n") + len("rows\n") for x in ("04-admit", "06-settle")))

    def test_pruned_layout_still_passes_finding_check(self):
        make_run(self.findings, "2026-10-30-live")
        run = self.findings / "2026-10-30-live"
        for rel in live_retention.prunable(run):  # on a copy: the rule itself deletes nothing
            (run / rel).unlink()
        self.assertEqual(finding.check(run), [])

    def test_plan_deletes_nothing(self):
        for n in ("2026-10-30-live", "2026-11-01-live"):
            make_run(self.findings, n)
        before = sorted(p.as_posix() for p in self.findings.rglob("*"))
        live_retention.plan(self.findings)
        subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "live_retention.py"), str(self.findings)], check=True, capture_output=True)
        self.assertEqual(before, sorted(p.as_posix() for p in self.findings.rglob("*")))

    def test_real_findings_tree_plans_without_error(self):
        p = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "live_retention.py")], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_unreadable_baseline_refuses(self):
        make_run(self.findings, "2026-11-01-live")
        for text in (None, "not json", "{}"):
            base = Path(self.tmp.name) / "b.json"
            if text is not None:
                base.write_text(text)
            with self.assertRaises(ValueError):
                live_retention.plan(self.findings, base)
        p = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "live_retention.py"), str(self.findings),
                            "--baseline", str(Path(self.tmp.name) / "nope.json")], capture_output=True)
        self.assertEqual(p.returncode, 2)

    def test_json_output(self):
        for n in ("2026-10-30-live", "2026-11-01-live"):
            make_run(self.findings, n)
        p = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "live_retention.py"), str(self.findings), "--baseline", str(ROOT / "baseline" / "live.json"), "--json"],
                           capture_output=True, text=True)
        self.assertEqual([(e["run"], e["action"]) for e in json.loads(p.stdout)], [("2026-11-01-live", "keep"), ("2026-10-30-live", "prune")])

    def test_real_tree_keeps_newest_and_the_baseline_run(self):
        a = {e["run"]: e["action"] for e in live_retention.plan(ROOT / "findings", ROOT / "baseline" / "live.json")}
        newest = max(a, key=lambda n: (n[:10], int(n.rsplit("-", 1)[1]) if n[-1].isdigit() and "live-" in n else 0))
        self.assertEqual(a[newest], "keep")
        self.assertEqual(a[live_retention.baseline_run(ROOT / "baseline" / "live.json")], "keep")

    def test_missing_dir_exits_2(self):
        p = subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "live_retention.py"), str(self.findings / "nope")], capture_output=True)
        self.assertEqual(p.returncode, 2)


if __name__ == "__main__":
    unittest.main()
