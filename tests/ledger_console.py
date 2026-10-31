"""A stand-in for each live node's ledger-checkpoint route and for loop-ledger-check, for the live tests of steps 04,
05 and 09 (loop#288).

The fake console answers `<console>/api/plugins/capsules/http/ledger-checkpoint?file=<f>` the way the capsules plugin
does, from the node's ledger file as it is when asked: a reply whose range covers every complete line, under the key
the node's nodes/ listing gives. It signs nothing; instead the reply lists each line's leaf digest (`x-test-leaves`),
and the stub checker (LOOP_LEDGER_CHECK_BIN) holds a copy to those digests, the key and the line count, as the real
checker holds it to the signed root. The real checker's cryptography is steps/ledger-check.d's own tests and the
fixture tests in tests/test_ledger_proof.py.

    env(population_dir, lab_dir, nodes_dir)  ->  {LOOP_NODE_CONSOLES, LOOP_LEDGER_CHECK_BIN}
"""
import hashlib
import http.server
import json
import os
import re
import sys
import tempfile
import threading
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "steps"))
sys.path.insert(0, str(ROOT / "steps" / "09-dispute.d"))
import aac  # noqa: E402
import ledger_proof  # noqa: E402
import live  # noqa: E402

_TMP = tempfile.TemporaryDirectory(prefix="ledger-console-")
STUB = Path(_TMP.name) / "loop-ledger-check"
CUT = "2026-10-31T21:04:00Z"


def leaf(chain, line):
    """The leaf digest of a line as the stub checker takes it from a copy (the real checker's rule)."""
    try:
        rec = json.loads(line)
    except ValueError:
        rec = None
    if chain:
        return rec.get("capsule_id") if isinstance(rec, dict) else None
    marker = rec.get(live.REDACTION, {}).get("line_sha256") if isinstance(rec, dict) and \
        live.REDACTED.encode() in line else None
    return marker or hashlib.sha256(line).hexdigest()


STUB.write_text(f"""#!{sys.executable}
import json, sys
sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})
import ledger_console
a = dict(zip(sys.argv[1::2], sys.argv[2::2]))
copy, reply = open(a["--file"], "rb").read(), json.load(open(a["--checkpoint"]))
lines = copy.split(b"\\n")
if lines.pop() != b"":
    sys.exit(print("fail: the copy ends in a line with no newline", file=sys.stderr) or 1)
if reply["checkpoint"]["key_id"] != a["--key"]:
    sys.exit(print("fail: the checkpoint is signed by another key, not by the given key", file=sys.stderr) or 1)
chain = reply["file"] == "capsules.jsonl"
if [ledger_console.leaf(chain, l) for l in lines] != reply["x-test-leaves"]:
    sys.exit(print("fail: the copied lines are not the checkpointed leaves", file=sys.stderr) or 1)
redacted = [i + 1 for i, l in enumerate(lines) if ledger_console.leaf(False, l) != __import__("hashlib").sha256(l).hexdigest()]
print(json.dumps({{"ok": True, "file": reply["file"], "lines": len(lines), "cut": reply["checkpoint"]["timestamp"],
                  "key_id": a["--key"], "redacted_lines": redacted}}))
""")
STUB.chmod(0o755)


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        token, _, route = url.path.lstrip("/").partition("/")
        node = json.loads(urllib.parse.unquote(token))
        file = urllib.parse.parse_qs(url.query).get("file", [""])[0]
        path = Path(node["ledger"]) / file
        if node.get("refuse") or "/" + route != ledger_proof.ROUTE or not path.is_file():
            return self.answer(400, b"invalid request, ask again later")
        raw = path.read_bytes()
        lines = raw.split(b"\n")[:-1]  # complete lines only
        if not lines:
            return self.answer(400, b"no complete line yet")
        self.answer(200, json.dumps({
            "ledger_checkpoint": 1, "file": file,
            "leaf_digest": "capsule_id" if file == ledger_proof.CHAIN else "sha256-line",
            "checkpoint": {"key_id": node["key"], "timestamp": CUT, "log_id": "capsules/test"},
            "range_proof": {"v": 1, "kind": "range", "size": len(lines), "from_index": 0, "to_index": len(lines) - 1,
                            "witness": []},
            "x-test-leaves": [leaf(file == ledger_proof.CHAIN, l) for l in lines]}).encode())

    def answer(self, status, body):
        self.send_response(status)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


SERVER = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=SERVER.serve_forever, daemon=True).start()


def token(ledger, key, refuse=False):
    """A node's console path: which ledger it answers from, under which key, and whether it refuses every ask."""
    return urllib.parse.quote(json.dumps({"ledger": str(ledger), "key": key, "refuse": refuse}, sort_keys=True), safe="")


def env(population_dir, lab_dir=None, nodes_dir=None, refuse=()):
    """The environment a live step needs to ask each population node with a data_dir for its checkpoints: one
    console per node (its ledger dir, $LAB_DIR expanded from `lab_dir`, and the key nodes_dir/<node_id>.json lists,
    or for an unlisted node the key its data dir holds, which signs as the plugin would), and the stub checker. Nodes named in `refuse` get a console that refuses."""
    pop = json.loads((Path(population_dir) / "population.json").read_text())
    nodes_dir = Path(nodes_dir or os.environ.get("LOOP_NODES_DIR") or ROOT / "nodes")
    items = []
    for n in pop["nodes"]:
        d = n.get("data_dir")
        if not d:
            continue
        data = Path(re.sub(r"\$\{?LAB_DIR\}?", str(lab_dir or ""), d))
        ledger, key = data / "ledger", None
        for listing in (nodes_dir / f"{n.get('node_id')}.json", nodes_dir / "twins" / f"{n.get('node_id')}.json"):
            try:
                key = key or json.loads(listing.read_text()).get("key_id")
            except (OSError, ValueError):
                pass
        if key is None and (data / "keys" / "node-key.pub.pem").is_file():
            key = aac.key_id_of_pem(data / "keys" / "node-key.pub.pem")
        t = token(ledger, key, n["name"] in refuse)
        items.append(f"{n['name']}=http://127.0.0.1:{SERVER.server_port}/{t}")
    return {ledger_proof.CONSOLES: ",".join(items), "LOOP_LEDGER_CHECK_BIN": str(STUB)}
