#!/usr/bin/env python3
"""Sign a Nostr event (NIP-01) with BIP-340, with the standard library only.

The lab binds each node to a test Nostr key (lab/lab.sh bind). The plugin's join ceremony
(Mesh-LLM/capsules-plugin#121) hands back an unsigned kind-3374 event; this signs it. The
secret key is a lab test key kept in the lab directory; the signed event goes only to the
node's own console and is never published to a relay.

    nostr_sign.py pubkey KEYFILE        print the x-only public key (64 lowercase hex), making the key if absent
    nostr_sign.py sign KEYFILE          read the unsigned event as JSON on stdin, print it signed

The BIP-340 code is the reference algorithm (bips/bip-0340/reference.py); tests check it against
the BIP's test vectors.
"""
import hashlib
import json
import os
import sys

P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
G = (0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
     0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8)


def tagged_hash(tag, msg):
    t = hashlib.sha256(tag.encode()).digest()
    return hashlib.sha256(t + t + msg).digest()


def point_add(a, b):
    if a is None:
        return b
    if b is None:
        return a
    if a[0] == b[0] and a[1] != b[1]:
        return None
    if a == b:
        lam = 3 * a[0] * a[0] * pow(2 * a[1], P - 2, P) % P
    else:
        lam = (b[1] - a[1]) * pow(b[0] - a[0], P - 2, P) % P
    x = (lam * lam - a[0] - b[0]) % P
    return x, (lam * (a[0] - x) - a[1]) % P


def point_mul(point, k):
    out = None
    for i in range(256):
        if (k >> i) & 1:
            out = point_add(out, point)
        point = point_add(point, point)
    return out


def has_even_y(point):
    return point[1] % 2 == 0


def pubkey(seckey):
    if len(seckey) != 32:
        raise ValueError("the secret key must be 32 bytes")
    d = int.from_bytes(seckey, "big")
    if not 1 <= d < N:
        raise ValueError("the secret key is out of range")
    return point_mul(G, d)[0].to_bytes(32, "big")


def schnorr_sign(msg, seckey, aux):
    if len(seckey) != 32 or len(aux) != 32:
        raise ValueError("the secret key and aux must be 32 bytes")
    d0 = int.from_bytes(seckey, "big")
    if not 1 <= d0 < N:
        raise ValueError("the secret key is out of range")
    point = point_mul(G, d0)
    d = d0 if has_even_y(point) else N - d0
    t = (d ^ int.from_bytes(tagged_hash("BIP0340/aux", aux), "big")).to_bytes(32, "big")
    k0 = int.from_bytes(tagged_hash("BIP0340/nonce", t + point[0].to_bytes(32, "big") + msg), "big") % N
    if k0 == 0:
        raise ValueError("the nonce is zero")
    r = point_mul(G, k0)
    k = k0 if has_even_y(r) else N - k0
    e = int.from_bytes(tagged_hash("BIP0340/challenge", r[0].to_bytes(32, "big") + point[0].to_bytes(32, "big") + msg),
                       "big") % N
    return r[0].to_bytes(32, "big") + ((k + e * d) % N).to_bytes(32, "big")


def schnorr_verify(msg, pub, sig):
    if len(pub) != 32 or len(sig) != 64:
        return False
    x = int.from_bytes(pub, "big")
    if x >= P:
        return False
    y_sq = (pow(x, 3, P) + 7) % P
    y = pow(y_sq, (P + 1) // 4, P)
    if pow(y, 2, P) != y_sq:
        return False
    point = (x, y if y % 2 == 0 else P - y)
    r, s = int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")
    if r >= P or s >= N:
        return False
    e = int.from_bytes(tagged_hash("BIP0340/challenge", sig[:32] + pub + msg), "big") % N
    out = point_add(point_mul(G, s), point_mul(point, N - e))
    return out is not None and has_even_y(out) and out[0] == r


def event_id(event):
    """NIP-01: sha256 of [0, pubkey, created_at, kind, tags, content] in compact JSON."""
    body = [0, event["pubkey"], event["created_at"], event["kind"], event["tags"], event["content"]]
    return hashlib.sha256(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode()).digest()


def sign_event(event, seckey):
    if event["pubkey"] != pubkey(seckey).hex():
        raise ValueError("the event names another key than the lab's")
    digest = event_id(event)
    return {**event, "id": digest.hex(), "sig": schnorr_sign(digest, seckey, os.urandom(32)).hex()}


def load_key(path, make=False):
    """The 32-byte secret key in `path` (hex). With `make`, a new random one is written at mode 0600 if absent."""
    if make and not os.path.exists(path):
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        while True:
            key = os.urandom(32)
            if 1 <= int.from_bytes(key, "big") < N:
                break
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(key.hex() + "\n")
    if os.stat(path).st_mode & 0o077:
        raise ValueError(f"{path} is readable beyond its user: chmod 600 it")
    with open(path) as f:
        return bytes.fromhex(f.read().strip())


def main(argv):
    if len(argv) != 3 or argv[1] not in ("pubkey", "sign"):
        print(__doc__, file=sys.stderr)
        return 2
    key = load_key(argv[2], make=argv[1] == "pubkey")
    if argv[1] == "pubkey":
        print(pubkey(key).hex())
    else:
        print(json.dumps(sign_event(json.load(sys.stdin), key), separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
