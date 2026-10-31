"""A node's signed ledger checkpoint and the copy it covers, kept by a step and checked offline by 08 (loop#276).

The capsules plugin (v0.1.8, docs/LEDGER-CHECKPOINTS.md) signs a checkpoint over each ledger file it keeps and answers
`GET <console>/api/plugins/capsules/http/ledger-checkpoint?file=<name>` with the checkpoint and a range proof. A step
that re-derives from a copy of a ledger file keeps that reply beside the copy; a step that judges it checks the copy is
the whole file at the checkpoint with loop-ledger-check (steps/ledger-check.d), under the key the node lists (nodes/),
never the reply's own.

Only the files the plugin checkpoints can be shown complete (CHECKPOINTED); the rest keep their entry in loop#193.
"""
import os
import subprocess
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
    import json
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
    import json
    bin_ = checker()
    if bin_ is None:
        raise FileNotFoundError(CHECKER)
    n = covered(reply)
    cut = prefix(raw, n) if n else None
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
    if r.returncode == 0:
        try:
            return json.loads(r.stdout), None
        except ValueError:
            return None, f"{CHECKER} said ok and printed {r.stdout[:200]!r}"
    return None, (r.stderr.strip() or f"{CHECKER} exited {r.returncode}")[:500]
