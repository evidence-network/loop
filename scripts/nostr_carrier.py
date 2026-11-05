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
    events   what each relay's events came to: rejected (named reason), announced (by the mailbox key the event's
             own commitment names), relayed (any other author, same octets), superseded (older than the current
             commitment), unread (no octets inline: an r tag)
    sha256   of the octets read back, when they are the git carrier's bytes (PASS)

Every commitment that verifies is ranked, whoever posted it (V4: arrival order and carrier never decide); the
author only labels it announced or relayed (§5.3). The newest valid commitment must be announced and must be the
git carrier's bytes. An event by the node's own key with no octets inline (an r tag) can only be fetched later:
it leaves the node NOT_CHECKED when it could change the outcome (its digest is one we hold no commitment for, or
it names the git bytes while a stale inline one is current), and it counts as the node's announcement of the git
bytes when an outsider posted the same octets. Outsiders' junk never turns that NOT_CHECKED into a FAIL.

Named reasons: nostr.no_events, nostr.event_form, nostr.event_id, nostr.event_signature, nostr.digest_mismatch,
nostr.octets_form, nostr.octets_invalid (the octets fail the checks: signature, fields, key, node_id; the policy
and staples are checked on the git bytes), nostr.future_dated (every commitment is dated after the read),
nostr.no_mailbox_route (the git commitment names no ask.nostr), nostr.not_announced (the current commitment has
only relayed events), nostr.relays_conflict (same issued_at, different octets), nostr.bytes_differ (the current
commitment is not the git carrier's bytes), nostr.over_size (NOT_CHECKED), nostr.recording (read_at missing or
malformed: NOT_CHECKED). A superseded older commitment is flagged, not failed. A node whose recording is absent
is "not advertised on nostr" (the step decides that).
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
    return [t[1] if len(t) > 1 and isinstance(t[1], str) else None
            for t in event["tags"] if isinstance(t, list) and t and t[0] == name]


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
    try:
        digest = ns.event_id(event).hex()
    except UnicodeError:  # a tag or content string no UTF-8 encoding can hold (a lone surrogate)
        return "nostr.event_form", "a string in the event cannot be encoded as UTF-8"
    if digest != event["id"]:
        return "nostr.event_id", "id is not the NIP-01 hash of the event"
    if not ns.schnorr_verify(bytes.fromhex(event["id"]), bytes.fromhex(event["pubkey"]), bytes.fromhex(event["sig"])):
        return "nostr.event_signature", "the BIP-340 signature does not verify under pubkey"
    return None


def octets_of(event):
    """The commitment octets the event carries inline, or None (empty content: Size, §5.3)."""
    if not event["content"]:
        return None
    return base64.urlsafe_b64decode(event["content"] + "=" * (-len(event["content"]) % 4))  # may raise binascii.Error


def _issued_at(doc):
    try:
        return datetime.datetime.strptime(doc["issued_at"], TIME).replace(tzinfo=datetime.timezone.utc)
    except (ValueError, KeyError, TypeError):
        return None


def read_back(recording, git_bytes, node_id, verify_octets):
    """Compare what the node's relays hold with `git_bytes`, the git carrier's commitment bytes.

    `verify_octets(octets)` is the step 03 check of a commitment (V2, V3, key): it returns (document, errors).
    """
    events = {"rejected": [], "relayed": [], "announced": [], "superseded": [], "unread": []}
    out = {"result": "FAIL", "reasons": [], "events": events}
    git_digest = hashlib.sha256(git_bytes).hexdigest()
    relays = recording.get("relays") if isinstance(recording, dict) else None
    if not isinstance(relays, dict) or not any(isinstance(v, list) and v for v in relays.values()):
        out["reasons"].append("nostr.no_events: no relay returned an announce event for this node")
        return out
    try:
        read_at = datetime.datetime.strptime(recording["read_at"], TIME).replace(tzinfo=datetime.timezone.utc)
        newest_allowed = read_at + SKEW
    except (ValueError, KeyError, TypeError, OverflowError):
        out["result"] = "NOT_CHECKED"
        out["reasons"].append("nostr.recording: read_at is missing or not YYYY-MM-DDTHH:MM:SSZ, so a future-dated "
                              "commitment cannot be told")
        return out

    try:
        git_doc = json.loads(git_bytes)
        git_key = git_doc["ask"]["nostr"]["mailbox_key"]
    except (ValueError, KeyError, TypeError, RecursionError):
        out["reasons"].append("nostr.no_mailbox_route: the git carrier's commitment names no ask.nostr, so no "
                              "event can be the node's announcement")
        return out

    by_octets = {}  # octets -> {"doc", "announced": [relays], "relayed": [relays]}
    for relay, found in sorted(relays.items()):
        for event in found if isinstance(found, list) else []:
            bad = event_error(event)
            if bad is None and tag_values(event, "d") != [node_id]:
                bad = "nostr.event_form", "its d tag is not this node's id"
            octets = None
            if bad is None:
                try:
                    octets = octets_of(event)
                except ValueError:  # binascii.Error is a ValueError: content is not base64url octets
                    bad = "nostr.event_form", "content is not base64url octets"
            if bad is None and octets is None:
                # No octets inline (an r tag). Only the node's own key can have posted the one that matters.
                (events["unread"] if event["pubkey"] == git_key else events["relayed"]).append(
                    {"relay": relay, "id": event["id"], "pubkey": event["pubkey"], "x": tag_values(event, "x")[0]})
                continue
            if bad is None and hashlib.sha256(octets).hexdigest() != tag_values(event, "x")[0]:
                bad = "nostr.digest_mismatch", "the x tag is not the sha256 of the octets"
            if bad:
                events["rejected"].append({"relay": relay, "id": event.get("id") if isinstance(event, dict) else None,
                                           "reason": bad[0], "why": bad[1]})
                continue
            doc, errs = verify_octets(octets)
            if doc is None:
                events["rejected"].append({"relay": relay, "id": event["id"], "reason": "nostr.octets_form",
                                           "why": "; ".join(errs)})
            elif errs or doc.get("node_id") != node_id:
                events["rejected"].append({"relay": relay, "id": event["id"], "reason": "nostr.octets_invalid",
                                           "why": "; ".join(errs) or "the commitment's node_id is not the event's d"})
            else:
                # §5.3 Attribution: the commitment the event carries names its own mailbox key.
                own = event["pubkey"] == ((doc.get("ask") or {}).get("nostr") or {}).get("mailbox_key")
                v = by_octets.setdefault(octets, {"doc": doc, "announced": [], "relayed": []})
                v["announced" if own else "relayed"].append(relay)

    def unread_reason():
        return (f"nostr.over_size: {events['unread'][0]['relay']} holds an event by the node's mailbox key with no "
                "octets (an r tag); fetching them is not done here")

    if not by_octets:
        if events["unread"]:
            # The node's own announcement sits behind an r tag, which an outsider's junk cannot change.
            out["result"] = "NOT_CHECKED"
            out["reasons"].append(unread_reason())
            return out
        out["reasons"] += sorted({f"{r['reason']}: {r['relay']}: {r['why']}" for r in events["rejected"]})
        if events["relayed"] and not out["reasons"]:
            out["reasons"].append("nostr.not_announced: no event by the node's mailbox key carries octets")
        elif not out["reasons"]:
            out["reasons"].append("nostr.no_events: no relay returned an announce event for this node")
        return out

    for v in by_octets.values():
        v["sha256"] = hashlib.sha256(next(o for o, w in by_octets.items() if w is v)).hexdigest()
        v["issued_at"] = _issued_at(v["doc"])
    # V4: a commitment dated after the read plus the skew takes no part; the latest issued_at is current.
    live = {o: v for o, v in by_octets.items() if v["issued_at"] is not None and v["issued_at"] <= newest_allowed}
    for o, v in by_octets.items():
        if o not in live:
            events["rejected"].append({"relay": (v["announced"] + v["relayed"])[0], "id": None,
                                       "reason": "nostr.future_dated",
                                       "why": f"issued_at {v['doc']['issued_at']} is after the read plus the skew"})
    if not live:
        if events["unread"]:  # the node's own r-tag event may name what is current: unproven, not failed
            out["result"] = "NOT_CHECKED"
            out["reasons"].append(unread_reason())
            return out
        out["reasons"].append("nostr.future_dated: every commitment is dated after the read")
        return out
    latest = max(v["issued_at"] for v in live.values())
    tied = [o for o, v in live.items() if v["issued_at"] == latest]
    for v in live.values():
        entry = {"sha256": v["sha256"], "issued_at": v["doc"]["issued_at"]}
        if v["announced"]:
            events["announced"].append({**entry, "relays": sorted(v["announced"])})
        if v["relayed"]:
            events["relayed"].append({**entry, "relays": sorted(v["relayed"])})
    events["announced"].sort(key=lambda a: (a["issued_at"], a["sha256"]))
    if len(tied) > 1:
        out["reasons"].append("nostr.relays_conflict: relays hold different commitments with the same issued_at "
                              f"{live[tied[0]]['doc']['issued_at']}: "
                              + ", ".join(f"{live[o]['sha256']} on {','.join(sorted(live[o]['announced'] + live[o]['relayed']))}"
                                          for o in sorted(tied, key=lambda o: live[o]['sha256'])))
        return out
    current = tied[0]
    events["superseded"] = sorted(
        ({"sha256": v["sha256"], "issued_at": v["doc"]["issued_at"], "relays": sorted(v["announced"] + v["relayed"])}
         for o, v in live.items() if o != current), key=lambda a: a["sha256"])
    cur = live[current]
    named_by_unread = any(u["x"] == git_digest for u in events["unread"])  # the node's own r-tag event names the git bytes
    if current != git_bytes:
        git_at = _issued_at(git_doc)
        if named_by_unread and git_at is not None and cur["issued_at"] <= git_at <= newest_allowed:
            # The git bytes may be what the node announced behind an r tag, and they are not older than the inline one.
            out["result"] = "NOT_CHECKED"
            out["reasons"].append(unread_reason())
            return out
        out["reasons"].append(f"nostr.bytes_differ: the current commitment {cur['sha256']} "
                              f"(issued_at {cur['doc']['issued_at']}) is not the git carrier's bytes {git_digest}")
        return out
    if any(u["x"] not in {v["sha256"] for v in live.values()} for u in events["unread"]):
        # An event by the node's key with octets we cannot read names a commitment we hold none of: it may be newer.
        out["result"] = "NOT_CHECKED"
        out["reasons"].append(unread_reason())
        return out
    if not cur["announced"] and not named_by_unread:
        out["reasons"].append("nostr.not_announced: the current commitment is only relayed, by keys other than the "
                              f"mailbox key its own commitment names ({', '.join(sorted(cur['relayed']))})")
        return out
    out["result"] = "PASS"
    out["sha256"] = cur["sha256"]
    out["relays"] = sorted(cur["announced"] or cur["relayed"])
    return out
