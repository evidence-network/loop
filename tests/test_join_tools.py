"""join.sh and steps/12-join.setup.sh around the tools themselves, with no real capsulectl.

A capsulectl that refuses its plugin must be quoted by join.sh, not read as a missing
--probe; and the setup leaves the tools and their directory not group-writable under a
umask of 002 (capsulectl refuses a plugin others can write), the already-built path included.
"""
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def pin(name):
    for line in (ROOT / "versions.lock").read_text().splitlines():
        f = line.split()
        if f and f[0] == name:
            return f[1], f[2]
    raise AssertionError(f"{name} is not pinned")


class Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)


@unittest.skipUnless(shutil.which("openssl"), "openssl is not installed: join.sh checks its keys with it")
class JoinRefusedPluginTest(Tmp):
    def test_capsulectl_refusal_is_quoted(self):
        fake = self.tmp / "bin" / "capsulectl"
        fake.parent.mkdir()
        fake.write_text("#!/bin/sh\necho 'plugin capsulectl-book refused: group-writable (planted)' >&2\nexit 1\n")
        fake.chmod(0o755)
        keys = []
        for k in ("node", "controller"):
            p = self.tmp / f"{k}.pem"
            subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(p)], check=True,
                           capture_output=True)
            keys.append(p)
        r = subprocess.run(["bash", str(ROOT / "join.sh"), "--name", "lab-1", "--node-key", str(keys[0]),
                            "--controller-key", str(keys[1]), "--out", str(self.tmp / "out"),
                            "--home", str(self.tmp / "home")],
                           capture_output=True, text=True, env=dict(os.environ, CAPSULECTL=str(fake)))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("plugin capsulectl-book refused: group-writable (planted)", r.stderr)
        self.assertIn("chmod go-w", r.stderr)


class SetupPermissionsTest(Tmp):
    def test_already_built_tools_are_made_not_group_writable(self):
        repo, tag = pin("capsulectl-book")
        bin_ = self.tmp / "tools" / "bin"
        bin_.mkdir(parents=True)
        for name in ("capsulectl", "capsulectl-book"):
            (bin_ / name).write_text("#!/bin/sh\n")
            (bin_ / name).chmod(0o775)
        (bin_ / "capsulectl.built-from").write_text(f"{repo} {tag} {'0' * 40} capsule-cli v0.0.0\n")
        bin_.chmod(0o775)
        bin_.parent.chmod(0o775)
        r = subprocess.run(["bash", "-c", 'umask 002 && exec bash "$0"', str(ROOT / "steps" / "12-join.setup.sh")],
                           capture_output=True, text=True, env=dict(os.environ, LOOP_TOOLS=str(self.tmp / "tools")))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("already in", r.stderr)
        for p in (bin_.parent, bin_, bin_ / "capsulectl", bin_ / "capsulectl-book"):
            self.assertEqual(p.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH), 0, p)


if __name__ == "__main__":
    unittest.main()
