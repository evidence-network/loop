"""Signed rehearsal stand-ins for step 04 Admit: Ed25519 over canonical JSON, with openssl.

A stand-in is {"body": {...}, "key": <raw Ed25519 public key, hex>, "sig": <hex>}:
the signature is over the body's canonical form (sorted keys, no whitespace,
ASCII). The stand-ins are the rehearsal's shapes for inputs other steps own
(population/rehearsal/step04/README.md); they are signed with TEST keys only.
"""
import base64
import json
import subprocess
import tempfile
from pathlib import Path

SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")


def canonical(body):
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def pub_hex(pem_path):
    """The raw Ed25519 public key of a PKCS#8 private or SPKI public PEM, hex."""
    text = Path(pem_path).read_text()
    args = ["openssl", "pkey", "-in", str(pem_path), "-pubout", "-outform", "DER"]
    if "PUBLIC KEY" in text:
        args.insert(2, "-pubin")
    der = subprocess.run(args, capture_output=True, check=True).stdout
    if len(der) != 44 or der[:12] != SPKI_PREFIX:
        raise ValueError(f"{pem_path}: not an Ed25519 key")
    return der[12:].hex()


def seed_hex(pem_path):
    """The 32-byte seed of a PKCS#8 Ed25519 private key, hex (the seed file capsulectl reads)."""
    der = subprocess.run(["openssl", "pkey", "-in", str(pem_path), "-outform", "DER"],
                         capture_output=True, check=True).stdout
    return der[-32:].hex()


def sign(body, key_pem):
    """A stand-in: body signed by the private key at key_pem."""
    with tempfile.TemporaryDirectory() as d:
        msg = Path(d) / "msg"
        msg.write_bytes(canonical(body))
        sig = subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", str(key_pem), "-rawin", "-in", str(msg)],
                             capture_output=True, check=True).stdout
    return {"body": body, "key": pub_hex(key_pem), "sig": sig.hex()}


def verify(doc, key=None):
    """(ok, why): doc's signature holds over its body under doc["key"], and doc["key"] is `key` when given."""
    if not (isinstance(doc, dict) and isinstance(doc.get("body"), dict)
            and isinstance(doc.get("key"), str) and isinstance(doc.get("sig"), str)):
        return False, "not a signed stand-in {body, key, sig}"
    if key is not None and doc["key"] != key:
        return False, f"signed by {doc['key'][:16]}…, not by {key[:16]}…"
    try:
        raw, sig = bytes.fromhex(doc["key"]), bytes.fromhex(doc["sig"])
    except ValueError:
        return False, "key or sig is not hex"
    if len(raw) != 32 or len(sig) != 64:
        return False, "key or sig has the wrong length"
    pem = "-----BEGIN PUBLIC KEY-----\n" + base64.b64encode(SPKI_PREFIX + raw).decode() + "\n-----END PUBLIC KEY-----\n"
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "pub.pem").write_text(pem)
        (Path(d) / "msg").write_bytes(canonical(doc["body"]))
        (Path(d) / "sig").write_bytes(sig)
        r = subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(Path(d) / "pub.pem"), "-rawin",
                            "-in", str(Path(d) / "msg"), "-sigfile", str(Path(d) / "sig")],
                           capture_output=True, text=True)
    return (True, "signature holds") if r.returncode == 0 else (False, "signature does not hold")
