#!/usr/bin/env python3
"""Read the history cards a commitment staples, for either profile, and the bound groups.

Usage: history_cards.py COMMITMENT.json   (prints the cards as JSON)

    history_cards(doc) -> [{"stream", "community", "derivation", "sha256", "witnesses": [...],
                            "stream_started", "has_history"}, ...]
    bound_groups(bindings_dir, lock, commitments) -> [{"controller_key_id", "subjects", "verifier"}, ...]

- evidence-commitment/1 carries `history_cards`, one entry per evidence stream
  (one book's log), sorted by stream; each names its stream and community.
- evidence-commitment/0 carries one `history_card`; it reads as a one-entry
  list whose stream is the card's own log id, in the `mesh-llm` community.

What a card says comes from the card itself. The answer's artifact is the
history_card/1 result, `{"evidence_stream", "history": [{"checkpoint",
"receipts", "consistency_from_previous"}, ...]}`:

- `witnesses`: the witnesses whose receipts it carries, each named in the
  receipt's RECEIPT_WITNESS member; [] when unwitnessed. That member name is
  provisional until a receipt producer exists.
- `stream_started`: the date (YYYY-MM-DD) of the stream's first checkpoint.
- `has_history`: whether the stream's latest checkpoint covers any record. A
  stream started afresh (new, or a new stream after a clear) reads false; the
  two are not told apart.

This reads; it does not verify. scripts/check_commitment.py refuses a
commitment whose cards do not hold, and scripts/check_binding.py a binding
verdict that does not. A card whose bytes cannot be decoded reads here with
stream None, no witnesses, stream_started None and has_history False.
"""
import base64
import json
import re
import sys
from pathlib import Path

PROFILE_0 = "evidence-commitment/0"
PROFILE_1 = "evidence-commitment/1"
# The communities a card may belong to (closed list). A /0 card is mesh-llm's.
COMMUNITIES = ("mesh-llm", "muse")
DEFAULT_COMMUNITY = "mesh-llm"
# The receipt member naming its witness. Provisional: no receipt producer exists yet.
RECEIPT_WITNESS = "witness"
# A book (capsulectl-book v0.8.1) holds this many log entries before its first record: the log's
# own first entry and the book's two opening entries. A checkpoint over no more covers no record.
EMPTY_BOOK_SIZE = 3


def _unb64url(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# --- a little CBOR, enough to read a checkpoint statement (COSE_Sign1) --------

def _cbor(buf, i=0):
    """(value, next index) of the CBOR item at buf[i]: ints, strings, arrays, maps, tags, simple values."""
    major, info = buf[i] >> 5, buf[i] & 0x1F
    i += 1
    if info < 24:
        arg = info
    elif info in (24, 25, 26, 27):
        n = 1 << (info - 24)
        arg, i = int.from_bytes(buf[i:i + n], "big"), i + n
    else:
        raise ValueError("indefinite or reserved CBOR length")
    if major == 0:
        return arg, i
    if major == 1:
        return -1 - arg, i
    if major in (2, 3):
        raw = bytes(buf[i:i + arg])
        if len(raw) != arg:
            raise ValueError("truncated CBOR string")
        return (raw if major == 2 else raw.decode("utf-8")), i + arg
    if major == 4:
        out = []
        for _ in range(arg):
            v, i = _cbor(buf, i)
            out.append(v)
        return out, i
    if major == 5:
        out = {}
        for _ in range(arg):
            k, i = _cbor(buf, i)
            v, i = _cbor(buf, i)
            out[k] = v
        return out, i
    if major == 6:
        return _cbor(buf, i)
    return {20: False, 21: True, 22: None}.get(arg), i


def checkpoint_payload(statement_hex):
    """The payload map of a checkpoint statement (hex COSE_Sign1), or None."""
    try:
        sign1, _ = _cbor(bytes.fromhex(statement_hex))
        payload, _ = _cbor(sign1[2])
    except (ValueError, TypeError, IndexError, KeyError, UnicodeDecodeError, AttributeError, RecursionError):
        return None  # RecursionError: tags or arrays nested past Python's limit read as unreadable
    return payload if isinstance(payload, dict) else None


# --- one card ---------------------------------------------------------------

def card_answer(member):
    """The decoded evidence-interop/0 answer a staple entry carries, or None."""
    try:
        answer = json.loads(base64.b64decode(member["bytes"], validate=True))
    except (KeyError, TypeError, ValueError):
        return None
    return answer if isinstance(answer, dict) else None


def card_result(answer):
    """The history_card/1 result inside an answer (its artifact, decoded), or None."""
    try:
        result = json.loads(_unb64url(answer["artifact"]))
    except (KeyError, TypeError, ValueError):
        return None
    return result if isinstance(result, dict) else None


def _history(answer):
    result = card_result(answer) if answer is not None else None
    history = result.get("history") if result else None
    return [e for e in history if isinstance(e, dict)] if isinstance(history, list) else []


def card_stream(answer):
    """The card's own log id: the evidence stream its result names, or None."""
    result = card_result(answer) if answer is not None else None
    stream = result.get("evidence_stream") if result else None
    return stream if isinstance(stream, str) and stream else None


def _entry_witnesses(entry):
    receipts = entry.get("receipts")
    return [r[RECEIPT_WITNESS] for r in receipts if isinstance(r, dict) and isinstance(r.get(RECEIPT_WITNESS), str)
            and r[RECEIPT_WITNESS]] if isinstance(receipts, list) else []


def card_witnesses(answer):
    """The witnesses whose receipts the card carries, in first-seen order; [] when unwitnessed."""
    seen = []
    for entry in _history(answer):
        for witness in _entry_witnesses(entry):
            if witness not in seen:
                seen.append(witness)
    return seen


def card_checkpoint_witnesses(answer):
    """[(payload or None, witnesses)] for each checkpoint the card lists, in order.

    Reads; it verifies neither the checkpoint's signature nor its receipts.
    """
    return list(zip(card_checkpoints(answer), (_entry_witnesses(e) for e in _history(answer))))


def card_checkpoints(answer):
    """The payload of each checkpoint the card lists, in order (None where unreadable)."""
    return [checkpoint_payload(e["checkpoint"]) if isinstance(e.get("checkpoint"), str) else None
            for e in _history(answer)]


def card_started(answer):
    """YYYY-MM-DD of the stream's first checkpoint, or None."""
    first = next(iter(card_checkpoints(answer)), None)
    issued = first.get("issued_at") if first else None
    return issued[:10] if isinstance(issued, str) and re.match(r"^\d{4}-\d{2}-\d{2}T", issued) else None


def card_has_history(answer):
    """True when the stream's latest checkpoint covers a record (log beyond an empty book's)."""
    sizes = [p.get("log_size") for p in card_checkpoints(answer) if p]
    sizes = [s for s in sizes if isinstance(s, int) and not isinstance(s, bool)]
    return bool(sizes) and max(sizes) > EMPTY_BOOK_SIZE


# --- a commitment's cards -----------------------------------------------------

def entries(doc):
    """The raw staple entries, each with `stream` (None when a /0 card names none) and `community`."""
    if not isinstance(doc, dict):
        return []
    if doc.get("profile") == PROFILE_1:
        cards = doc.get("history_cards")
        return [c for c in cards if isinstance(c, dict)] if isinstance(cards, list) else []
    card = doc.get("history_card")
    if not isinstance(card, dict):
        return []
    return [dict(card, stream=card_stream(card_answer(card)), community=DEFAULT_COMMUNITY)]


def history_cards(doc):
    """[{stream, community, derivation, sha256, witnesses, stream_started, has_history}] for /0 or /1."""
    out = []
    for e in entries(doc):
        answer = card_answer(e)
        out.append({"stream": e.get("stream"), "community": e.get("community"), "derivation": e.get("derivation"),
                    "sha256": e.get("sha256"), "witnesses": card_witnesses(answer),
                    "stream_started": card_started(answer), "has_history": card_has_history(answer)})
    return out


# --- bound groups -------------------------------------------------------------

def bound_groups(bindings_dir, lock, commitments):
    """The subjects that may be drawn as one: verdicts that are `verified` by the binding verifier `lock` pins.

    bindings_dir holds bindings/<controller_key_id>.json (binding-verdict/0);
    lock is the path of a versions.lock; commitments are the subjects'
    commitment documents. A verdict groups only when every subject it names
    has a commitment here whose controller_binding digest is the verdict's
    controller_key_id. A verdict scripts/check_binding.py refuses, and every
    `revoked` or `not_checked` one, never groups.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import check_binding as cb
    pins = cb.read_lock(lock)
    digests = {}
    for doc in commitments:
        binding = doc.get("controller_binding") if isinstance(doc, dict) else None
        if isinstance(binding, dict) and isinstance(doc.get("node_id"), str):
            digests[doc["node_id"]] = binding.get("digest")
    groups = []
    for path in sorted(Path(bindings_dir).glob("*.json")) if Path(bindings_dir).is_dir() else []:
        verdict, errs = cb.check_file(path, pins)
        if not errs and verdict["status"] == "verified" and cb.pinned(verdict["verifier"], pins) and \
                all(digests.get(s) == verdict["controller_key_id"] for s in verdict["subjects"]):
            groups.append({"controller_key_id": verdict["controller_key_id"], "subjects": verdict["subjects"],
                           "verifier": verdict["verifier"]})
    return groups


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__.splitlines()[2])
    with open(sys.argv[1], encoding="utf-8") as f:
        print(json.dumps(history_cards(json.load(f)), indent=2))
