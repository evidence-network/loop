#!/usr/bin/env python3
"""Hydrate the newest live run from its release archive (evidence-network/loop#336).

  scripts/hydrate_findings.py [FINDINGS_DIR] [--archive-file TGZ]

A live run harvested into main (scripts/live_harvest.py, loop#309) keeps only finding.json files and
proof-archive.json; its proof bytes are the release asset that file names. The site and status builds read proof
bytes (volume.json, counts.json, the edges), so they run this first. It finds the newest live run under FINDINGS_DIR
(default findings), and if that run is digest-only it downloads the archive from proof-archive.json's url (or takes
--archive-file), checks the bytes against proof-archive.json's sha256 and unpacks them over the run directory, then
runs steps/finding.py's full check on it. Nothing else is touched; a run that already has its proofs is left alone.

Exit: 0 hydrated or nothing to do; 1 the download failed; 2 the archive does not match proof-archive.json's sha256;
3 the archive is malformed or the hydrated run fails the check.
"""
import argparse
import json
import sys
import shutil
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "steps"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "status"))
import finding  # noqa: E402
import live_harvest  # noqa: E402  (its archive-member allowlist and sha256)
import status_page  # noqa: E402  (which run is the newest)


class Refuse(Exception):
    def __init__(self, code, reason):
        super().__init__(reason)
        self.code = code


def download(url, dest):
    if not url.startswith("https://"):
        raise Refuse(1, f"{url} is not an https URL")
    try:
        with urllib.request.urlopen(url, timeout=120) as r, open(dest, "wb") as f:
            while block := r.read(1 << 20):
                f.write(block)
    except OSError as e:
        raise Refuse(1, f"downloading {url} failed: {e}")


def hydrate(findings, archive_file=None, goose=False):
    """The run directory hydrated, or None when the newest live run is absent or already complete. goose: the newest live
    run under findings/goose/ (loop#363), whose archive holds findings/goose/<name> and is its own release asset."""
    if goose:
        findings = Path(findings) / "goose"
        if not findings.is_dir():
            return None
    run = status_page.latest_runs(findings)["live"]
    if run is None or not finding.digest_only(run):
        return None
    pin = json.loads((run / finding.ARCHIVE).read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory(prefix="hydrate-") as tmp:
        tgz = Path(archive_file) if archive_file else Path(tmp) / pin["asset"]
        if not archive_file:
            download(pin["url"], tgz)
        got = live_harvest.sha256(tgz)
        if got != pin["sha256"]:
            raise Refuse(2, f"{pin['asset']} has sha256 {got}, but {run.name}/{finding.ARCHIVE} records {pin['sha256']}")
        try:
            # The Mesh run's tgz also carries the day's Goose directory (loop#363); it is not this run's to unpack.
            # Any plain-named Goose directory the harvest let into the tgz (it scanned them), however it is named.
            src = live_harvest.extract(tgz, pin["asset"], [f"findings/{'goose/' if goose else ''}{run.name}"],
                                       Path(tmp) / "unpacked", any_goose=not goose)[0]
        except live_harvest.Refuse as r:
            raise Refuse(3, str(r))
        # Written and checked on a copy, and put in place only when it passes: a run that fails the check stays exactly as
        # committed (digest-only), never half hydrated.
        stage = Path(tmp) / "stage" / run.name
        shutil.copytree(run, stage)
        for f in sorted(p for p in src.rglob("*") if p.is_file()):
            rel = f.relative_to(src)
            if len(rel.parts) > 1 and rel.name == "finding.json":
                continue  # the committed finding is the record; the check below compares its proof digests to these bytes
            if rel.parts[0] in ("run.json", "index.md") or (stage / rel.parts[0]).is_dir():
                (stage / rel).parent.mkdir(parents=True, exist_ok=True)
                (stage / rel).write_bytes(f.read_bytes())
        errs = finding.check(stage)
        if errs:
            raise Refuse(3, f"{run.name} fails steps/finding.py check after hydration: {'; '.join(errs)[:300]}")
        if finding.digest_only(stage):
            raise Refuse(3, f"{run.name} still lacks proof files after hydration: the archive does not hold them")
        shutil.rmtree(run)
        shutil.copytree(stage, run)
    return run


def main(argv):
    ap = argparse.ArgumentParser(prog="hydrate_findings.py")
    ap.add_argument("findings", nargs="?", default="findings")
    ap.add_argument("--archive-file")
    args = ap.parse_args(argv)
    try:
        # --archive-file is one run's archive: it completes the Mesh run (the original use), never a Goose one.
        runs = [hydrate(args.findings, args.archive_file)]
    except Refuse as r:
        print(f"hydrate_findings: {r}", file=sys.stderr)
        return r.code
    if not args.archive_file:
        # The Goose run (loop#363) is the page's second column: its download, digest or check failing must not stop the
        # Mesh site (this step's non-zero exit fails stats.yml, which then deploys nothing), so it is logged and left as
        # committed (digest-only, which the status page reads).
        try:
            runs.append(hydrate(args.findings, goose=True))
        except Exception as r:  # nothing about the Goose run may stop the Mesh site (a cut-short download is not an OSError)
            print(f"::warning::hydrate_findings: Goose run left digest-only: {r}", file=sys.stderr)
    done = [str(r) for r in runs if r]
    print(f"hydrate_findings: hydrated {', '.join(done)}" if done else "hydrate_findings: nothing to hydrate")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
