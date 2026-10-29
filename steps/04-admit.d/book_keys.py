"""The buyer's TEST key, read with openssl in the forms capsulectl takes: the raw Ed25519 public key and seed."""
import subprocess
from pathlib import Path

SPKI_PREFIX = bytes.fromhex("302a300506032b6570032100")


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
