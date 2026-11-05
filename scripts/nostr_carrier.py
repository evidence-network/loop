#!/usr/bin/env python3
"""Read a node's commitment back from its Nostr announce event, and compare it with the git carrier's bytes.

The second carrier of step 03 (evidence-network/loop#305). The event is AAC's evidence-commitment
carriers binding v0 §5.3 (kind 34700, a placeholder: no NIP, no relay, no registry of ours); the
checks are its §6 V0 to V4. The verifier trusts only the commitment bytes, never the carrier
(AAC#541): an event is read for its octets, and the octets are verified on their own.

A recording is what a REQ returned from each relay, kept as JSON so a rehearsal needs no relay:

    {"read_at": "2026-11-05T00:00:00Z", "relays": {"wss://relay.example": [<event>, ...], ...}}

read_back(recording, git_bytes, node_id, verify_octets) -> dict:
    result   PASS, FAIL or NOT_CHECKED
    reasons  one named reason per failure, "nostr.<name>: why"
    events   what each relay's events came to: rejected (named reason), relayed, announced, superseded
    sha256   of the octets read back, when they are the git carrier's bytes (PASS)

Named reasons: nostr.no_events, nostr.event_form, nostr.event_id, nostr.event_signature,
nostr.digest_mismatch, nostr.octets_form, nostr.octets_invalid (the octets fail the step 03 checks),
nostr.no_mailbox_route (the git commitment names no ask.nostr), nostr.not_announced (only events by
other keys: relayed), nostr.relays_conflict (same issued_at, different octets), nostr.bytes_differ
(the current octets are not the git carrier's bytes). A superseded older commitment is flagged, not
failed. A node whose recording is absent is "not advertised on nostr" (the step decides that).
"""
import base64
import datetime
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lab"))
import nostr_sign as ns  # noqa: E402

KIND = 34700
T_VALUE = "evidence-commitment"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX128 = re.compile(r"^[0-9a-f]{128}$")
B64URL = re.compile(r"^[A-Za-z0-9_-]*$")
SKEW = datetime.timedelta(minutes=5)
TIME = "%Y-%m-%dT%H:%M:%SZ"


def tag_values(event, name):
    return [t[1] if len(t) > 1 else None for t in event["tags"] if isinstance(t, list) and t and t[0] == name]


def event_error(event):
    """(named reason, why) when the event is not a well-formed, validly signed announce event, else None (V0)."""
    if not isinstance(event, dict):
        return "nostr.event_form", "the event is not an object"
    for member, ok in (("id", lambda v: isinstance(v, str) and HEX64.fullmatch(v)),
                       ("pubkey", lambda v: isinstance(v, str) and HEX64.fullmatch(v)),
                       ("sig", lambda v: isinstance(v, str) and HEX128.fullmatch(v)),
                       ("created_at", lambda v: isinstance(v, int) and not isinstance(v, bool)),
                       ("kind", lambda v: v == KIND),
                       ("content", lambda v: isinstance(v, str) and B64URL.fullmatch(v)),
                       ("tags", lambda v: isinstance(v, list))):
        if member not in event or not ok(event[member]):
            return "nostr.event_form", f"{member} is missing or not the form kind {KIND} fixes"
    for name, check in (("d", lambda v: v is not None and HEX64.fullmatch(v)),
                        ("t", lambda v: v == T_VALUE), ("x", lambda v: v is not None and HEX64.fullmatch(v))):
        values = tag_values(event, name)
        if len(values) != 1 or not check(values[0]):
            return "nostr.event_form", f"needs exactly one well formed {name} tag"
    if len(tag_values(event, "r")) > 1:
        return "nostr.event_form", "has more than one r tag"
    if ns.event_id(event).hex() != event["id"]:
        return "nostr.event_id", "id is not the NIP-01 hash of the event"
    if not ns.schnorr_verify(bytes.fromhex(event["id"]), bytes.fromhex(event["pubkey"]), bytes.fromhex(event["sig"])):
        return "nostr.event_signature", "the BIP-340 signature does not verify under pubkey"
    return None


def octets_of(event):
    """The commitment octets the event carries inline, or None (empty content: Size, §5.3)."""
    if not event["content"]:
        return None
    return base64.urlsafe_b64decode(event["content"] + "=" * (-len(event["content"]) % 4))


def _issued_at(doc):
    try:
        return datetime.datetime.strptime(doc["issued_at"], TIME).replace(tzinfo=datetime.timezone.utc)
    except (ValueError, KeyError, TypeError):
        return None


def read_back(recording, git_bytes, node_id, verify_octets):
    """Compare what the node's relays hold with `git_bytes`, the git carrier's commitment bytes.

    `verify_octets(octets)` is the step 03 check of a commitment (V2, V3, key): it returns (document, errors).
    """
    events = {"rejected": [], "relayed": [], "announced": [], "superseded": []}
    out = {"result": "FAIL", "reasons": [], "events": events}
    relays = recording.get("relays") if isinstance(recording, dict) else None
    if not isinstance(relays, dict) or not any(isinstance(v, list) and v for v in relays.values()):
        out["reasons"].append("nostr.no_events: no relay returned an announce event for this node")
        return out
    try:
        read_at = datetime.datetime.strptime(recording["read_at"], TIME).replace(tzinfo=datetime.timezone.utc)
    except (ValueError, KeyError, TypeError):
        out["result"] = "NOT_CHECKED"
        out["reasons"].append("nostr.recording: read_at is missing, so a future-dated commitment cannot be told")
        return out

    try:
        git_route = (json.loads(git_bytes).get("ask") or {}).get("nostr")
        mailbox_key = git_route["mailbox_key"]
    except (ValueError, KeyError, TypeError, AttributeError):
        out["reasons"].append("nostr.no_mailbox_route: the git carrier's commitment names no ask.nostr, so no "
                              "event can be the node's announcement")
        return out

    valid = []  # (relay, event, octets, doc)
    for relay, found in sorted(relays.items()):
        for event in found if isinstance(found, list) else []:
            bad = event_error(event)
            if bad is None and tag_values(event, "d") != [node_id]:
                bad = "nostr.event_form", "its d tag is not this node's id"
            if bad is None:
                octets = octets_of(event)
                if octets is None:
                    out["result"] = "NOT_CHECKED"
                    out["reasons"].append(f"nostr.over_size: {relay} holds an event with no octets (an r tag); "
                                          "fetching them is not done here")
                    continue
                if hashlib.sha256(octets).hexdigest() != tag_values(event, "x")[0]:
                    bad = "nostr.digest_mismatch", "the x tag is not the sha256 of the octets"
            if bad:
                events["rejected"].append({"relay": relay, "id": event.get("id") if isinstance(event, dict) else None,
                                           "reason": bad[0], "why": bad[1]})
                continue
            doc, errs = verify_octets(octets)
            if doc is None:
                events["rejected"].append({"relay": relay, "id": event["id"], "reason": "nostr.octets_form",
                                           "why": "; ".join(errs)})
            elif errs:
                events["rejected"].append({"relay": relay, "id": event["id"], "reason": "nostr.octets_invalid",
                                           "why": "; ".join(errs)})
            elif event["pubkey"] != mailbox_key:
                events["relayed"].append({"relay": relay, "id": event["id"], "pubkey": event["pubkey"],
                                          "sha256": hashlib.sha256(octets).hexdigest()})
            else:
                valid.append((relay, event, octets, doc))

    if not valid:
        if events["relayed"] and not events["rejected"]:
            out["reasons"].append("nostr.not_announced: only events by keys other than the commitment's "
                                  f"mailbox key {mailbox_key} (relayed, not announced)")
        elif events["relayed"]:
            out["reasons"].append("nostr.not_announced: no valid event by the commitment's mailbox key "
                                  f"{mailbox_key}")
        if events["rejected"]:
            out["reasons"] += sorted({f"{r['reason']}: {r['relay']}: {r['why']}" for r in events["rejected"]})
        if not out["reasons"]:
            out["reasons"].append("nostr.no_events: no relay returned an announce event for this node")
        return out

    by_octets = {}
    for relay, event, octets, doc in valid:
        by_octets.setdefault(octets, {"doc": doc, "relays": []})["relays"].append(relay)
    for octets, v in by_octets.items():
        v["sha256"] = hashlib.sha256(octets).hexdigest()
        v["issued_at"] = _issued_at(v["doc"])
        events["announced"].append({"sha256": v["sha256"], "issued_at": v["doc"]["issued_at"],
                                    "relays": sorted(v["relays"])})
    events["announced"].sort(key=lambda a: (a["issued_at"], a["sha256"]))

    # V4: a commitment dated after the read plus the skew takes no part; the latest issued_at is current.
    live = {o: v for o, v in by_octets.items() if v["issued_at"] is not None and v["issued_at"] <= read_at + SKEW}
    for o, v in by_octets.items():
        if o not in live:
            events["rejected"].append({"relay": v["relays"][0], "id": None, "reason": "nostr.future_dated",
                                       "why": f"issued_at {v['doc']['issued_at']} is after the read plus the skew"})
    if not live:
        out["reasons"].append("nostr.future_dated: every announced commitment is dated after the read")
        return out
    latest = max(v["issued_at"] for v in live.values())
    tied = [o for o, v in live.items() if v["issued_at"] == latest]
    if len(tied) > 1:
        out["reasons"].append("nostr.relays_conflict: relays hold different commitments with the same issued_at "
                              f"{live[tied[0]]['doc']['issued_at']}: "
                              + ", ".join(f"{live[o]['sha256']} on {','.join(sorted(live[o]['relays']))}"
                                          for o in sorted(tied, key=lambda o: live[o]['sha256'])))
        return out
    current = tied[0]
    events["superseded"] = sorted(
        ({"sha256": v["sha256"], "issued_at": v["doc"]["issued_at"], "relays": sorted(v["relays"])}
         for o, v in live.items() if o != current), key=lambda a: a["sha256"])
    if current != git_bytes:
        out["reasons"].append(f"nostr.bytes_differ: the current announced commitment {live[current]['sha256']} "
                              f"(issued_at {live[current]['doc']['issued_at']}) is not the git carrier's bytes "
                              f"{hashlib.sha256(git_bytes).hexdigest()}")
        return out
    out["result"] = "PASS"
    out["sha256"] = live[current]["sha256"]
    out["relays"] = sorted(live[current]["relays"])
    return out
