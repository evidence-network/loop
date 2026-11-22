"""ops/live-ci/ci.sh report_failure (evidence-network/loop#467): the failure issue carries the pickup label.
`gh` is a stand-in that records what it was asked and reports an open issue only when STUB_OPEN is set. Nothing reaches a network.
Run: python3 -B -m unittest tests.test_live_failure_pickup -v
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GH = '#!/usr/bin/env bash\necho "$*" >> "$STUB_LOG"\n[ "$1 $2" = "issue list" ] && echo "${STUB_OPEN:-}"\n[ "$1 $2" = "${STUB_FAIL:-}" ] && { [ "$1 $2" != "issue create" ] || [[ "$*" == *--label* ]]; } && exit 1\nexit 0\n'


class ReportFailureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="live-pickup-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        gh = self.tmp / "gh"
        gh.write_text(GH)
        gh.chmod(0o755)
        self.log = self.tmp / "log"
        self.log.write_text("")

    def report(self, open_issue="", fail=""):
        env = {**os.environ, "PATH": f"{self.tmp}:{os.environ['PATH']}", "STUB_LOG": str(self.log), "STUB_OPEN": open_issue, "STUB_FAIL": fail,
               "LIVE_VM": "vm", "LIVE_PROJECT": "p", "LIVE_ZONE": "z", "LIVE_LAB_USER": "u", "GITHUB_ENV": str(self.tmp / "env"),
               "GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "7"}
        subprocess.run(["bash", "-c", f"source {ROOT}/ops/live-ci/ci.sh && report_failure failed"], env=env, check=True,
                       stdin=subprocess.DEVNULL, capture_output=True)
        return self.log.read_text().splitlines()

    def test_new_failure_issue_carries_the_label_and_the_label_is_ensured(self):
        c = self.report()
        self.assertTrue(any(x.startswith("label create live-failure-pickup") for x in c))
        creates = [x for x in c if x.startswith("issue create")]
        self.assertEqual(len(creates), 1)
        self.assertIn("--label live-failure-pickup", creates[0])
        self.assertIn("--title loop-live-failed", creates[0])

    def test_open_issue_gets_the_label_and_a_comment_not_a_second_issue(self):
        c = self.report("41")
        self.assertIn("issue edit 41 --add-label live-failure-pickup", c)
        self.assertTrue(any(x.startswith("issue comment 41") for x in c))
        self.assertFalse(any(x.startswith("issue create") for x in c))

    def test_failed_label_edit_still_records_the_failure(self):
        c = self.report("41", fail="issue edit")
        self.assertTrue(any(x.startswith("issue comment 41") for x in c))

    def test_failed_labelled_create_opens_the_issue_without_the_label(self):
        # the stub fails an "issue create" that has --label, so the retry without it is the second logged call
        creates = [x for x in self.report(fail="issue create") if x.startswith("issue create")]
        self.assertEqual(len(creates), 2)
        self.assertIn("--label", creates[0])
        self.assertNotIn("--label", creates[1])


if __name__ == "__main__":
    unittest.main()
