#!/usr/bin/env python3
"""Check node commitments: every nodes/commitments/<node_id>.json.

Usage: check_commitment.py [DIR]   (default: nodes/commitments)

A commitment is one signed object saying what a node can be asked and how
(steps/03-advertise.md is the schema). This refuses a commitment:

- that is missing a required field or has an unknown one, or whose fields
  have the wrong shape (the one optional field, controller_binding, is
  checked when present);
- that carries a count (COUNT RULE below);
- whose signature does not verify under the public key it names, over its
  RFC 8785 (JCS) canonical form without `sig`;
- whose policy digest is not the sha256 of the policy bytes at policy_uri, or
  whose policy is not signed by the same key (when policy_uri is a relative
  path or a file: URI; any other scheme is reported, not fetched);
- whose stapled history_card/1 or checkpoint bytes do not match their sha256,
  or whose staple is not presented by its own subject.

Two profiles verify. evidence-commitment/0 staples one card, `history_card`.
evidence-commitment/1 staples one card per evidence stream (one book's log),
`history_cards`: a list of {stream, community, derivation, sha256, bytes},
and optionally checkpoint_key, sorted by stream. `community` is one of
history_cards.COMMUNITIES; `checkpoint_key` is the SPKI of the stream's
checkpoint signer (DER, base64url; standard base64 is read too), so a
verifier can check that card's signature. Each /1 refusal of a card starts with its name, `history_cards.<name>:`
(the CARD_* constants below), so a reader can tell them apart.

/1 may also name its Nostr mailbox route, `ask.nostr`: exactly {mailbox_key,
relays}, both required. mailbox_key is the AAC Nostr mailbox binding v0 §3
mailbox key (secp256k1 x-only, 64 lowercase hex, a point on the curve);
relays is a non-empty list of distinct wss:// URLs, the relays the node reads
(§4.3). Each refusal starts with `ask.nostr.<name>:` (the NOSTR_* constants).

COUNT RULE. A commitment says what a node can answer, never what it has done.
It is refused when, anywhere in its own members (the opaque `bytes` of the
checkpoint and of each staple are not its members; they are signed artifacts it
carries whole):
  - a key's name contains a count word as a token (split on `_`, `-`, `.`
    and camelCase): count, counts, total, totals, tally, num, number,
    quantity, size, n, nb; or
  - a value is a JSON number, or a string of decimal digits only.
No field of the schema is a number: durations are ISO 8601 strings.

Signing and verifying use Ed25519 through the `openssl` command (3.0 or later).
Exit 0 = clean, 1 = refused.
"""
import base64
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

import history_cards as hc

PROFILE = hc.PROFILE_0
PROFILE_1 = hc.PROFILE_1
POLICY_PROFILE = "disclosure-policy/0"
STAPLE_PROFILE = "evidence-interop/0"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
# A /1 card's stream: a book's log id as join.sh accepts one, a plain name (step 12 makes file names from it).
STREAM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
# DER SubjectPublicKeyInfo prefix of an Ed25519 key (RFC 8410): 32 key bytes follow.
ED25519_SPKI = bytes.fromhex("302a300506032b6570032100")
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
DURATION = re.compile(r"^P(?=\d|T\d)(?:\d+D)?(?:T(?=\d)(?:\d+H)?(?:\d+M)?(?:\d+S)?)?$")
PREDICATE_CLASS = re.compile(r"^[a-z][a-z0-9_]*:[a-z0-9][a-z0-9_./-]*$")
TOKEN = re.compile(r"^[a-z0-9][a-z0-9_./:+-]*$")

# The members, and the members of each object member, exactly.
SCHEMA = {
    "profile": str,
    "node_id": str,
    "key_id": str,
    "spki": str,
    "issued_at": str,
    "ask": {"endpoint": str, "bindings": list},
    "answers": {"subjects": list, "derivations": list},
    "lcap": {"policy_digest": str, "policy_uri": str, "predicate_classes": list},
    "checkpoint": {"sha256": str, "bytes": str},
    "human_gated": bool,
    "answers_within": str,
    "history_card": {"derivation": str, "sha256": str, "bytes": str},
    "sig": {"alg": str, "canon": str, "value": str},
}
# Optional members, checked when present. controller_binding names the node
# key's binding to its operator's key by its digest alone (AAC Controller
# Binding v0 §4, §9.3): never the record, so never the operator's key.
OPTIONAL = {"controller_binding": {"digest": str}}
# /1: the one history_card becomes history_cards; every other member is /0's.
SCHEMA_1 = {("history_cards" if k == "history_card" else k): (list if k == "history_card" else v)
            for k, v in SCHEMA.items()}
CARD_MEMBERS = {"stream": str, "community": str, "derivation": str, "sha256": str, "bytes": str}
CARD_OPTIONAL = {"checkpoint_key": str}
OPAQUE = {("checkpoint", "bytes"), ("history_card", "bytes")}

# The named refusals of a /1 card.
CARD_EMPTY = "history_cards.empty"
CARD_SHAPE = "history_cards.malformed"
CARD_DERIVATION = "history_cards.derivation"
CARD_UNSORTED = "history_cards.unsorted"
CARD_BYTES = "history_cards.bytes_mismatch"
CARD_NOT_ANSWER = "history_cards.not_an_answer"
CARD_NOT_SUBJECT = "history_cards.not_its_subject"
CARD_ARTIFACT = "history_cards.artifact_digest"
CARD_STREAM = "history_cards.stream_mismatch"
CARD_DUPLICATE = "history_cards.duplicate_stream"
CARD_COMMUNITY = "history_cards.unknown_community"
CARD_CHECKPOINT_KEY = "history_cards.checkpoint_key"
COUNT_WORDS = {"count", "counts", "total", "totals", "tally", "num", "number", "quantity", "size", "n", "nb"}
DIGITS = re.compile(r"^\d+$")

# /1's optional Nostr mailbox route, ask.nostr (AAC Nostr mailbox binding v0, §3 and §4.3; its N-4 and N-5
# leave the member to this profile). The key and the relays travel together: one is no route without the other.
ASK_OPTIONAL_1 = {"nostr": dict}
NOSTR_MEMBERS = {"mailbox_key": str, "relays": list}
NOSTR_SHAPE = "ask.nostr.malformed"
NOSTR_MISSING = "ask.nostr.missing_member"
NOSTR_KEY = "ask.nostr.bad_mailbox_key"
NOSTR_RELAY = "ask.nostr.bad_relay"
NOSTR_DUPLICATE = "ask.nostr.duplicate_relay"
NOSTR_EMPTY = "ask.nostr.no_relays"
# secp256k1's field prime: a BIP-340 x-only key is an x below it whose x^3 + 7 is a square mod it.
SECP256K1_P = 2 ** 256 - 2 ** 32 - 977


# --- canonical form -------------------------------------------------------

def canonical(value):
    """RFC 8785 (JCS) bytes for the JSON this schema uses.

    Keys must be ASCII (so code-point order is JCS's UTF-16 order) and numbers
    integers within 2**53 (so their text is the ECMAScript form); anything else
    is refused rather than canonicalized wrongly.
    """
    def walk(v):
        if isinstance(v, dict):
            for k, x in v.items():
                if not (isinstance(k, str) and k.isascii()):
                    raise ValueError(f"key {k!r}: only ASCII keys are canonicalized here")
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, float) or (isinstance(v, int) and not isinstance(v, bool) and abs(v) >= 2 ** 53):
            raise ValueError(f"number {v!r}: only integers below 2**53 are canonicalized here")
        elif not (v is None or isinstance(v, (str, bool, int))):
            raise ValueError(f"{type(v).__name__} is not JSON")
    walk(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def signed_bytes(doc):
    return canonical({k: v for k, v in doc.items() if k != "sig"})


# --- keys and signatures ----------------------------------------------------

def b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def unb64url(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def spki_from_pem(text):
    body = "".join(line for line in text.splitlines() if line and not line.startswith("-----"))
    der = base64.b64decode(body)
    if len(der) != 44 or not der.startswith(ED25519_SPKI):
        raise ValueError("not an Ed25519 public key in SubjectPublicKeyInfo form")
    return der


def key_id_of(spki_der):
    return spki_der[len(ED25519_SPKI):].hex()


def _openssl(*args):
    return subprocess.run(["openssl", *args], capture_output=True)


def sign(doc, private_key_pem):
    """doc with `sig` set: Ed25519 by the key at private_key_pem over signed_bytes(doc)."""
    doc = {k: v for k, v in doc.items() if k != "sig"}
    with tempfile.TemporaryDirectory() as tmp:
        msg, out = Path(tmp) / "msg", Path(tmp) / "sig"
        msg.write_bytes(canonical(doc))
        r = _openssl("pkeyutl", "-sign", "-inkey", str(private_key_pem), "-rawin", "-in", str(msg), "-out", str(out))
        if r.returncode != 0:
            raise RuntimeError(f"openssl sign failed: {r.stderr.decode(errors='replace').strip()}")
        doc["sig"] = {"alg": "Ed25519", "canon": "jcs", "value": b64url(out.read_bytes())}
    return doc


def verify_sig(doc, spki_der):
    """True when doc's sig is Ed25519 by spki_der over signed_bytes(doc)."""
    sig = doc.get("sig")
    if not (isinstance(sig, dict) and sig.get("alg") == "Ed25519" and sig.get("canon") == "jcs"
            and isinstance(sig.get("value"), str)):
        return False
    try:
        raw = unb64url(sig["value"])
        msg_bytes = signed_bytes(doc)
    except ValueError:
        return False
    return verify_raw(msg_bytes, raw, spki_der)


def verify_raw(message, sig, spki_der):
    """True when `sig` (64 raw bytes) is Ed25519 by spki_der over `message`, by the openssl on PATH."""
    if len(sig) != 64:
        return False
    with tempfile.TemporaryDirectory() as tmp:
        msg, sigf, pub = Path(tmp) / "msg", Path(tmp) / "sig", Path(tmp) / "pub.pem"
        msg.write_bytes(message)
        sigf.write_bytes(sig)
        pub.write_text("-----BEGIN PUBLIC KEY-----\n" + base64.b64encode(spki_der).decode() + "\n-----END PUBLIC KEY-----\n")
        r = _openssl("pkeyutl", "-verify", "-pubin", "-inkey", str(pub), "-rawin", "-in", str(msg), "-sigfile", str(sigf))
        return r.returncode == 0


# --- the checks ---------------------------------------------------------------

def tokens(name):
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", name)
    return [t for t in re.split(r"[_.\-]+", name.lower()) if t]


def count_errors(doc, path=()):
    """Every place doc carries a count, per the COUNT RULE."""
    errs = []
    if isinstance(doc, dict):
        for k, v in doc.items():
            where = path + (k,)
            if where in OPAQUE or (len(where) == 3 and where[0] == "history_cards" and where[2] == "bytes"):
                continue
            if COUNT_WORDS & set(tokens(k)):
                errs.append(f"{'.'.join(where)}: a key named as a count; a commitment carries no counts")
            errs += count_errors(v, where)
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            errs += count_errors(v, path + (str(i),))
    elif isinstance(doc, (int, float)) and not isinstance(doc, bool):
        errs.append(f"{'.'.join(path)}: a number; a commitment carries no counts")
    elif isinstance(doc, str) and DIGITS.match(doc):
        errs.append(f"{'.'.join(path)}: a decimal number as a string; a commitment carries no counts")
    return errs


def schema_of(doc):
    return SCHEMA_1 if doc.get("profile") == PROFILE_1 else SCHEMA


def card_checkpoint_key(card):
    """The raw 32-byte checkpoint key (hex) a /1 entry names, or None (absent or not an Ed25519 SPKI)."""
    text = card.get("checkpoint_key")
    if not isinstance(text, str) or not re.match(r"^[A-Za-z0-9_+/-]+=*$", text):
        return None
    try:
        der = unb64url(text.replace("+", "-").replace("/", "_").rstrip("="))
    except ValueError:
        return None
    return key_id_of(der) if len(der) == 44 and der.startswith(ED25519_SPKI) else None


def card_field_errors(cards):
    """The shape of /1's history_cards: a non-empty list of {stream, derivation, sha256, bytes}, sorted."""
    if not cards:
        return [f"{CARD_EMPTY}: history_cards must hold at least one card"]
    errs = []
    for i, c in enumerate(cards):
        name = f"history_cards[{i}]"
        if not isinstance(c, dict):
            errs.append(f"{CARD_SHAPE}: {name} must be an object")
            continue
        shape = [f"{CARD_SHAPE}: missing {name}.{m}" for m in CARD_MEMBERS if m not in c]
        shape += [f"{CARD_SHAPE}: {name}.{m} must be a str" for m in {**CARD_MEMBERS, **CARD_OPTIONAL}
                  if m in c and not isinstance(c[m], str)]
        shape += [f"{CARD_SHAPE}: unknown field {name}.{x}"
                  for x in sorted(set(c) - set(CARD_MEMBERS) - set(CARD_OPTIONAL))]
        errs += shape
        if shape:
            continue
        if not STREAM.fullmatch(c["stream"]):
            errs.append(f"{CARD_SHAPE}: {name}.stream must be a plain log id (letters, digits, '.', '_', '-')")
        if not HEX64.match(c["sha256"]):
            errs.append(f"{CARD_SHAPE}: {name}.sha256 is not 64 lowercase hex")
        if c["derivation"] != "history_card/1":
            errs.append(f"{CARD_DERIVATION}: {name}.derivation must be history_card/1")
        if c["community"] not in hc.COMMUNITIES:
            errs.append(f"{CARD_COMMUNITY}: {name}.community {c['community']!r} is not one of "
                        f"{', '.join(hc.COMMUNITIES)}")
        if "checkpoint_key" in c and card_checkpoint_key(c) is None:
            errs.append(f"{CARD_CHECKPOINT_KEY}: {name}.checkpoint_key is not an Ed25519 SubjectPublicKeyInfo")
    if not errs:
        streams = [c["stream"] for c in cards]
        if streams != sorted(streams):
            errs.append(f"{CARD_UNSORTED}: history_cards must be sorted by stream")
    return errs


def is_xonly_key(text):
    """True when text is 64 lowercase hex naming a secp256k1 point's x (BIP-340 lift_x succeeds)."""
    if not HEX64.match(text):
        return False
    x = int(text, 16)
    if x >= SECP256K1_P:
        return False
    c = (pow(x, 3, SECP256K1_P) + 7) % SECP256K1_P
    y = pow(c, (SECP256K1_P + 1) // 4, SECP256K1_P)
    return y * y % SECP256K1_P == c


def relay_error(url):
    """Why url is not a relay URL (wss://host[:port][/path], printable ASCII, no credentials, query or fragment), or None."""
    if not (url.isascii() and url.isprintable() and not any(ch.isspace() for ch in url)):
        return "is not printable ASCII without spaces"
    u = urlparse(url)
    try:
        u.port
    except ValueError:
        return "has a bad port"
    if not url.startswith("wss://") or not u.hostname:
        return "is not a wss:// URL with a host"
    if u.username is not None or u.password is not None or u.query or u.fragment or u.params:
        return "carries credentials, a query or a fragment"
    return None


def nostr_errors(route):
    """/1's ask.nostr: exactly {mailbox_key, relays}, both present and well formed."""
    if not isinstance(route, dict):
        return [f"{NOSTR_SHAPE}: ask.nostr must be an object"]
    errs = [f"{NOSTR_MISSING}: missing ask.nostr.{m}; the mailbox key and the relays travel together"
            for m in NOSTR_MEMBERS if m not in route]
    errs += [f"{NOSTR_SHAPE}: ask.nostr.{m} must be a {t.__name__}"
             for m, t in NOSTR_MEMBERS.items() if m in route and not isinstance(route[m], t)]
    errs += [f"{NOSTR_SHAPE}: unknown field ask.nostr.{x}" for x in sorted(set(route) - set(NOSTR_MEMBERS))]
    if errs:
        return errs
    if not is_xonly_key(route["mailbox_key"]):
        errs.append(f"{NOSTR_KEY}: ask.nostr.mailbox_key is not a secp256k1 x-only key (64 lowercase hex)")
    relays = route["relays"]
    if not relays:
        errs.append(f"{NOSTR_EMPTY}: ask.nostr.relays must list at least one relay")
    seen = {}
    for i, url in enumerate(relays):
        why = relay_error(url) if isinstance(url, str) else "is not a string"
        if why:
            errs.append(f"{NOSTR_RELAY}: ask.nostr.relays[{i}] {why}")
        elif url in seen:
            errs.append(f"{NOSTR_DUPLICATE}: ask.nostr.relays[{i}] repeats ask.nostr.relays[{seen[url]}]")
        else:
            seen[url] = i
    return errs


def field_errors(doc):
    """Missing, unknown and malformed fields."""
    if not isinstance(doc, dict):
        return ["not a JSON object"]
    errs = []
    schema = schema_of(doc)
    sub_optional = {"ask": ASK_OPTIONAL_1} if doc.get("profile") == PROFILE_1 else {}
    for name, shape in {**schema, **OPTIONAL}.items():
        if name not in doc:
            if name not in OPTIONAL:
                errs.append(f"missing {name}")
            continue
        v = doc[name]
        if isinstance(shape, dict):
            if not isinstance(v, dict):
                errs.append(f"{name} must be an object")
                continue
            for sub, t in shape.items():
                if sub not in v:
                    errs.append(f"missing {name}.{sub}")
                elif not isinstance(v[sub], t):
                    errs.append(f"{name}.{sub} must be a {t.__name__}")
            optional = sub_optional.get(name, {})
            errs += [f"{name}.{sub} must be a {t.__name__}" for sub, t in optional.items()
                     if sub in v and not isinstance(v[sub], t)]
            errs += [f"unknown field {name}.{x}" for x in sorted(set(v) - set(shape) - set(optional))]
        elif not isinstance(v, shape):
            errs.append(f"{name} must be a {shape.__name__}")
    errs += [f"unknown field {x}" for x in sorted(set(doc) - set(schema) - set(OPTIONAL))]
    if doc.get("profile") not in (PROFILE, PROFILE_1):
        errs.append(f"profile must be {PROFILE} or {PROFILE_1}")
    if errs:
        return errs

    for name in ("node_id", "key_id"):
        if not HEX64.match(doc[name]):
            errs.append(f"{name} is not 64 lowercase hex")
    if not TIMESTAMP.fullmatch(doc["issued_at"]):
        errs.append("issued_at must be YYYY-MM-DDTHH:MM:SSZ")
    if not doc["ask"]["endpoint"].strip():
        errs.append("ask.endpoint must be non-empty")
    for name, lst in (("ask.bindings", doc["ask"]["bindings"]), ("answers.subjects", doc["answers"]["subjects"]),
                      ("answers.derivations", doc["answers"]["derivations"])):
        if not lst or not all(isinstance(x, str) and TOKEN.fullmatch(x) for x in lst):
            errs.append(f"{name} must be a non-empty list of names")
    if "nostr" in doc["ask"]:
        errs += nostr_errors(doc["ask"]["nostr"])
    if "controller_binding" in doc and not HEX64.fullmatch(doc["controller_binding"]["digest"]):
        errs.append("controller_binding.digest is not a sha256 (64 lowercase hex)")
    if not HEX64.match(doc["lcap"]["policy_digest"]):
        errs.append("lcap.policy_digest is not a sha256 (64 lowercase hex)")
    if not doc["lcap"]["policy_uri"].strip():
        errs.append("lcap.policy_uri must be non-empty")
    pcs = doc["lcap"]["predicate_classes"]
    if not pcs or not all(isinstance(x, str) and PREDICATE_CLASS.fullmatch(x) for x in pcs):
        errs.append("lcap.predicate_classes must be a non-empty list of <kind>:<source>")
    if not DURATION.fullmatch(doc["answers_within"]):
        errs.append("answers_within must be an ISO 8601 duration (PnD, PTnH, PTnM, PTnS)")
    if doc["profile"] == PROFILE_1:
        errs += card_field_errors(doc["history_cards"])
    elif doc["history_card"]["derivation"] != "history_card/1":
        errs.append("history_card.derivation must be history_card/1")
    if "history_card/1" not in doc["answers"]["derivations"]:
        errs.append("answers.derivations must include history_card/1, the derivation stapled")
    return errs


def key_errors(doc):
    """The key the commitment names: an Ed25519 SPKI matching key_id. Returns (errs, spki_der)."""
    try:
        der = base64.b64decode(doc["spki"], validate=True)
    except ValueError:
        return ["spki is not base64"], None
    if len(der) != 44 or not der.startswith(ED25519_SPKI):
        return ["spki is not an Ed25519 SubjectPublicKeyInfo"], None
    if key_id_of(der) != doc["key_id"]:
        return ["key_id is not the key in spki"], None
    return [], der


def _decoded(member, name):
    try:
        raw = base64.b64decode(member["bytes"], validate=True)
    except ValueError:
        return None, [f"{name}.bytes is not base64"]
    if hashlib.sha256(raw).hexdigest() != member["sha256"]:
        return None, [f"{name}.bytes do not match {name}.sha256"]
    return raw, []


def card_errors(member, name, key_id):
    """One stapled history_card/1: its bytes match, it is the plugin's answer format, presented by its subject.

    Returns (errors as (code, text), the decoded answer or None). Its signature
    is not verified here (no pinned verifier yet; see steps/03-advertise.md).
    """
    raw, errs = _decoded(member, name)
    if errs:
        return [(CARD_BYTES, e) for e in errs], None
    try:
        card = json.loads(raw)
    except ValueError:
        return [(CARD_NOT_ANSWER, f"{name}.bytes are not a JSON answer")], None
    if not isinstance(card, dict) or card.get("profile") != STAPLE_PROFILE:
        return [(CARD_NOT_ANSWER, f"{name} is not an {STAPLE_PROFILE} answer")], None
    found = []
    if card.get("derivation") != "history_card/1":
        found.append((CARD_DERIVATION, f"{name} answer's derivation is not history_card/1"))
    if card.get("responder") != key_id:
        found.append((CARD_NOT_SUBJECT, f"{name} is not presented by its subject: its responder is not key_id"))
    try:
        artifact = unb64url(card.get("artifact", ""))
    except ValueError:
        artifact = None
    if artifact is None or hashlib.sha256(artifact).hexdigest() != card.get("artifact_digest"):
        found.append((CARD_ARTIFACT, f"{name} answer's artifact_digest is not the sha256 of its artifact"))
    return found, card


def staple_errors(doc):
    """The stapled history_card/1 (/0), or each of history_cards (/1), and no stream stapled twice."""
    if doc["profile"] != PROFILE_1:
        return [text for _, text in card_errors(doc["history_card"], "history_card", doc["key_id"])[0]]
    errs, seen = [], {}
    for i, member in enumerate(doc["history_cards"]):
        name, stream = f"history_cards[{i}]", member["stream"]
        found, card = card_errors(member, name, doc["key_id"])
        if card is not None and hc.card_stream(card) != stream:
            found.append((CARD_STREAM, f"{name}.stream {stream} is not the card's own log id "
                                       f"({hc.card_stream(card) or 'none named'})"))
        if stream in seen:
            found.append((CARD_DUPLICATE, f"{name} staples stream {stream} again (first at history_cards[{seen[stream]}])"))
        seen.setdefault(stream, i)
        errs += [f"{code}: {text}" for code, text in found]
    return errs


def checkpoint_errors(doc):
    return _decoded(doc["checkpoint"], "checkpoint")[1]


def policy_errors(doc, policy_bytes, spki_der):
    """lcap.policy_digest is the sha256 of policy_bytes, a policy signed by the commitment's key."""
    if hashlib.sha256(policy_bytes).hexdigest() != doc["lcap"]["policy_digest"]:
        return ["lcap.policy_digest is not the sha256 of the policy bytes at lcap.policy_uri"]
    try:
        policy = json.loads(policy_bytes)
    except ValueError:
        return ["the policy at lcap.policy_uri is not JSON"]
    if not (isinstance(policy, dict) and policy.get("profile") == POLICY_PROFILE
            and policy.get("key_id") == doc["key_id"] and isinstance(policy.get("policy"), dict)):
        return [f"the policy is not a {POLICY_PROFILE} document for key_id"]
    if not verify_sig(policy, spki_der):
        return ["the policy's signature does not verify under the commitment's key"]
    return []


def verify(doc, fetch_policy):
    """Every check on a parsed commitment. fetch_policy(uri) returns the bytes, or None if it cannot fetch.

    Returns (errors, notes): errors refuse it; notes say what was not checked.
    """
    errs = field_errors(doc)
    if errs:
        return errs + count_errors(doc) if isinstance(doc, dict) else errs, []
    errs = count_errors(doc)
    kerrs, der = key_errors(doc)
    errs += kerrs
    if der is not None and not verify_sig(doc, der):
        errs.append("signature does not verify under spki")
    errs += checkpoint_errors(doc) + staple_errors(doc)
    notes = []
    policy_bytes = fetch_policy(doc["lcap"]["policy_uri"])
    if policy_bytes is None:
        notes.append(f"policy at {doc['lcap']['policy_uri']} not fetched; its digest is not checked")
    elif der is not None:
        errs += policy_errors(doc, policy_bytes, der)
    return errs, notes


def file_fetcher(base_dir):
    """Fetch a relative path (against base_dir) or a file: URI; None for any other scheme."""
    def fetch(uri):
        u = urlparse(uri)
        if u.scheme == "file":
            path = Path(url2pathname(u.path))
        elif u.scheme == "" and not uri.startswith("/"):
            path = Path(base_dir) / uri
        else:
            return None
        try:
            return path.read_bytes()
        except OSError:
            return b""
    return fetch


def check_file(path):
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [f"not valid JSON ({e})"], []
    errs, notes = verify(doc, file_fetcher(Path(path).parent))
    if isinstance(doc, dict) and isinstance(doc.get("node_id"), str) and Path(path).name != f"{doc['node_id']}.json":
        errs.append("file name must be <node_id>.json")
    return errs, notes


def main(argv):
    root = Path(argv[1] if len(argv) > 1 else "nodes/commitments")
    if not root.exists():
        print(f"{root}: no commitments")
        return 0
    files = [root] if root.is_file() else sorted(root.glob("*.json"))
    bad = 0
    for f in files:
        errs, notes = check_file(f)
        for n in notes:
            print(f"{f.name}: note: {n}")
        for e in errs:
            print(f"{f.name}: {e}", file=sys.stderr)
        bad += bool(errs)
    if bad:
        return 1
    print(f"{root}: {len(files)} commitment{'' if len(files) == 1 else 's'} ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
