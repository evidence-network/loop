"""steps/cargo-build-cached.sh: a pinned crate is rebuilt when an input changes, and only then (loop#489)."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "steps" / "cargo-build-cached.sh"

# A stand-in cargo: counts its builds and writes the binary where `cargo build --release` would.
CARGO = """#!/bin/sh
case "$1" in
  --version) echo "cargo 1.0.0-test"; exit 0 ;;
esac
echo "start $(date +%s.%N 2>/dev/null || date +%s)" >> "$BUILDS.log"
sleep "${SLOW:-0}"
echo build >> "$BUILDS"
echo "end" >> "$BUILDS.log"
mkdir -p "$CARGO_TARGET_DIR/release"
printf '#!/bin/sh\\necho built\\n' > "$CARGO_TARGET_DIR/release/tool"
chmod 755 "$CARGO_TARGET_DIR/release/tool"
"""


class CargoBuildCachedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.tmp)])
        bin_ = self.tmp / "bin"
        bin_.mkdir()
        (bin_ / "cargo").write_text(CARGO)
        (bin_ / "rustc").write_text('#!/bin/sh\necho "rustc 1.0.0-test"\n')
        for f in ("cargo", "rustc"):
            (bin_ / f).chmod(0o755)
        self.crate = self.tmp / "crate"
        (self.crate / "src").mkdir(parents=True)
        (self.crate / "Cargo.toml").write_text("[package]\n")
        (self.crate / "Cargo.lock").write_text("version = 3\n")
        (self.crate / "src" / "main.rs").write_text("fn main() {}\n")
        self.tools = self.tmp / "tools"
        self.builds = self.tmp / "builds"
        self.env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "BUILDS": str(self.builds)}
        self.env.pop("RUSTFLAGS", None)

    def build(self, **env):
        r = subprocess.run(["bash", "-c", f'. "{HELPER}" && cargo_build_cached "$1" "$2" tool', "-",
                            str(self.crate), str(self.tools)], capture_output=True, text=True,
                           env={**self.env, **env}, stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def count(self):
        return len(self.builds.read_text().splitlines()) if self.builds.exists() else 0

    def test_the_same_inputs_build_once(self):
        self.build()
        self.assertTrue(os.access(self.tools / "bin" / "tool", os.X_OK))
        r = self.build()
        self.assertEqual(self.count(), 1)
        self.assertIn("already built from these sources", r.stderr)

    def test_a_changed_source_lock_or_flag_rebuilds(self):
        self.build()
        (self.crate / "src" / "main.rs").write_text("fn main() { }\n")
        self.build()
        (self.crate / "Cargo.lock").write_text("version = 4\n")
        self.build()
        self.build(RUSTFLAGS="-C opt-level=1")
        self.assertEqual(self.count(), 4)

    def test_a_missing_binary_rebuilds_even_with_its_stamp(self):
        self.build()
        (self.tools / "bin" / "tool").unlink()
        self.build()
        self.assertEqual(self.count(), 2)

    def test_the_build_output_does_not_count_as_an_input(self):
        self.build()
        (self.crate / "target").mkdir()
        (self.crate / "target" / "junk").write_text("x")
        self.build()
        self.assertEqual(self.count(), 1)

    def test_two_builds_into_one_tools_directory_take_turns(self):
        # Two checkouts at different sources: without the lock the binary of one could carry the stamp of the other.
        other = self.tmp / "crate2"
        subprocess.run(["cp", "-R", str(self.crate), str(other)], check=True)
        (other / "src" / "main.rs").write_text("fn main() { /* other */ }\n")
        procs = [subprocess.Popen(["bash", "-c", f'. "{HELPER}" && cargo_build_cached "$1" "$2" tool', "-", str(c),
                                   str(self.tools)], env={**self.env, "SLOW": "1"}, stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for c in (self.crate, other)]
        for p in procs:
            self.assertEqual(p.wait(timeout=60), 0)
        marks = [line.split()[0] for line in (self.tmp / "builds.log").read_text().splitlines()]
        self.assertEqual(marks, ["start", "end", "start", "end"])  # never two at once


if __name__ == "__main__":
    unittest.main()
