#!/usr/bin/env python3
"""Write the lab nodes' entries into this clone, and pack what goes back into the repository.

Usage:
  pack.py record check|tools --lab-dir DIR   (lab/lab.sh: one command's outcome, JSON on stdin)
  pack.py entries --lab-dir DIR --operator HANDLE [--reporter lab-3] [--root DIR] [--dry-run]
  pack.py pack --lab-dir DIR --date YYYY-MM-DD [--root DIR] [--dry-run]

record: merges what `lab/lab.sh check` or `lab/lab.sh tools` found into DIR/build.json.
`check` rewrites everything but the tools record; `tools` rewrites only that. `failures`
is the check's failures then the setups' failures, and `ok` is true only when it is
empty and the check has run.

entries: for each node in population/live/population.json, reads its node id and
plugin key id from the public files its capsules plugin wrote under
DIR/nodes/<name>/.local/share/capsules (scripts/reporter_key.py), and writes
  - nodes/<node_id>.json, with the operator statement check_nodes.CONSENT;
  - the node ids, data_dir and book_dir into population/live/population.json;
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
import re
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


def record(lab_dir, section, outcome):
    """Merge one lab.sh command's outcome into lab_dir/build.json; returns the merged build."""
    path = Path(lab_dir) / "build.json"
    try:
        build = load(path)
    except (OSError, ValueError):
        build = {}
    if section == "check":
        tools = build.get("tools")
        build = dict(outcome)
        build["check_failures"] = build.pop("failures", [])
        if tools is not None:
            build["tools"] = tools
    else:
        build["tools"] = outcome
    check = build.get("check_failures", ["lab/lab.sh check has not run"])
    build["failures"] = list(check) + list((build.get("tools") or {}).get("failures", []))
    build["ok"] = not build["failures"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump(build), encoding="utf-8")
    return build


def build_strings(build):
    """(host_build, plugin_version) for the entries, from build.json."""
    m, c = build["mesh_llm"], build["capsules"]
    pins = ", ".join(f"{k} {v}" for k, v in sorted(m["contains"].items()) if v)
    host_build = (f"mesh-llm {m['source_head']} (contains {pins}), binary sha256 {m['binary_sha256']}; "
                  "no relay until Mesh-LLM/capsules-plugin#48 is closed")
    # A tag pin names its commit too: the tag is what versions.lock says, the commit what was checked.
    tag = c.get("pin") if c.get("pin") not in (None, c["source_head"]) else None
    at = f"{tag} ({c['source_head']})" if tag else c["source_head"]
    return host_build, f"capsules {at}, binary sha256 {c['binary_sha256']}"


def host_name(node):
    """The node's directory name under $LAB_DIR/nodes: from its data_dir once entries wrote one,
    else its name. A node's name is its operator's label and may differ (stevenmih-1 for lab-1)."""
    m = re.match(r"\$LAB_DIR/nodes/([^/]+)/", node.get("data_dir") or "")
    return m.group(1) if m else node["name"]


def entries(root, lab_dir, operator, reporter, dry_run=False, add_hosts=()):
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
        print("would write population/live/population.json (the node ids, data_dir and book_dir)")
        for host in add_hosts:
            print(f"would write {lab_dir}/population-twins/population.json (with {host}; not nodes/, not population/live)")
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
    # A twins-only lab node (lab-4 with LAB_TWINS=1) is not a committed live node: it gets no nodes/ entry and is not in
    # population/live. It joins a separate population written under the lab dir (population-twins/), which
    # `lab.sh dispute` reads, so step 09 can verify its half without the committed population changing.
    twins_pop = None
    extra = []
    for host in add_hosts:
        if not any(host_name(n) == host for n in pop["nodes"]):
            # Named as the others are (stevenmih-1..3), not from the operator's handle, which may differ in case.
            prefix = next((n["name"].rsplit("-", 1)[0] for n in pop["nodes"] if "-" in n["name"]), operator.lower())
            extra.append({"name": f"{prefix}-{host.rsplit('-', 1)[-1]}", "host": "lab",
                          "data_dir": f"$LAB_DIR/nodes/{host}/.local/share/capsules",
                          "book_dir": f"$LAB_DIR/nodes/{host}/join-book"})
    writes = []
    reporter_identity = None
    for node in pop["nodes"]:
        host = host_name(node)
        d = data_dir(lab_dir, host)
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
        # Where steps 01 and 02 find the node's key and book on this host, named from $LAB_DIR
        # (steps/live.py expands it) so no host path goes into the repository.
        node["data_dir"] = f"$LAB_DIR/nodes/{host}/.local/share/capsules"
        node["book_dir"] = f"$LAB_DIR/nodes/{host}/join-book"
        if host == reporter:
            try:
                reporter_key.check_private_key(d)
            except (OSError, ValueError) as e:
                return [f"{reporter} (reporter): {e}"]
            reporter_identity = identity
    if reporter_identity is None:
        return [f"the reporter {reporter} is not a node in {pop_path}"]
    writes.append((pop_path, json.dumps(pop, indent=2) + "\n"))
    if extra:
        for node in extra:
            try:
                node["node_id"] = reporter_key.read_identity(data_dir(lab_dir, host_name(node)))["node_id"]
            except (OSError, ValueError) as e:
                return [f"{node['name']}: {e}"]
        twins_pop = dict(pop, nodes=pop["nodes"] + extra)
        writes.append((Path(lab_dir) / "population-twins" / "population.json", json.dumps(twins_pop, indent=2) + "\n"))
    rep_path = root / "reporter" / "reporter.json"
    rep = load(rep_path)
    rep.update(reporter_identity, operator=operator, mesh="lab", host_build=host_build, plugin_version=plugin_version)
    writes.append((rep_path, json.dumps(rep, indent=2) + "\n"))
    for path, text in writes:
        if dry_run:
            print(f"would write {path.relative_to(root) if path.is_relative_to(root) else path}:")
            print("".join(f"  {line}\n" for line in text.splitlines()), end="")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            print(f"wrote {path.relative_to(root) if path.is_relative_to(root) else path}")
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
        joined = walk(lab_dir / "join" / host_name(node), f"{run}/join/{node['name']}")
        if not joined:
            missing.append(f"{run}/join/{node['name']}/ (run lab/lab.sh join)")
        files += joined
    # A twins run (LAB_TWINS=1 entries): the four-node population its 09-dispute finding ran on, and the twins-only node's join
    # output, as lab provenance under the run (neither is committed live: lab-4 is not in population/live or nodes/).
    twins_path = lab_dir / "population-twins" / "population.json"
    if twins_path.is_file():
        files.append((f"{run}/population-twins/population.json", twins_path))
        for node in load(twins_path)["nodes"]:
            if host_name(node) not in {host_name(n) for n in pop["nodes"]}:
                files += walk(lab_dir / "join" / host_name(node), f"{run}/join/{node['name']}")
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
    r = sub.add_parser("record")
    r.add_argument("section", choices=("check", "tools"))
    r.add_argument("--lab-dir", required=True, type=Path)
    e = sub.add_parser("entries")
    e.add_argument("--operator", required=True)
    e.add_argument("--reporter", default="lab-3")
    e.add_argument("--add-host", action="append", default=[], help="a twins-only lab host directory (lab-4): written to the lab dir's population-twins/, not to population/live")
    p = sub.add_parser("pack")
    p.add_argument("--date", required=True)
    for s in (e, p):
        s.add_argument("--lab-dir", required=True, type=Path)
        s.add_argument("--root", type=Path, default=ROOT)
        s.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "record":
        try:
            outcome = json.load(sys.stdin)
        except ValueError as e:
            print(f"pack.py: record {a.section}: not JSON on stdin: {e}", file=sys.stderr)
            return 1
        record(a.lab_dir, a.section, outcome)
        return 0
    if a.cmd == "entries":
        errs = entries(a.root, a.lab_dir, a.operator, a.reporter, a.dry_run, a.add_host)
    else:
        errs = pack(a.root, a.lab_dir, a.date, a.dry_run)
    for err in errs:
        print(f"pack.py: {err}", file=sys.stderr)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
