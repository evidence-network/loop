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
GH = '#!/usr/bin/env bash\necho "$*" | tr "\\n" " " >> "$STUB_LOG"; echo >> "$STUB_LOG"\n[ "$1 $2" = "issue list" ] && echo "${STUB_OPEN:-}"\n[ "$1" = api ] && echo "${STUB_STEP:-}"\n[ "$1 $2" = "${STUB_FAIL:-}" ] && { [ "$1 $2" != "issue create" ] || [[ "$*" == *--label* ]]; } && exit 1\nexit 0\n'


class ReportFailureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="live-pickup-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        gh = self.tmp / "gh"
        gh.write_text(GH)
        gh.chmod(0o755)
        self.log = self.tmp / "log"
        self.log.write_text("")

    def report(self, open_issue="", fail="", fn="report_failure failed", step="", notes="", check=True):
        env = {**os.environ, "PATH": f"{self.tmp}:{os.environ['PATH']}", "STUB_LOG": str(self.log), "STUB_OPEN": open_issue, "STUB_FAIL": fail, "STUB_STEP": step, "RUNNER_TEMP": str(self.tmp),
               "LIVE_VM": "vm", "LIVE_PROJECT": "p", "LIVE_ZONE": "z", "LIVE_LAB_USER": "u", "GITHUB_ENV": str(self.tmp / "env"),
               "GITHUB_SERVER_URL": "https://github.com", "GITHUB_REPOSITORY": "o/r", "GITHUB_RUN_ID": "7"}
        pre = notes and f"{notes}; "
        subprocess.run(["bash", "-e", "-c", f"source {ROOT}/ops/live-ci/ci.sh && {pre}{fn}"], env=env, check=check,
                       stdin=subprocess.DEVNULL, capture_output=True)
        return [x.rstrip() for x in self.log.read_text().splitlines()]

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

    def test_body_names_the_failed_step_exit_code_and_last_lines(self):
        # the host's status answer is public by design: its last lines are quoted
        c = self.report(step="Wait (2)", notes='note_host "live_daily.sh ended without a pack" 4 "$(printf "a\nb\nsettle refused")"')
        create = [x for x in c if x.startswith("issue create")][0]
        self.assertIn("Failed workflow step: Wait (2).", create)
        self.assertIn("live_daily.sh ended without a pack: exit 4", create)
        self.assertIn("settle refused", create)

    def test_a_failed_gcloud_call_reports_keywords_not_its_text(self):
        c = self.report(notes='note_call "start" 255 "ERROR: secret-host.example PERMISSION_DENIED for alice"')
        create = [x for x in c if x.startswith("issue create")][0]
        self.assertIn("start: exit 255; PERMISSION_DENIED", create)
        self.assertNotIn("secret-host", create)

    def test_green_run_closes_the_open_failure_issue_with_its_run_link(self):
        c = self.report("41", fn="close_failure")
        self.assertTrue(any(x.startswith("issue comment 41") and "actions/runs/7" in x for x in c))
        self.assertIn("issue close 41", c)

    def test_green_run_with_no_open_failure_issue_does_nothing(self):
        c = self.report(fn="close_failure")
        self.assertEqual([x for x in c if not x.startswith("issue list")], [])


    def test_stage_failure_never_quotes_the_host_text(self):
        # entry.sh's install and setfacl errors exit 23 with a path in their text; run the real harvest against a gcloud stand-in
        g = self.tmp / "gcloud"
        g.write_text('#!/usr/bin/env bash\necho "install: cannot stat /srv/lab/out/live/x.tgz: No such file"\nexit 23\n')
        g.chmod(0o755)
        c = self.report(notes="GH_TOKEN=t harvest || true")
        create = [x for x in c if x.startswith("issue create")][0]
        self.assertIn("stage: exit 23", create)
        self.assertNotIn("/srv/lab", create)

    def test_failure_paths_without_a_host_answer_still_leave_a_note(self):
        for fn, text in [("out_of_time", "no result after four waits of 50 minutes: exit 22"),
                         ('GH_TOKEN= ; harvest', "no App token minted")]:
            self.log.write_text("")
            c = self.report(notes=fn + " || true")
            create = [x for x in c if x.startswith("issue create")][0]
            self.assertIn(text, create, fn)

    def test_a_cancelled_step_is_named_too(self):
        script = (ROOT / "ops/live-ci/ci.sh").read_text()
        self.assertIn('.conclusion == "cancelled"', script)

    def test_a_failed_close_comment_does_not_close_the_issue(self):
        c = self.report("41", fail="issue comment", fn="close_failure", check=False)  # under bash -e the step stops
        self.assertFalse(any(x.startswith("issue close") for x in c))


if __name__ == "__main__":
    unittest.main()
