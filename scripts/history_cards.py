#!/usr/bin/env python3
"""Read the history cards a commitment staples, for either profile.

Usage: history_cards.py COMMITMENT.json   (prints the cards as JSON)

    history_cards(doc) -> [{"stream", "derivation", "sha256", "witnesses": [...]}, ...]

- evidence-commitment/1 carries `history_cards`, one entry per evidence stream
  (one book's log), sorted by stream; each names its stream.
- evidence-commitment/0 carries one `history_card`; it reads as a one-entry
  list whose stream is the card's own log id.

The stream and witnesses of a card come from the card itself: the answer's
artifact is the history_card/1 result, `{"evidence_stream", "history": [{"checkpoint",
"receipts", "consistency_from_previous"}, ...]}`, and each witness receipt names its
witness in `witness`. A card with no receipt reads `witnesses: []` (unwitnessed).

This reads; it does not verify. scripts/check_commitment.py refuses a
commitment whose cards do not hold, and a card whose bytes cannot be decoded
reads here with stream None and no witnesses.
"""
import base64
import json
import sys

PROFILE_0 = "evidence-commitment/0"
PROFILE_1 = "evidence-commitment/1"


def _unb64url(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


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


def card_stream(answer):
    """The card's own log id: the evidence stream its result names, or None."""
    result = card_result(answer) if answer is not None else None
    stream = result.get("evidence_stream") if result else None
    return stream if isinstance(stream, str) and stream else None


def card_witnesses(answer):
    """The witnesses whose receipts the card carries, in first-seen order; [] when unwitnessed."""
    result = card_result(answer) if answer is not None else None
    history = result.get("history") if result else None
    seen = []
    for entry in history if isinstance(history, list) else []:
        receipts = entry.get("receipts") if isinstance(entry, dict) else None
        for receipt in receipts if isinstance(receipts, list) else []:
            witness = receipt.get("witness") if isinstance(receipt, dict) else None
            if isinstance(witness, str) and witness and witness not in seen:
                seen.append(witness)
    return seen


def entries(doc):
    """The raw staple entries, each with a `stream` (None when a /0 card names none)."""
    if not isinstance(doc, dict):
        return []
    if doc.get("profile") == PROFILE_1:
        cards = doc.get("history_cards")
        return [c for c in cards if isinstance(c, dict)] if isinstance(cards, list) else []
    card = doc.get("history_card")
    if not isinstance(card, dict):
        return []
    return [dict(card, stream=card_stream(card_answer(card)))]


def history_cards(doc):
    """[{stream, derivation, sha256, witnesses}] for an evidence-commitment/0 or /1 document."""
    out = []
    for e in entries(doc):
        out.append({"stream": e.get("stream"), "derivation": e.get("derivation"), "sha256": e.get("sha256"),
                    "witnesses": card_witnesses(card_answer(e))})
    return out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__.splitlines()[2])
    with open(sys.argv[1], encoding="utf-8") as f:
        print(json.dumps(history_cards(json.load(f)), indent=2))
