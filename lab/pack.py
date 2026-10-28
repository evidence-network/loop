#!/usr/bin/env python3
"""Write the lab nodes' entries into this clone, and pack what goes back into the repository.

Usage:
  pack.py entries --lab-dir DIR --operator HANDLE [--reporter lab-3] [--root DIR] [--dry-run]
  pack.py pack --lab-dir DIR --date YYYY-MM-DD [--root DIR] [--dry-run]

entries: for each node in population/live/population.json, reads its node id and
plugin key id from the public files its capsules plugin wrote under
DIR/nodes/<name>/.local/share/capsules (scripts/reporter_key.py), and writes
  - nodes/<node_id>.json, with the operator statement check_nodes.CONSENT;
  - the node ids into population/live/population.json;
  - the reporter node's ids into reporter/reporter.json.
It refuses when DIR/build.json says the build is not the pinned one, and when the
reporter's private key is readable beyond its user.

pack: one tarball, DIR/out/loop-lab-<date>.tgz, whose paths are this repository's:
  nodes/<node_id>.json, population/live/population.json, reporter/reporter.json,
  findings/<date>-live/..., and lab/runs/<date>/ (build.json, join/<name>/... as
  join.sh wrote it, MANIFEST.json with every packed file's sha256).
No key material goes in: a *.seed or *.pem file is left out, and a file holding a
PEM private key or the mesh invite token stops the pack.

--dry-run prints what would be written or packed and writes nothing.
"""
import argparse
import hashlib
import io
import json
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_nodes  # noqa: E402
import reporter_key  # noqa: E402

SKIP_SUFFIXES = (".seed", ".pem")
PRIVATE_MARK = b"PRIVATE KEY-----"


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dump(obj):
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"


def data_dir(lab_dir, name):
    return Path(lab_dir) / "nodes" / name / ".local" / "share" / "capsules"


def build_strings(build):
    """(host_build, plugin_version) for the entries, from build.json."""
    m, c = build["mesh_llm"], build["capsules"]
    pins = ", ".join(f"{k} {v}" for k, v in sorted(m["contains"].items()) if v)
    host_build = (f"mesh-llm {m['source_head']} (contains {pins}), binary sha256 {m['binary_sha256']}; "
                  "no relay until Mesh-LLM/capsules-plugin#48 is closed")
    return host_build, f"capsules {c['source_head']}, binary sha256 {c['binary_sha256']}"


def entries(root, lab_dir, operator, reporter, dry_run=False):
    """Write (or print) the entries. Returns a list of errors; empty means done."""
    build_path = Path(lab_dir) / "build.json"
    pop_path = root / "population" / "live" / "population.json"
    if dry_run and not build_path.is_file():
        # Nothing is up yet: say what would be written, from what.
        print(f"would read {build_path} (lab/lab.sh check) and each node's "
              f"{data_dir(lab_dir, '<name>')}/{{self-peer-id,keys/node-key.pub.pem}}")
        for node in load(pop_path)["nodes"]:
            print(f"would write nodes/<{node['name']} node_id>.json "
                  f"(operator {operator}, consent check_nodes.CONSENT, mesh lab, ask_route mesh)")
        print("would write population/live/population.json (the node ids)")
        print(f"would write reporter/reporter.json ({reporter}'s node_id and key_id)")
        return []
    if not build_path.is_file():
        return [f"{build_path}: missing; run lab/lab.sh check first"]
    build = load(build_path)
    if build.get("ok") is not True:
        return [f"{build_path}: the build is not the pinned one: " + "; ".join(build.get("failures", []))]
    errs = check_nodes.check_operator(operator)
    if errs:
        return errs
    host_build, plugin_version = build_strings(build)
    pop = load(pop_path)
    writes = []
    reporter_identity = None
    for node in pop["nodes"]:
        d = data_dir(lab_dir, node["name"])
        try:
            identity = reporter_key.read_identity(d)
        except (OSError, ValueError) as e:
            return [f"{node['name']}: {e}"]
        entry = {
            "node_id": identity["node_id"],
            "key_id": identity["key_id"],
            "operator": operator,
            "consent": check_nodes.CONSENT,
            "mesh": "lab",
            "ask_route": "mesh",
            "disclosure": {"policy": "counterparties", "stranger_safe": list(check_nodes.STRANGER_SAFE)},
            "host_build": host_build,
            "plugin_version": plugin_version,
        }
        path = root / "nodes" / f"{identity['node_id']}.json"
        errs = check_nodes.check_entry(path, entry)
        if errs:
            return errs
        writes.append((path, dump(entry)))
        node["node_id"] = identity["node_id"]
        if node["name"] == reporter:
            try:
                reporter_key.check_private_key(d)
            except (OSError, ValueError) as e:
                return [f"{reporter} (reporter): {e}"]
            reporter_identity = identity
    if reporter_identity is None:
        return [f"the reporter {reporter} is not a node in {pop_path}"]
    writes.append((pop_path, json.dumps(pop, indent=2) + "\n"))
    rep_path = root / "reporter" / "reporter.json"
    rep = load(rep_path)
    rep.update(reporter_identity, operator=operator, mesh="lab", host_build=host_build, plugin_version=plugin_version)
    writes.append((rep_path, json.dumps(rep, indent=2) + "\n"))
    for path, text in writes:
        if dry_run:
            print(f"would write {path.relative_to(root)}:")
            print("".join(f"  {line}\n" for line in text.splitlines()), end="")
        else:
            path.write_text(text, encoding="utf-8")
            print(f"wrote {path.relative_to(root)}")
    return []


def walk(src, arc):
    """(arcname, path) for every regular file under src, sorted; nothing for a missing dir."""
    if not src.is_dir():
        return []
    return [(f"{arc}/{p.relative_to(src).as_posix()}", p) for p in sorted(src.rglob("*"))
            if p.is_file() and not p.is_symlink()]


def plan(root, lab_dir, day):
    """([(arcname, source path)], [what is missing]) for the tarball."""
    lab_dir = Path(lab_dir)
    run = f"lab/runs/{day}"
    files, missing = [], []
    pop_path = root / "population" / "live" / "population.json"
    pop = load(pop_path)
    for node in pop["nodes"]:
        if node.get("node_id"):
            files.append((f"nodes/{node['node_id']}.json", root / "nodes" / f"{node['node_id']}.json"))
        else:
            missing.append(f"nodes/<{node['name']} node_id>.json (run lab/lab.sh entries)")
    files.append(("population/live/population.json", pop_path))
    files.append(("reporter/reporter.json", root / "reporter" / "reporter.json"))
    findings = walk(root / "findings" / f"{day}-live", f"findings/{day}-live")
    if not findings:
        missing.append(f"findings/{day}-live/ (run lab/lab.sh loop)")
    files += findings
    files.append((f"{run}/build.json", lab_dir / "build.json"))
    for node in pop["nodes"]:
        joined = walk(lab_dir / "join" / node["name"], f"{run}/join/{node['name']}")
        if not joined:
            missing.append(f"{run}/join/{node['name']}/ (run lab/lab.sh join)")
        files += joined
    files = [(a, p) for a, p in files if not p.name.endswith(SKIP_SUFFIXES)]
    missing += [a for a, p in files if not p.is_file()]
    return [(a, p) for a, p in files if p.is_file()], missing


def refuse_secrets(files, lab_dir):
    """Errors for any file that holds a PEM private key or the mesh invite token."""
    token_path = Path(lab_dir) / "run" / "invite.token"
    token = token_path.read_bytes().strip() if token_path.is_file() else b""
    errs = []
    for arc, path in files:
        data = path.read_bytes()
        if PRIVATE_MARK in data:
            errs.append(f"{arc}: holds a private key; it is never packed")
        if token and token in data:
            errs.append(f"{arc}: holds the mesh invite token; it is never packed")
    return errs


def pack(root, lab_dir, day, dry_run=False):
    files, missing = plan(root, lab_dir, day)
    out = Path(lab_dir) / "out" / f"loop-lab-{day}.tgz"
    if dry_run:
        print(f"would pack {out}:")
        for arc, path in files:
            print(f"  {arc}  <-  {path}")
        print(f"  lab/runs/{day}/MANIFEST.json  (sha256 of every file above)")
        for m in missing:
            print(f"  missing: {m}")
        return []
    if missing:
        return [f"missing: {m}" for m in missing]
    errs = refuse_secrets(files, lab_dir)
    if errs:
        return errs
    manifest = {"date": day, "files": {arc: hashlib.sha256(p.read_bytes()).hexdigest() for arc, p in files}}
    out.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out, "w:gz") as tar:
        def add(arc, data):
            info = tarfile.TarInfo(arc)
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            tar.addfile(info, io.BytesIO(data))
        for arc, path in files:
            add(arc, path.read_bytes())
        add(f"lab/runs/{day}/MANIFEST.json", dump(manifest).encode())
    print(f"packed {len(files) + 1} files into {out}")
    print(f"sha256 {hashlib.sha256(out.read_bytes()).hexdigest()}  {out.name}")
    return []


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("entries")
    e.add_argument("--operator", required=True)
    e.add_argument("--reporter", default="lab-3")
    p = sub.add_parser("pack")
    p.add_argument("--date", required=True)
    for s in (e, p):
        s.add_argument("--lab-dir", required=True, type=Path)
        s.add_argument("--root", type=Path, default=ROOT)
        s.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "entries":
        errs = entries(a.root, a.lab_dir, a.operator, a.reporter, a.dry_run)
    else:
        errs = pack(a.root, a.lab_dir, a.date, a.dry_run)
    for err in errs:
        print(f"pack.py: {err}", file=sys.stderr)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
