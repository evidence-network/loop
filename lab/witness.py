#!/usr/bin/env python3
"""The lab's test witness: one Ed25519 key the lab host holds, apart from every node it witnesses.

    witness.py list  --key PEM --listing FILE --lab-dir DIR [--not-before RFC3339]
    witness.py serve --key PEM --cert PEM --tls-key PEM --port N --driver BIN [--at RFC3339]

`list` adds the key's public half to the listing (lab/witness/witnesses.json) as its own entry,
with the time from which its receipts count (`not_before`, default now). Entries are only ever
added: a rotated-out key keeps its entry, so the receipts it signed still verify. It refuses a key
that is any lab node's plugin key or controller key, and prints the key id.

`serve` answers a book's checkpoint delivery, `POST /checkpoints` with the raw COSE checkpoint, on
127.0.0.1 over HTTPS (the CLL witness client speaks only HTTPS). Step 02's driver signs each
receipt (`loop-step02-book lab-receipt`), stating the key id and the signing time; the private
key is read by that process only and never leaves the lab dir. --at signs every receipt with a
fixed time instead of the clock (tests).

This is one organization's test witness for its own lab, not a witness for the network.
"""
import argparse
import json
import os
import ssl
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

NAME = "lab-test-witness"
SPKI_ED25519 = bytes.fromhex("302a300506032b6570032100")
MAX_STATEMENT = 64 << 10


def public_hex(pem):
    """The raw Ed25519 public key of a private or public PEM, as 64 hex characters (openssl reads it)."""
    pubin = ["-pubin"] if "PUBLIC KEY" in Path(pem).read_text() else []
    der = subprocess.run(["openssl", "pkey", *pubin, "-in", str(pem), "-pubout", "-outform", "DER"],
                         capture_output=True, check=True).stdout
    if len(der) != 44 or not der.startswith(SPKI_ED25519):
        raise SystemExit(f"witness: {pem} is not an Ed25519 key")
    return der[12:].hex()


def node_keys(lab_dir):
    """{key id: where} for every lab node's plugin key and controller key under the lab dir."""
    lab_dir, keys = Path(lab_dir), {}
    for pem in sorted(lab_dir.glob("nodes/*/.local/share/capsules/keys/node-key.pub.pem")):
        keys[public_hex(pem)] = f"{pem.parents[4].name}'s plugin key"
    for pem in sorted(lab_dir.glob("controllers/*/controller-key.pem")):
        keys[public_hex(pem)] = f"{pem.parent.name}'s controller key"
    return keys


def utc(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def cmd_list(a):
    key_id = public_hex(a.key)
    clash = node_keys(a.lab_dir).get(key_id)
    if clash:
        raise SystemExit(f"witness: the witness key is {clash}; the witness must hold its own key")
    listing = Path(a.listing)
    doc = json.loads(listing.read_text())
    entries = doc.setdefault("witnesses", [])
    if any(e.get("key_id") == key_id for e in entries):
        print(key_id)
        return 0
    not_before = a.not_before or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    utc(not_before)  # refuse a time that does not parse
    entries.append({"name": NAME, "key_id": key_id, "not_before": not_before})
    listing.write_text(json.dumps(doc, indent=2) + "\n")
    print(key_id)
    return 0


def handler(driver, key, at):
    class Witness(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path.rstrip("/") != "/checkpoints":
                return self.answer(404, {"error": "not found"})
            n = int(self.headers.get("Content-Length") or 0)
            if not 0 < n <= MAX_STATEMENT:
                return self.answer(400, {"error": "a checkpoint statement is 1 to 65536 bytes"})
            with tempfile.NamedTemporaryFile(prefix="checkpoint-", suffix=".cose") as f:
                f.write(self.rfile.read(n))
                f.flush()
                signed = at if at is not None else int(time.time())
                r = subprocess.run([driver, "lab-receipt", "--key", key, "--statement", f.name, "--at", str(signed)],
                                   capture_output=True, text=True)
            if r.returncode != 0:
                return self.answer(400, {"error": r.stderr.strip()[-300:]})
            body = r.stdout.strip().encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def answer(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            sys.stderr.write("witness: " + fmt % args + "\n")
    return Witness


def cmd_serve(a):
    if os.stat(a.key).st_mode & 0o077:
        raise SystemExit(f"witness: {a.key} is readable beyond its user; chmod 600 it")
    at = int(utc(a.at).timestamp()) if a.at else None
    server = ThreadingHTTPServer(("127.0.0.1", a.port), handler(a.driver, a.key, at))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(a.cert, a.tls_key)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    print(f"witness: {NAME} {public_hex(a.key)} on https://127.0.0.1:{server.server_port}", flush=True)
    server.serve_forever()


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list")
    p.add_argument("--key", required=True)
    p.add_argument("--listing", required=True)
    p.add_argument("--lab-dir", required=True)
    p.add_argument("--not-before")
    p = sub.add_parser("serve")
    for f in ("--key", "--cert", "--tls-key", "--driver"):
        p.add_argument(f, required=True)
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--at")
    a = ap.parse_args(argv)
    return cmd_list(a) if a.cmd == "list" else cmd_serve(a)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
