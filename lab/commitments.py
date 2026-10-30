#!/usr/bin/env python3
"""Publish a lab node's own signed commitment under nodes/commitments/ in this clone.

    commitments.py --console URL [--root DIR] [--dry-run]

Asks the node's console for its `commitment` tool (capsules-plugin#75): the reply is
{"commitment": <object>, "policy": <base64 of the policy bytes>}. This writes, in this clone,
  - nodes/commitments/<node_id>.json           the `commitment` member exactly as the plugin returned it:
                                               the reply's own bytes for that member, not re-serialized;
  - nodes/commitments/policies/<node_id>.json  the decoded policy bytes, which `lcap.policy_uri`
                                               (policies/<node_id>.json) names and `lcap.policy_digest` covers.
The harness signs nothing. Before it writes, it refuses a commitment that scripts/check_commitment.py
does not verify (which also refuses any member outside the profile, so no controller key or npub can
ride along; a binding shows only as controller_binding.digest), whose node_id has no entry
nodes/<node_id>.json here, or whose key_id is not the one that entry lists. A node whose commitment
changed since the last run has its file replaced; one that did not is left alone. Nothing is
committed or pushed.
"""
import argparse
import base64
import binascii
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_commitment as cc  # noqa: E402

TOOL = "/api/plugins/capsules/tools/commitment"
DECODER = json.JSONDecoder()


def ask(console, timeout=60):
    """The console's raw reply bytes."""
    req = urllib.request.Request(console.rstrip("/") + TOOL, data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise SystemExit(f"commitments: {console} refused ({e.code}): {e.read().decode(errors='replace')[:300]}")
    except OSError as e:
        raise SystemExit(f"commitments: {console} did not answer: {e}")


def members(text):
    """{name: (start, end)} of each top-level member's value text in a JSON object, so a member can be
    taken as the sender wrote it."""
    i = text.index("{") + 1
    out = {}
    while True:
        while text[i] in " \t\r\n,":
            i += 1
        if text[i] == "}":
            return out
        key, i = DECODER.raw_decode(text, i)
        while text[i] in " \t\r\n:":
            i += 1
        start = i
        _, i = DECODER.raw_decode(text, i)
        out[key] = (start, i)


def no_duplicates(pairs):
    """A JSON object may not repeat a name: json.loads keeps the last, the file keeps every one."""
    names = [k for k, _ in pairs]
    dup = sorted({k for k in names if names.count(k) > 1})
    if dup:
        raise ValueError(f"repeats member {dup[0]!r}")
    return dict(pairs)


def take(raw):
    """(commitment text as returned, parsed commitment, policy bytes) from the console's reply, or a refusal."""
    try:
        text = raw.decode("utf-8")
        reply = json.loads(text, object_pairs_hook=no_duplicates)
    except ValueError as e:  # UnicodeDecodeError is one
        sys.exit(f"commitments: the node's reply is not usable JSON ({e}): {raw[:200]!r}")
    if not isinstance(reply, dict) or "commitment" not in reply or "policy" not in reply:
        sys.exit(f"commitments: the node returned no commitment: {text[:300]}")
    start, end = members(text)["commitment"]
    try:
        policy = base64.b64decode(reply["policy"], validate=True)
    except (binascii.Error, TypeError, ValueError):
        sys.exit("commitments: the policy is not base64")
    return text[start:end], reply["commitment"], policy


def publish(console, root, dry_run=False):
    text, doc, policy = take(ask(console))
    if not isinstance(doc, dict) or not isinstance(doc.get("node_id"), str) or not isinstance(doc.get("key_id"), str):
        sys.exit("commitments: the commitment names no node_id and key_id")
    node_id = doc["node_id"]
    entry = root / "nodes" / f"{node_id}.json"
    if not entry.is_file():
        sys.exit(f"commitments: no entry nodes/{node_id}.json here (run lab/lab.sh entries first)")
    listed = json.loads(entry.read_text(encoding="utf-8")).get("key_id")
    if doc["key_id"] != listed:
        sys.exit(f"commitments: the commitment for {node_id} is not under the key nodes/{node_id}.json lists")
    errs, _ = cc.verify(doc, lambda uri: policy if uri == f"policies/{node_id}.json" else None)
    if not errs and doc["lcap"]["policy_uri"] != f"policies/{node_id}.json":
        errs.append(f"lcap.policy_uri is {doc['lcap']['policy_uri']!r}, not policies/{node_id}.json")
    if errs:
        sys.exit(f"commitments: the commitment for {node_id} does not verify: " + "; ".join(errs))
    files = {root / "nodes" / "commitments" / f"{node_id}.json": (text + "\n").encode("utf-8"),
             root / "nodes" / "commitments" / "policies" / f"{node_id}.json": policy}
    changed = [p for p, data in files.items() if not p.is_file() or p.read_bytes() != data]
    verb = "would write" if dry_run else "wrote"
    if not changed:
        print(f"commitments: {node_id}: unchanged")
        return node_id
    for p in changed:
        if not dry_run:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(files[p])
        print(f"commitments: {node_id}: {verb} {p.relative_to(root)}")
    return node_id


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--console", required=True)
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    publish(a.console, a.root, a.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
