#!/usr/bin/env python3
"""Export the reporter's public identity from its capsules plugin.

Usage: reporter_key.py [DATA_DIR] [--write FILE]   (default: ~/.local/share/capsules)

The reporter's key is its capsules plugin's Ed25519 key. The plugin makes it on
first start and keeps the private half under <DATA_DIR>/keys/; it never leaves
the node, and this script never reads it. This reads the two public files the
plugin writes, <DATA_DIR>/self-peer-id and <DATA_DIR>/keys/node-key.pub.pem,
and prints {"node_id", "key_id"} (or writes them into FILE, normally
reporter/reporter.json, for a pull request). Standard library only.

check_private_key() looks only at the private key's owner and mode, never its
bytes: by the reporter's key custody rule the key is readable by the
reporter's user only and has no backup.
"""
import base64
import json
import os
import re
import stat
import sys
from pathlib import Path

HEX64 = re.compile(r"^[0-9a-f]{64}$")
# DER SubjectPublicKeyInfo prefix of an Ed25519 key (RFC 8410): 32 key bytes follow.
ED25519_SPKI = bytes.fromhex("302a300506032b6570032100")


def key_from_pem(text):
    body = "".join(line for line in text.splitlines() if line and not line.startswith("-----"))
    der = base64.b64decode(body)
    if len(der) != 44 or not der.startswith(ED25519_SPKI):
        raise ValueError("not an Ed25519 public key in SubjectPublicKeyInfo form")
    return der[len(ED25519_SPKI):].hex()


def check_private_key(data_dir):
    """Refuse a private key another user could read or that this user does not own."""
    path = data_dir / "keys" / "node-key.pem"
    st = path.stat()
    if st.st_uid != os.getuid():
        raise ValueError(f"{path}: owned by uid {st.st_uid}, not this user ({os.getuid()})")
    if st.st_mode & 0o077:
        raise ValueError(f"{path}: mode {stat.filemode(st.st_mode)}, readable beyond this user (chmod 600 it)")


def read_identity(data_dir):
    node_id = (data_dir / "self-peer-id").read_text(encoding="utf-8").strip().lower()
    if not HEX64.fullmatch(node_id):
        raise ValueError(f"{data_dir / 'self-peer-id'}: not 64 hex")
    key_id = key_from_pem((data_dir / "keys" / "node-key.pub.pem").read_text(encoding="utf-8"))
    return {"node_id": node_id, "key_id": key_id}


def main(argv):
    args = argv[1:]
    target = None
    if "--write" in args:
        i = args.index("--write")
        target = Path(args[i + 1])
        del args[i:i + 2]
    data_dir = Path(args[0]).expanduser() if args else Path.home() / ".local/share/capsules"
    try:
        identity = read_identity(data_dir)
    except (OSError, ValueError) as e:
        print(e, file=sys.stderr)
        return 1
    if target is None:
        print(json.dumps(identity, indent=2))
        return 0
    reporter = json.loads(target.read_text(encoding="utf-8"))
    reporter.update(identity)
    target.write_text(json.dumps(reporter, indent=2) + "\n")
    print(f"{target}: node_id {identity['node_id']}, key_id {identity['key_id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
