"""A node's signed ledger checkpoint and the copy it covers, kept by a step and checked offline by 08 (loop#276).

The capsules plugin (v0.1.8, docs/LEDGER-CHECKPOINTS.md) signs a checkpoint over each ledger file it keeps and answers
`GET <console>/api/plugins/capsules/http/ledger-checkpoint?file=<name>` with the checkpoint and a range proof. A step
that re-derives from a copy of a ledger file keeps that reply beside the copy; a step that judges it checks the copy is
the whole file at the checkpoint with loop-ledger-check (steps/ledger-check.d), under the key the node lists (nodes/),
never the reply's own.

Only the files the plugin checkpoints can be shown complete (CHECKPOINTED); the rest keep their entry in loop#193.
Steps 04, 05 and 09 keep each node's files the same way (loop#288): keep_checked asks, then copies and cuts; judge
checks every kept copy of one node.
"""
import json
import os
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

import live

ISSUE = "Mesh-LLM/capsules-plugin#175"  # node-signed checkpoints and range proofs over a node's ledgers
ROUTE = "/api/plugins/capsules/http/ledger-checkpoint"
CHAIN = "capsules.jsonl"
CHECKPOINTED = (CHAIN, "received-capsules.jsonl", "received-provenance.jsonl", "received-inclusion.jsonl",
                "issued-adjudications.jsonl", "received-adjudications.jsonl", "requested-adjudications.jsonl")
CHECKER = "loop-ledger-check"
HERE = Path(__file__).resolve().parent
KEPT_SUFFIX = ".checkpoint.json"  # beside each kept copy: the node's reply for the file, or why there is none
SHAPE = "ledger_checkpoints"  # the nodes.json member a step that keeps KEPT_SUFFIX files sets to 1 (loop#288)
CONSOLES = "LOOP_NODE_CONSOLES"  # NAME=URL,...: each population node's own console (lab/lab.sh)
CONSOLE_ISSUE = "evidence-network/loop: lab/lab.sh gives each node's console (LOOP_NODE_CONSOLES)"
NOT_INSTALLED = f"{CHECKER} is not installed (steps/ledger-check.setup.sh)"
TOOLS_ISSUE = f"evidence-network/loop: steps/ledger-check.setup.sh installs {CHECKER} where the step runs"


def tools_dir():
    return Path(os.environ.get("LOOP_TOOLS", Path.home() / ".cache" / "evidence-network-loop"))


def checker():
    """The installed loop-ledger-check (steps/ledger-check.setup.sh), or None."""
    env = os.environ.get("LOOP_LEDGER_CHECK_BIN")
    p = Path(env) if env else tools_dir() / "ledger-check" / "bin" / CHECKER
    return p if p.is_file() and os.access(p, os.X_OK) else None


def fetch(console, file, timeout, http):
    """What the node's console answers to a ledger-checkpoint ask for `file`: {"reply": the reply or None,
    "refusal": the refusal text or None}. `http` is daily_report.http. The reply is kept as the node sent it."""
    url = f"{console}{ROUTE}?{urllib.parse.urlencode({'file': file})}"
    try:
        status, body = http("GET", url, None, timeout)
    except OSError as e:
        return {"file": file, "reply": None, "refusal": f"no answer from the console: {type(e).__name__}: {e}"}
    text = body.decode("utf-8", "replace")
    try:
        reply = json.loads(text)
    except ValueError:
        reply = None
    if status == 200 and isinstance(reply, dict) and reply.get("ledger_checkpoint") == 1:
        return {"file": file, "reply": reply, "refusal": None}
    return {"file": file, "reply": None, "refusal": f"HTTP {status}: {' '.join(text.split())[:500]}"}


def keep_copy(src, dest):
    """Copy the ledger file `src` to `dest` the way the loop keeps any ledger (live.copy_redacted: a payment
    preimage never leaves the lab host); returns how many lines changed. The copy keeps every line, so a line's
    position is the node's."""
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    if Path(src).name == CHAIN:  # the node's chain: from `capsules chain-lines` when it is on the shelf (loop#296)
        data, n = live.redact_bytes(live.chain_bytes(Path(src).parent))
        Path(dest).write_bytes(data)
        return n
    return live.copy_redacted(src, dest)


def prefix(raw, lines):
    """The first `lines` complete lines of `raw` (bytes), each with its newline; None when fewer are there."""
    out, at = [], 0
    for _ in range(lines):
        end = raw.find(b"\n", at)
        if end < 0:
            return None
        out.append(raw[at:end + 1])
        at = end + 1
    return b"".join(out)


def covered(reply):
    """How many lines the reply's checkpoint covers (a whole-file reply: to_index + 1), or None for a reply that is
    not one."""
    try:
        proof = reply["range_proof"]
        if proof["from_index"] != 0:
            return None
        return int(proof["to_index"]) + 1
    except (KeyError, TypeError, ValueError):
        return None


def check(raw, reply, key_id, file):
    """(info, None) when the first lines of the kept copy `raw` are the whole `file` at the reply's checkpoint under
    `key_id`, else (None, reason). `info` is the checker's reading (lines, log id, cut time, redacted lines).
    Raises FileNotFoundError when the checker is not installed."""
    bin_ = checker()
    if bin_ is None:
        raise FileNotFoundError(CHECKER)
    n = covered(reply)
    if not n:
        return None, "the reply is not a checkpoint over the whole file (range_proof from_index 0)"
    cut = prefix(raw, n)
    if cut is None:
        return None, f"the kept copy holds fewer lines than the checkpoint covers ({n})"
    if reply.get("file") != file:
        return None, f"the reply is for {reply.get('file')!r}, not {file}"
    with tempfile.TemporaryDirectory() as tmp:
        copy, rep = Path(tmp) / file, Path(tmp) / "reply.json"
        copy.write_bytes(cut)
        rep.write_text(json.dumps(reply), encoding="utf-8")
        r = subprocess.run([str(bin_), "--file", str(copy), "--checkpoint", str(rep), "--key", key_id],
                           capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        return None, (r.stderr.strip() or f"{CHECKER} exited {r.returncode}")[:500]
    try:
        info = json.loads(r.stdout)
        redacted = [int(i) for i in info["redacted_lines"]]
    except (ValueError, KeyError, TypeError):
        return None, f"{CHECKER} said ok and printed {r.stdout[:200]!r}"
    kept = cut.split(b"\n")
    forged = [i for i in redacted if not 1 <= i <= n or not held_a_preimage(kept[i - 1], file)]
    if forged:
        return None, (f"line(s) {', '.join(map(str, forged))} carry a redaction marker but did not hold a payment "
                      "preimage (only a settlement observation's preimage in the chain is withheld)")
    return info, None


# Where the plugin keeps a payment preimage: a settlement observation it seals into its own chain (capsules-plugin
# v0.1.8 producer/capsule.rs, block SETTLEMENT_EXTENSION_KEY, member preimage). Every withheld preimage on the lab
# (live-16, live-17 and the loop#288 proof) sits there and nowhere else.
PREIMAGE_AT = ("model_attestation", "compute_attestation", "x-mesh-settlement-v1", "preimage")


def held_a_preimage(line, file):
    """Whether a copied line whose leaf digest the checker took from its x-loop-redacted marker is one that really
    held a preimage, as live.copy_redacted keeps such a line: a line of the chain (no other file holds one), a JSON
    object with the marker's 64-hex line_sha256, REDACTED exactly at PREIMAGE_AT beside a payment_hash, and REDACTED
    nowhere else. Its leaf is then the marker's digest, so the rest of the line is read as kept; anywhere else a
    marker would let a copy restate a line whose leaf is its own bytes."""
    if file != CHAIN:
        return False
    try:
        rec = json.loads(line)
        sha = rec.pop(live.REDACTION)["line_sha256"]
        block = rec
        for k in PREIMAGE_AT[:-1]:
            block = block[k]
        if block[PREIMAGE_AT[-1]] != live.REDACTED or not isinstance(block.get("payment_hash"), str):
            return False
        block = dict(block)
        del block[PREIMAGE_AT[-1]]
        rest = dict(rec, model_attestation=dict(rec["model_attestation"], compute_attestation=dict(
            rec["model_attestation"]["compute_attestation"], **{PREIMAGE_AT[2]: block})))
    except (ValueError, KeyError, TypeError, AttributeError):
        return False
    hexed = isinstance(sha, str) and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha)
    return hexed and live.REDACTED not in json.dumps(rest)


def listed_key(node_id):
    """The key the node's nodes/<node_id>.json listing gives (LOOP_NODES_DIR, else this checkout's nodes/; the twins
    run's fourth node's under nodes/twins/, lab/pack.py), or None."""
    root = Path(os.environ.get("LOOP_NODES_DIR", HERE.parent / "nodes"))
    for path in (root / f"{node_id}.json", root / "twins" / f"{node_id}.json"):
        try:
            key = json.loads(path.read_text(encoding="utf-8")).get("key_id") if node_id else None
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(key, str) and key:
            return key
    return None


def consoles():
    """{node name: console URL} from LOOP_NODE_CONSOLES; ValueError for an item that is not NAME=URL."""
    out = {}
    for item in [x for x in os.environ.get(CONSOLES, "").split(",") if x]:
        name, sep, url = item.partition("=")
        if not sep or not name or not url:
            raise ValueError(f"{CONSOLES} item {item!r} is not NAME=URL")
        out[name] = url.rstrip("/")
    return out


def keep_checked(console, src, dest, file, timeout=30, http=None):
    """Keep the node's `file` (its ledger/<file>, `src`) at `dest` with the node's checkpoint reply beside it
    (dest + KEPT_SUFFIX), as the plugin says to take a copy a reply covers: ask first, then read the file, and keep
    the first to_index + 1 lines (every line the file held before the ask). The file only grows, so those lines are
    the whole file at the checkpoint. With no console, or a refusal, the copy is the whole file as read and the
    beside-file says why there is no reply. A file the node does not hold is asked about too (its reply, were there
    one, would show a copy is missing). Returns what is kept beside the copy."""
    if http is None:
        sys.path.insert(0, str(HERE.parent / "scripts"))
        import daily_report
        http = daily_report.http
    asked = fetch(console, file, timeout, http) if console else \
        {"file": file, "reply": None, "refusal": f"no console for the node ({CONSOLES})"}
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shelf = Path(src).name == CHAIN and live.chain_stored(Path(src).parent)
    if shelf and not live.chain_present(Path(src).parent):  # the node's chain is on the shelf and no capsules binary
        asked = {**asked, "unread": f"no capsules binary to run chain-lines (PATH, or {live.CAPSULES_BIN})"}
    elif Path(src).is_file() or shelf:
        try:
            keep_copy(src, dest)
        except OSError as e:
            if not shelf:  # an unreadable flat file stops the step, as it always did (test_step05_deal)
                raise
            dest.unlink(missing_ok=True)  # a shelf node whose chain-lines fails: no copy, and the beside-file says why
            asked = {**asked, "unread": f"chain-lines failed: {e}"}
        if dest.exists():
            n = covered(asked["reply"]) if asked["reply"] else None
            cut = prefix(dest.read_bytes(), n) if n else None
            if cut is not None:  # else the copy is kept whole and fails the check: fewer lines than the reply covers
                dest.write_bytes(cut)
    Path(f"{dest}{KEPT_SUFFIX}").write_text(json.dumps(asked, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return asked


def judge(ledger_dir, files, key_id, name):
    """(errors, not_checked, proof) for the copies a step kept of node `name`'s `files` under `ledger_dir`, each beside
    its KEPT_SUFFIX file (keep_checked): each copy is the node's whole file at the checkpoint it signed, checked with
    loop-ledger-check under `key_id` (the key the node's nodes/ listing gives; None when it has none). A reply with no
    copy, a copy longer than its checkpoint, or a copy that does not check is an error. A refusal, no console, no
    listed key or no checker leaves the copy not shown complete (one not_checked entry), never a pass. `proof` is the
    kept files, relative to the ledger dir's parent's parent (the step's out)."""
    errs, open_, proof = [], [], []
    rel = lambda p: f"{Path(ledger_dir).parent.name}/{Path(ledger_dir).name}/{p.name}"  # noqa: E731
    for f in files:
        copy = Path(ledger_dir) / f
        beside = Path(f"{copy}{KEPT_SUFFIX}")
        try:
            asked = json.loads(beside.read_text(encoding="utf-8"))
            reply, refusal = asked["reply"], asked.get("refusal")
            if asked.get("file") != f:
                raise ValueError(f"it is for {asked.get('file')!r}")
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
            errs.append(f"{f}{KEPT_SUFFIX} is missing or not readable ({type(e).__name__}: {e})")
            continue
        proof.append(rel(beside))
        if copy.is_file():
            proof.append(rel(copy))
        if asked.get("unread") and not copy.is_file():  # our side could not read the node's chain: a gap, not a fault
            open_.append((f"{f}: the node's chain was not read ({asked['unread']})", TOOLS_ISSUE))
            continue
        if reply is None:
            # A file the step found no complete line of (or no file) has nothing to show complete; the plugin
            # refuses an ask for it too. That such a file held none is the step's reading (step 08 names it).
            if copy.is_file() and b"\n" in copy.read_bytes():
                open_.append((f"{f}: {refusal}", CONSOLE_ISSUE if "no console" in str(refusal) else ISSUE))
            continue
        if not copy.is_file():
            errs.append(f"{f}: the node signed a checkpoint over it, but no copy is kept")
            continue
        if key_id is None:
            open_.append((f"{f}: the node has no nodes/ listing with a key to check its checkpoint under", live.ISSUE))
            continue
        raw = copy.read_bytes()
        try:
            info, why = check(raw, reply, key_id, f)
        except FileNotFoundError:
            open_.append((f"{f}: {NOT_INSTALLED}", TOOLS_ISSUE))
            continue
        if why is None and len(raw) != len(prefix(raw, covered(reply))):
            why = f"the copy holds lines past the {covered(reply)} its checkpoint covers"
        if why:
            errs.append(f"{f}: the node's ledger checkpoint does not show the copy whole: {why}")
    # One entry per owner of what is open, in the order first met.
    nc = [{"what": f"{name}: each copied ledger file is the node's whole file",
           "why": "; ".join(w for w, i in open_ if i == issue), "issue": issue}
          for issue in dict.fromkeys(i for _, i in open_)]
    return errs, nc, proof
