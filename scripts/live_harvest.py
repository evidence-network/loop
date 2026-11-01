#!/usr/bin/env python3
"""The live harvest (evidence-network/loop#309): turn one live_daily.sh run on the lab host into a data-only pull request.

  scripts/live_harvest.py [--name NAME] [--from-dir DIR] [--auto-merge]

Run it on the operator's Mac from a clone of this repository (any branch; the work happens in a temporary worktree at
origin/main, so the clone is left as it is). In order:

  1. fetch the run's NAME.tgz, NAME.tgz.sha256 and NAME.json from the lab host over `gcloud compute scp` (LIVE_HARVEST_VM,
     LIVE_HARVEST_ZONE, LIVE_HARVEST_REMOTE_DIR; NAME defaults to the newest run record there) into a fresh directory, or
     take them from --from-dir; check the tgz against its .sha256 and unpack it into a fresh directory (only
     findings/NAME/, no link, no member outside it)
  2. re-run check_no_preimages.py, check_personal_data.py and steps/finding.py check on what was unpacked
  3. a GitHub release live-NAME on the repository carries the three files: the proof bytes live there, not on main
  4. a branch live-harvest/NAME from origin/main takes findings/NAME holding only each step's finding.json (its proof is
     named there by sha256), run.json, index.md and proof-archive.json (the release asset and its digest), and the
     privacy checks run again over exactly what is committed
  5. ecosystem_gate.py update moves baseline/live.json only if the run passes it, and only if the run paid its exchange;
     a regression, or a refused payment, commits the findings and leaves the baseline untouched
  6. commit (signed off), push, open the pull request. The pull request is data only (.github/harvest_allowlist.py checks
     it in CI) and a human merges it. --auto-merge arms GitHub's auto-merge (it waits for the required checks, it is not
     a bypass) and is refused unless the diff passes that allowlist and the baseline moved cleanly. It stays off until
     Steven has said yes (loop#309).

Exit: 0 the pull request is open and the baseline moved; 1 it is open and the baseline did not move (a regression or a refused
payment); 2 usage or setup; 4 the clone, the branch or the release is not clean to start; 5 the fetch failed or the tgz does
not match its digest; 6 the archive or the findings are malformed; 7 a privacy check hit (nothing is published). Each refusal
prints one line "live_harvest: <reason>". Needs git, gh, python3, and gcloud unless --from-dir.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME_RE = re.compile(r"\d{4}-\d\d-\d\d-live-[1-9][0-9]*")


class Refuse(Exception):
    def __init__(self, code, reason):
        super().__init__(reason)
        self.code = code


def run(*cmd, cwd=None, check=True, **kw):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, **kw)
    if check and p.returncode != 0:
        raise Refuse(4, f"{' '.join(cmd[:3])} failed: {(p.stderr or p.stdout).strip()[:300]}")
    return p


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch(args, dest):
    """NAME and the directory holding NAME.tgz, NAME.tgz.sha256 and NAME.json."""
    if args.from_dir:
        src = Path(args.from_dir)
        names = sorted(p.stem for p in src.glob("*.json"))
        name = args.name or (names[-1] if names else "")
        if not name:
            raise Refuse(5, f"no run record in {src}")
    else:
        vm, zone, remote = (os.environ.get(k) for k in ("LIVE_HARVEST_VM", "LIVE_HARVEST_ZONE", "LIVE_HARVEST_REMOTE_DIR"))
        if not (vm and zone and remote):
            raise Refuse(2, "set LIVE_HARVEST_VM, LIVE_HARVEST_ZONE and LIVE_HARVEST_REMOTE_DIR (or use --from-dir)")
        name = args.name
        if not name:
            ls = run("gcloud", "compute", "ssh", vm, "--zone", zone, "--command", f"ls -1 {remote}", check=False)
            if ls.returncode != 0:
                raise Refuse(5, f"listing {remote} on {vm} failed: {ls.stderr.strip()[:200]}")
            names = sorted(f[:-5] for f in ls.stdout.split() if NAME_RE.fullmatch(f[:-5]) and f.endswith(".json"))
            if not names:
                raise Refuse(5, f"no run record in {remote} on {vm}")
            name = names[-1]
        if not NAME_RE.fullmatch(name):
            raise Refuse(2, f"{name!r} is not a <date>-live-<N> run name")
        for ext in (".tgz", ".tgz.sha256", ".json"):
            p = run("gcloud", "compute", "scp", "--zone", zone, f"{vm}:{remote}/{name}{ext}", str(dest / (name + ext)), check=False)
            if p.returncode != 0:
                raise Refuse(5, f"fetching {name}{ext} failed: {p.stderr.strip()[:200]}")
        return name, dest
    if not NAME_RE.fullmatch(name):
        raise Refuse(2, f"{name!r} is not a <date>-live-<N> run name")
    for ext in (".tgz", ".tgz.sha256", ".json"):
        if not (src / (name + ext)).is_file():
            raise Refuse(5, f"{src / (name + ext)} is missing")
        shutil.copy2(src / (name + ext), dest / (name + ext))
    return name, dest


def unpack(name, files, into):
    want = files / f"{name}.tgz"
    digest = (files / f"{name}.tgz.sha256").read_text().split()
    if not digest or digest[0] != sha256(want):
        raise Refuse(5, f"{name}.tgz does not match {name}.tgz.sha256")
    prefix = f"findings/{name}"
    try:
        with tarfile.open(want, "r:gz") as tar:
            for m in tar.getmembers():
                if not (m.isfile() or m.isdir()) or not (m.name == prefix or m.name.startswith(prefix + "/")) or ".." in Path(m.name).parts:
                    raise Refuse(6, f"{name}.tgz has a member outside {prefix}/ or that is not a file: {m.name}")
            tar.extractall(into, filter="data")
    except (tarfile.TarError, OSError) as e:
        raise Refuse(6, f"{name}.tgz cannot be unpacked: {e}")
    out = into / prefix
    if not out.is_dir():
        raise Refuse(6, f"{name}.tgz holds no {prefix}/")
    return out


def privacy(tree, where, code):
    """The checks are `code`'s (the worktree at origin/main), never the operator's clone, which may be on any branch."""
    for tool in ("check_no_preimages.py", "check_personal_data.py"):
        p = run(sys.executable, "-B", str(code / "scripts" / tool), str(tree), check=False)
        if p.returncode != 0:
            raise Refuse(7, f"{tool} flagged {where}: {(p.stdout + p.stderr).strip()[:300]}")
    p = run(sys.executable, "-B", str(code / "steps" / "finding.py"), "check", str(tree), check=False)
    if p.returncode != 0:
        raise Refuse(6, f"steps/finding.py check refused {where}: {p.stderr.strip()[:300]}")


def slim(full, dest, name, url, digest):
    """dest = full's finding.json files, run.json, index.md and the proof-archive.json naming the release asset."""
    dest.mkdir(parents=True)
    for step in sorted(p for p in full.iterdir() if p.is_dir()):
        (dest / step.name).mkdir()
        shutil.copy2(step / "finding.json", dest / step.name / "finding.json")
    for extra in ("run.json", "index.md"):
        if (full / extra).is_file():
            shutil.copy2(full / extra, dest / extra)
    (dest / "proof-archive.json").write_text(
        json.dumps({"asset": f"{name}.tgz", "sha256": digest, "url": url}, indent=2, sort_keys=True) + "\n")


def main(argv):
    ap = argparse.ArgumentParser(prog="live_harvest.py")
    ap.add_argument("--name")
    ap.add_argument("--from-dir")
    ap.add_argument("--auto-merge", action="store_true")
    args = ap.parse_args(argv)
    work = Path(tempfile.mkdtemp(prefix="live-harvest-"))
    wt = work / "wt"
    try:
        return harvest(args, work, wt)
    except Refuse as r:
        print(f"live_harvest: {r}", file=sys.stderr)
        return r.code
    finally:
        run("git", "worktree", "remove", "--force", str(wt), cwd=ROOT, check=False)
        shutil.rmtree(work, ignore_errors=True)


def harvest(args, work, wt):
    repo = os.environ.get("LIVE_HARVEST_REPO", "evidence-network/loop")
    files = work / "files"
    files.mkdir()
    name, files = fetch(args, files)
    full = unpack(name, files, work / "unpacked")
    record = json.loads((files / f"{name}.json").read_text())
    paid = bool(record.get("exchange", {}).get("paid"))
    digest = sha256(files / f"{name}.tgz")

    run("git", "fetch", "-q", "origin", "main", cwd=ROOT)
    branch = f"live-harvest/{name}"
    if run("git", "ls-remote", "--heads", "origin", branch, cwd=ROOT).stdout.strip():
        raise Refuse(4, f"origin already has {branch}")
    run("git", "worktree", "add", "-q", "--detach", str(wt), "origin/main", cwd=ROOT)
    privacy(full, f"{name} as unpacked", wt)
    if (wt / "findings" / name).exists():
        raise Refuse(4, f"findings/{name} is already on origin/main")
    tag = f"live-{name}"
    if run("gh", "release", "view", tag, "--repo", repo, check=False).returncode == 0:
        raise Refuse(4, f"release {tag} exists on {repo}: remove it by hand if no pull request holds it")

    url = f"https://github.com/{repo}/releases/download/{tag}/{name}.tgz"
    slim(full, wt / "findings" / name, name, url, digest)
    privacy(wt / "findings" / name, f"findings/{name} as committed", wt)

    # The worktree's own gate: its ROOT is the worktree, so the findings and the versions.lock are origin/main's.
    gate = run(sys.executable, "-B", str(wt / "scripts" / "ecosystem_gate.py"), "update", "--ecosystem", "mesh", "--findings",
               str(wt / "findings" / name), "--baseline", str(wt / "baseline" / "live.json"), check=False)
    # A refused payment leaves step 06 NOT_CHECKED with no result of the step: the baseline stays where it was.
    moved = gate.returncode == 0 and paid
    if gate.returncode == 0 and not paid:
        run("git", "checkout", "-q", "--", "baseline/live.json", cwd=wt)
    verdict = ("the baseline moved to this run" if moved else
               "REGRESSION: " + (gate.stderr or gate.stdout).strip() if gate.returncode != 0 else
               "the run's payment was refused (06 NOT_CHECKED), so the baseline did not move")

    paths = [f"findings/{name}"] + (["baseline/live.json"] if moved else [])
    run("git", "add", "--", *paths, cwd=wt)
    run("git", "checkout", "-q", "-b", branch, cwd=wt)
    run("git", "commit", "-q", "-s", "-m", f"data: live harvest {name}", cwd=wt)
    ok = run(sys.executable, "-B", str(wt / ".github" / "harvest_allowlist.py"), "origin/main", "HEAD", cwd=wt, check=False)
    if ok.returncode != 0:
        raise Refuse(6, f"the harvest commit fails its own allowlist: {ok.stderr.strip()[:300]}")

    notes = work / "notes.md"
    notes.write_text(f"Release asset (the proof bytes): {url}\nsha256 {digest}\n")
    run("gh", "release", "create", tag, str(files / f"{name}.tgz"), str(files / f"{name}.tgz.sha256"),
        str(files / f"{name}.json"), "--repo", repo, "--title", f"Live run {name}", "--notes-file", str(notes))
    run("git", "push", "-q", "origin", branch, cwd=wt)
    steps = ", ".join(f"{k} {v}" for k, v in sorted(record.get("steps", {}).items()))
    body = work / "body.md"
    body.write_text(
        f"Data-only harvest of live run `{name}` (loop#309).\n\n"
        f"- Steps: {steps}\n- Gate: {verdict}\n- Proof bytes: [{name}.tgz]({url}) (sha256 `{digest}`); "
        f"`findings/{name}/proof-archive.json` pins it, and `steps/finding.py check` accepts the digest-only layout.\n"
        "- The diff holds only `findings/<date>-live-<N>/` and `baseline/live.json` (`harvest-allowlist`). "
        "Preimage and personal-data checks ran on the full archive before it was published and on what is committed.\n"
        "- A human merges this pull request until Steven has approved auto-merge.\n")
    pr = run("gh", "pr", "create", "--repo", repo, "--base", "main", "--head", branch, "--title", f"data: live harvest {name}",
             "--body-file", str(body))
    print(pr.stdout.strip())
    if args.auto_merge:
        if not moved:
            raise Refuse(1, "not arming auto-merge: the baseline did not move")
        run("gh", "pr", "merge", "--repo", repo, "--auto", "--squash", pr.stdout.strip(), check=True)
    return 0 if moved else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
