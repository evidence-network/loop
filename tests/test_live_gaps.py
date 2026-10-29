"""No live gap cites a closed issue: tests/fixtures/closed_issues.json is the list, kept in one place.

A gap is cited in a step's not_checked entries, in the gap sentences of a PASS
detail, as a string in a step's or a script's source, and in the docs that
describe a step. Each is read here with no network: the live findings come
from the steps run on fixtures (no node, no console that answers).
"""
import ast
import importlib.machinery
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "steps"))
import finding  # noqa: E402

CLOSED = json.loads((ROOT / "tests" / "fixtures" / "closed_issues.json").read_text())["closed"]
REF = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+#\d+")
SOURCES = ["steps", "status", "scripts", "reporter", "lab"]
DOCS = [*ROOT.glob("*.md"), *(ROOT / "steps").glob("*.md"), ROOT / "status" / "README.md",
        ROOT / "reporter" / "README.md", ROOT / "nodes" / "README.md",
        *(p for p in (ROOT / "population").rglob("*.md") if "live" not in p.parts)]


def closed_refs(text):
    """The closed issues a text cites."""
    return sorted({r for r in REF.findall(str(text)) if r in CLOSED})


def python_sources():
    """Every Python file under SOURCES: *.py, and the extensionless step scripts with a python shebang."""
    for d in SOURCES:
        for p in sorted((ROOT / d).rglob("*")):
            if not p.is_file() or "fixtures" in p.parts:
                continue
            if p.suffix == ".py" or (not p.suffix and p.read_bytes()[:40].startswith(b"#!/usr/bin/env python3")):
                yield p


def string_constants(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]


def cited_by_finding(record):
    """[(where, closed issue)] for a finding's not_checked entries and detail gaps."""
    out = []
    for i in record["not_checked"]:
        out += [(f"not_checked {i['what']!r}", r) for r in closed_refs(f"{i['issue']} {i['why']}")]
    out += [(f"gap {what!r}", issue) for what, issue in finding.gaps(record["detail"]) if issue in CLOSED]
    return out


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


class ClosedListTest(unittest.TestCase):
    def test_the_list_is_well_formed(self):
        self.assertTrue(CLOSED)
        for ref, why in CLOSED.items():
            self.assertTrue(finding.ISSUE_REF.match(ref), ref)
            self.assertTrue(why.strip(), ref)

    def test_the_checks_find_a_planted_citation(self):
        ref = next(iter(CLOSED))
        planted = {"not_checked": [{"what": "w", "why": "y", "issue": ref}],
                   "detail": finding.outside("a part", ref)}
        self.assertEqual([r for _, r in cited_by_finding(planted)], [ref, ref])
        self.assertEqual(closed_refs(f"until {ref} lands"), [ref])
        self.assertEqual(closed_refs("capsules-plugin PR #75, in the pin"), [])


class SourcesTest(unittest.TestCase):
    def test_no_step_or_script_names_a_closed_issue(self):
        found = [f"{p.relative_to(ROOT)}: {r}" for p in python_sources() for s in string_constants(p)
                 for r in closed_refs(s)]
        self.assertEqual(found, [])

    def test_no_shell_script_names_a_closed_issue(self):
        found = [f"{p.relative_to(ROOT)}: {r}" for d in SOURCES for p in sorted((ROOT / d).rglob("*.sh"))
                 for r in closed_refs(p.read_text(encoding="utf-8"))]
        self.assertEqual(found, [])

    def test_no_doc_names_a_closed_issue(self):
        found = [f"{p.relative_to(ROOT)}: {r}" for p in DOCS if p.is_file()
                 for r in closed_refs(p.read_text(encoding="utf-8"))]
        self.assertEqual(found, [])


class LiveFindingsTest(unittest.TestCase):
    """The live findings, on fixtures: none cites a closed issue."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.findings = Path(self.tmp.name) / "findings"

    def tearDown(self):
        self.tmp.cleanup()

    def record(self, step):
        r = json.loads((self.findings / step / "finding.json").read_text())
        self.assertEqual(finding.check(self.findings), [])
        return r

    def test_step03_live(self):
        step = load("gaps_step03", ROOT / "steps" / "03-advertise")
        pop = ROOT / "tests" / "fixtures" / "step03" / "population" / "live"
        self.assertEqual(step.main(["--population", str(pop), "--out", str(self.findings / "03-advertise"),
                                    "--carrier", str(ROOT)]), 0)
        self.assertEqual(cited_by_finding(self.record("03-advertise")), [])

    def test_step05_live(self):
        live = ROOT / "tests" / "fixtures" / "step05" / "live"
        env = dict(os.environ, LOOP_TOOLS=self.tmp.name, LAB_DIR=str(live))
        env.pop("LOOP_STEP05_DEAL_BIN", None)
        r = subprocess.run([sys.executable, "-B", str(ROOT / "steps" / "05-deal"), "--population", str(live),
                            "--out", str(self.findings / "05-deal")], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(cited_by_finding(self.record("05-deal")), [])

    def test_step12_live(self):
        step = load("gaps_step12", ROOT / "steps" / "12-join")
        pop = Path(self.tmp.name) / "live"
        pop.mkdir()
        # A node with no nodes/ entry is never asked: no console is reached.
        (pop / "population.json").write_text(json.dumps({"population": "live", "rail": None,
                                                         "nodes": [{"name": "unlisted-node"}]}))
        self.assertEqual(step.main(["--population", str(pop), "--out", str(self.findings / "12-join"),
                                    "--console", "http://127.0.0.1:1"]), 0)
        r = self.record("12-join")
        self.assertEqual(cited_by_finding(r), [])
        self.assertEqual(cited_by_finding({"not_checked": step.LIVE_NOT_CHECKED, "detail": ""}), [])


if __name__ == "__main__":
    unittest.main()
