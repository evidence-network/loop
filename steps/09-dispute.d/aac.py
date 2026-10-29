"""AAC capsules as the pinned capsules plugin seals them: capsule_id, and the producer envelope.

- capsule_id is the SHA-256 of the RFC 8785 (JCS) form of the capsule without
  `capsule_id`, `signature` and `key_id`.
- `signature` is a hex COSE_Sign1 (tag 18) over the 32 capsule_id bytes, with
  protected header {3: "application/agent-action-capsule-id", 4: <key id>,
  1: -8 (EdDSA)}, an empty unprotected header and no external AAD; `key_id` is
  the raw Ed25519 public key, hex.

tests/test_step09_dispute.py checks both against a verdict the plugin's own
tests sign (tests/fixtures/step09/referee-verdict.json, from the pinned commit).
Standard library, plus the openssl command (3.0 or later) for Ed25519.
"""
import base64
import decimal
import hashlib
import json
import math
import subprocess
import tempfile
from pathlib import Path

STRIPPED = ("capsule_id", "signature", "key_id")
CONTENT_TYPE = "application/agent-action-capsule-id"
ED25519_SPKI = bytes.fromhex("302a300506032b6570032100")
EDDSA = -8


# --- RFC 8785 ------------------------------------------------------------------

def _number(v):
    """The ECMAScript Number-to-String form of v (RFC 8785, the serialization of numbers)."""
    if isinstance(v, int):
        if abs(v) >= 2 ** 53:
            raise ValueError(f"integer {v} is outside what JCS represents exactly")
        return str(v)
    if not math.isfinite(v):
        raise ValueError(f"{v!r} is not JSON")
    if v == 0:
        return "0"
    sign, digits, exp = decimal.Decimal(repr(v)).normalize().as_tuple()
    s = "".join(map(str, digits))
    k, n = len(s), exp + len(s)  # digits s, scaled so that v = 0.s * 10**n
    if k <= n <= 21:
        out = s + "0" * (n - k)
    elif 0 < n <= 21:
        out = s[:n] + "." + s[n:]
    elif -6 < n <= 0:
        out = "0." + "0" * -n + s
    else:
        e = n - 1
        out = s[0] + ("." + s[1:] if k > 1 else "") + "e" + ("+" if e > 0 else "-") + str(abs(e))
    return ("-" if sign else "") + out


def _jcs(v):
    if isinstance(v, dict):
        items = sorted(v.items(), key=lambda kv: kv[0].encode("utf-16-be"))
        return "{" + ",".join(json.dumps(k, ensure_ascii=False) + ":" + _jcs(x) for k, x in items) + "}"
    if isinstance(v, list):
        return "[" + ",".join(_jcs(x) for x in v) + "]"
    if v is None or isinstance(v, bool):
        return json.dumps(v)
    if isinstance(v, (int, float)):
        return _number(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    raise ValueError(f"{type(v).__name__} is not JSON")


def canonical(value):
    return _jcs(value).encode("utf-8")


def capsule_id(capsule):
    """The capsule_id the capsule's own bytes give."""
    return hashlib.sha256(canonical({k: v for k, v in capsule.items() if k not in STRIPPED})).hexdigest()


# --- CBOR, the little COSE_Sign1 needs ------------------------------------------

def _head(major, n):
    if n < 24:
        return bytes([major << 5 | n])
    for size, ai in ((1, 24), (2, 25), (4, 26), (8, 27)):
        if n < 1 << (8 * size):
            return bytes([major << 5 | ai]) + n.to_bytes(size, "big")
    raise ValueError("too long")


def _enc(v):
    if isinstance(v, bytes):
        return _head(2, len(v)) + v
    if isinstance(v, str):
        b = v.encode()
        return _head(3, len(b)) + b
    if isinstance(v, int):
        return _head(0, v) if v >= 0 else _head(1, -1 - v)
    if isinstance(v, list):
        return _head(4, len(v)) + b"".join(map(_enc, v))
    if isinstance(v, dict):
        return _head(5, len(v)) + b"".join(_enc(k) + _enc(x) for k, x in v.items())
    raise ValueError(f"{type(v).__name__} is not encoded here")


def _dec(b, i=0):
    """(value, next index) for one CBOR item at b[i]: ints, byte and text strings, arrays, maps, tags."""
    major, ai = b[i] >> 5, b[i] & 31
    i += 1
    if ai < 24:
        n = ai
    elif ai in (24, 25, 26, 27):
        size = 1 << (ai - 24)
        n, i = int.from_bytes(b[i:i + size], "big"), i + size
        if i > len(b):
            raise ValueError("truncated")
    else:
        raise ValueError("indefinite lengths are not COSE_Sign1")
    if major == 0:
        return n, i
    if major == 1:
        return -1 - n, i
    if major in (2, 3):
        if i + n > len(b):
            raise ValueError("truncated")
        raw = bytes(b[i:i + n])
        return (raw if major == 2 else raw.decode()), i + n
    if major == 4:
        out = []
        for _ in range(n):
            x, i = _dec(b, i)
            out.append(x)
        return out, i
    if major == 5:
        out = {}
        for _ in range(n):
            k, i = _dec(b, i)
            out[k], i = _dec(b, i)
        return out, i
    if major == 6:
        x, i = _dec(b, i)
        return ("tag", n, x), i
    raise ValueError(f"CBOR major type {major} is not COSE_Sign1")


def _sig_structure(protected, payload):
    return _enc(["Signature1", protected, b"", payload])


# --- Ed25519 through openssl ----------------------------------------------------

def _pem(spki):
    return "-----BEGIN PUBLIC KEY-----\n" + base64.b64encode(spki).decode() + "\n-----END PUBLIC KEY-----\n"


def key_id_of_pem(pub_pem_path):
    """The raw Ed25519 key of an SPKI PEM file, hex."""
    text = Path(pub_pem_path).read_text()
    der = base64.b64decode("".join(ln for ln in text.splitlines() if ln and not ln.startswith("-----")))
    if len(der) != 44 or not der.startswith(ED25519_SPKI):
        raise ValueError(f"{pub_pem_path}: not an Ed25519 SPKI public key")
    return der[len(ED25519_SPKI):].hex()


def _ed25519_sign(msg, key_pem):
    with tempfile.TemporaryDirectory() as tmp:
        m, s = Path(tmp) / "m", Path(tmp) / "s"
        m.write_bytes(msg)
        r = subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", str(key_pem), "-rawin", "-in", str(m),
                            "-out", str(s)], capture_output=True)
        if r.returncode != 0:
            raise RuntimeError(f"openssl sign: {r.stderr.decode(errors='replace').strip()}")
        return s.read_bytes()


def _ed25519_verify(msg, sig, key_id):
    with tempfile.TemporaryDirectory() as tmp:
        m, s, p = Path(tmp) / "m", Path(tmp) / "s", Path(tmp) / "p.pem"
        m.write_bytes(msg)
        s.write_bytes(sig)
        p.write_text(_pem(ED25519_SPKI + bytes.fromhex(key_id)))
        r = subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(p), "-rawin", "-in", str(m),
                            "-sigfile", str(s)], capture_output=True)
        return r.returncode == 0


# --- seal and verify -------------------------------------------------------------

def seal(body, key_dir):
    """body sealed under the TEST key in key_dir (node-key.pem, node-key.pub.pem): key_id, capsule_id, signature."""
    key_dir = Path(key_dir)
    capsule = {k: v for k, v in body.items() if k not in STRIPPED}
    kid = key_id_of_pem(key_dir / "node-key.pub.pem")
    cid = capsule_id(capsule)
    protected = _enc({3: CONTENT_TYPE, 4: bytes.fromhex(kid), 1: EDDSA})
    payload = bytes.fromhex(cid)
    sig = _ed25519_sign(_sig_structure(protected, payload), key_dir / "node-key.pem")
    envelope = _head(6, 18) + _enc([protected, {}, payload, sig])
    capsule.update(capsule_id=cid, signature=envelope.hex(), key_id=kid)
    return capsule


def verify(capsule):
    """(signer key id or None, detail): the capsule_id recomputes, and its envelope verifies under the key it names."""
    if not isinstance(capsule, dict):
        return None, "not a JSON object"
    try:
        cid = capsule_id(capsule)
    except ValueError as e:
        return None, f"no canonical form: {e}"
    if capsule.get("capsule_id") != cid:
        return None, f"capsule_id {capsule.get('capsule_id')} does not recompute (the bytes give {cid})"
    try:
        raw = bytes.fromhex(capsule.get("signature") or "")
        env, end = _dec(raw)
        if end != len(raw):
            raise ValueError("trailing bytes")
        if isinstance(env, tuple):
            if env[1] != 18:
                raise ValueError(f"tag {env[1]} is not COSE_Sign1")
            env = env[2]
        if not (isinstance(env, list) and len(env) == 4 and isinstance(env[0], bytes) and isinstance(env[2], bytes)
                and isinstance(env[3], bytes)):
            raise ValueError("not a COSE_Sign1 array")
        protected, _, payload, sig = env
        hdr, end = _dec(protected)
        if end != len(protected) or not isinstance(hdr, dict):
            raise ValueError("protected header is not a map")
    except ValueError as e:
        return None, f"signature is not a COSE_Sign1 envelope: {e}"
    kid = hdr.get(4)
    if hdr.get(1) != EDDSA or not (isinstance(kid, bytes) and len(kid) == 32):
        return None, "protected header names no EdDSA key"
    if payload != bytes.fromhex(cid):
        return None, "the envelope signs another capsule_id"
    if kid.hex() != capsule.get("key_id"):
        return None, f"the envelope's key {kid.hex()} is not key_id {capsule.get('key_id')}"
    if len(sig) != 64 or not _ed25519_verify(_sig_structure(protected, payload), sig, kid.hex()):
        return None, f"the Ed25519 signature does not verify under {kid.hex()}"
    return kid.hex(), f"capsule_id recomputes; signed by {kid.hex()}"
