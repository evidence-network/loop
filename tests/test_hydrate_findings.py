"""scripts/hydrate_findings.py (evidence-network/loop#336): the newest digest-only live run is completed from its
release archive after a sha256 check. The archive is a local file (--archive-file); nothing reaches a network.
Run: python3 -B -m unittest tests.test_hydrate_findings -v
"""
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "steps"))
import finding  # noqa: E402

NAME = "2026-11-01-live-2"
OLD = "2026-10-31-live-9"


def make_run(findings, name, steps=("04-admit", "06-settle")):
    for step in steps:
        d = findings / name / step
        d.mkdir(parents=True)
        (d / "counts.json").write_text(f"proof {step} {name}\n")
        finding.write(d, result="PASS", population="live", asserts=f"stub {step}", pinned=[], proof=["counts.json"],
                      detail="stub", not_checked=[], started="2026-11-01T00:00:00Z")


class HydrateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hydrate-test-"))
        self.full = self.tmp / "full"
        make_run(self.full, NAME)
        self.tgz = self.tmp / f"{NAME}.tgz"
        with tarfile.open(self.tgz, "w:gz") as t:
            t.add(self.full / NAME, arcname=f"findings/{NAME}")
        self.findings = self.tmp / "findings"
        make_run(self.findings, NAME)
        for p in (self.findings / NAME).glob("*/counts.json"):
            p.unlink()  # digest-only, as harvested
        self.archive = {"asset": f"{NAME}.tgz", "sha256": hashlib.sha256(self.tgz.read_bytes()).hexdigest(),
                        "url": f"https://example.org/releases/download/live-{NAME}/{NAME}.tgz"}
        (self.findings / NAME / "proof-archive.json").write_text(json.dumps(self.archive))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def hydrate(self, tgz=None):
        return subprocess.run([sys.executable, "-B", str(ROOT / "scripts" / "hydrate_findings.py"), str(self.findings),
                               "--archive-file", str(tgz or self.tgz)], capture_output=True, text=True)

    def test_unpacks_the_archive_over_the_digest_only_run(self):
        self.assertTrue(finding.digest_only(self.findings / NAME))
        r = self.hydrate()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("hydrated", r.stdout)
        self.assertTrue((self.findings / NAME / "04-admit" / "counts.json").is_file())
        self.assertFalse(finding.digest_only(self.findings / NAME))
        self.assertEqual(finding.check(self.findings / NAME), [])
        self.assertIn("nothing to hydrate", self.hydrate().stdout)

    def test_a_wrong_sha256_changes_nothing(self):
        bad = self.tmp / "bad.tgz"
        bad.write_bytes(self.tgz.read_bytes() + b"x")
        r = self.hydrate(bad)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertFalse((self.findings / NAME / "04-admit" / "counts.json").exists())

    def test_a_member_outside_the_run_is_refused(self):
        extra = self.tmp / "evil.txt"
        extra.write_text("x\n")
        with tarfile.open(self.tgz, "w:gz") as t:
            t.add(self.full / NAME, arcname=f"findings/{NAME}")
            t.add(extra, arcname="scripts/evil.txt")
        self.archive["sha256"] = hashlib.sha256(self.tgz.read_bytes()).hexdigest()
        (self.findings / NAME / "proof-archive.json").write_text(json.dumps(self.archive))
        r = self.hydrate()
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertFalse((self.findings / NAME / "04-admit" / "counts.json").exists())

    def test_an_archive_missing_a_proof_file_is_refused(self):
        (self.full / NAME / "06-settle" / "counts.json").unlink()
        with tarfile.open(self.tgz, "w:gz") as t:
            t.add(self.full / NAME, arcname=f"findings/{NAME}")
        self.archive["sha256"] = hashlib.sha256(self.tgz.read_bytes()).hexdigest()
        (self.findings / NAME / "proof-archive.json").write_text(json.dumps(self.archive))
        r = self.hydrate()
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("still lacks", r.stderr)

    def test_only_the_newest_live_run_is_hydrated(self):
        make_run(self.findings, OLD)
        for p in (self.findings / OLD).glob("*/counts.json"):
            p.unlink()
        (self.findings / OLD / "proof-archive.json").write_text(json.dumps(
            {**self.archive, "asset": f"{OLD}.tgz", "url": self.archive["url"].replace(NAME, OLD)}))
        self.assertEqual(self.hydrate().returncode, 0)
        self.assertFalse((self.findings / OLD / "04-admit" / "counts.json").exists())

    def test_a_complete_run_is_left_alone(self):
        shutil.rmtree(self.findings / NAME)
        shutil.copytree(self.full / NAME, self.findings / NAME)
        r = self.hydrate()
        self.assertEqual((r.returncode, "nothing to hydrate" in r.stdout), (0, True), r.stderr)

    def test_no_findings_is_nothing_to_do(self):
        shutil.rmtree(self.findings)
        self.assertEqual(self.hydrate().returncode, 0)


if __name__ == "__main__":
    unittest.main()
