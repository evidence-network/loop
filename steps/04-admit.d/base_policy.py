"""The loop's base admission policy (base-policy.json), evaluated over verified inputs.

evaluate() reads one deal's terms, the seller's history card, the adjudication
Results naming the seller, the controller declarations and the referees the
buyer trusts, and returns the decision's outcome (GO, ASK, LIMIT or NO), tier,
rows, derived asks and the facts it counted. Every signed input is verified
here (standin.verify) before it is read; one that does not verify is read as
absent and the row says so. This is the base policy, not admission-baseline@1
(action-state-group/evidencebook-skills#51).
"""
import datetime as dt
import hashlib
import json
from pathlib import Path

import standin

HERE = Path(__file__).resolve().parent
POLICY_FILE = HERE / "base-policy.json"
OUTCOMES = ("GO", "ASK", "LIMIT", "NO")


def load_policy(path=POLICY_FILE):
    data = Path(path).read_bytes()
    return json.loads(data), hashlib.sha256(data).hexdigest()


def utc(text):
    return dt.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def tier_of(policy, band):
    """The tier of a value band (currency, decade), or None above the last tier."""
    decade = band["decade"] + policy["decade_offset"][band["currency"]]
    for t in policy["tiers"]:
        if decade <= t["max_decade"]:
            return t["tier"]
    return None


def tier_index(policy, tier):
    return [t["tier"] for t in policy["tiers"]].index(tier)


def controllers(declarations):
    """[(controller key, set of keys it declares)] for each declaration whose signature holds."""
    out = []
    for d in declarations:
        ok, _ = standin.verify(d)
        body = d.get("body", {}) if isinstance(d, dict) else {}
        if ok and body.get("kind") == "controller_declaration" and body.get("controller") == d["key"]:
            out.append((d["key"], set(body.get("keys") or [])))
    return out


def same_controller(a, b, decls):
    return any(a in keys and b in keys for _, keys in decls)


def halves(card, seller, decls):
    """(held, self_dealt, notes): the card's deals with a counterparty half that verifies, split by controller."""
    held, self_dealt, notes = [], [], []
    for entry in card["body"].get("deals") or []:
        half = entry.get("counterparty_half")
        cp = entry.get("counterparty_key")
        ok, why = standin.verify(half, cp)
        body = (half or {}).get("body", {}) if isinstance(half, dict) else {}
        if not ok:
            notes.append(f"deal {entry.get('deal_id')}: counterparty half not counted ({why})")
        elif body.get("kind") != "deal_half" or body.get("deal_id") != entry.get("deal_id") or body.get("seller_key") != seller:
            notes.append(f"deal {entry.get('deal_id')}: counterparty half names another deal or seller")
        elif cp == seller:
            notes.append(f"deal {entry.get('deal_id')}: the counterparty is the seller's own key")
            self_dealt.append(entry["deal_id"])
        elif same_controller(seller, cp, decls):
            self_dealt.append(entry["deal_id"])
        else:
            held.append(entry["deal_id"])
    return held, self_dealt, notes


def adjudications(docs, seller, referees):
    """(counted, ignored): the Results against or for the seller signed by a trusted referee, and the rest."""
    counted, ignored = [], []
    for d in docs:
        ok, why = standin.verify(d)
        body = d.get("body", {}) if isinstance(d, dict) else {}
        result = body.get("result") or {}
        if not ok:
            ignored.append((d, why))
        elif d["key"] not in referees:
            ignored.append((d, "signed by a key that is not a referee the buyer trusts"))
        elif body.get("kind") != "adjudication_issued" or result.get("seller_key") != seller:
            ignored.append((d, "not an adjudication Result about this seller"))
        else:
            counted.append(d)
    return counted, ignored


def evaluate(policy, deal, card, adjudication_docs, declarations, referees, buyer):
    """The decision for one deal: {tier, outcome, limit_to, rows, asks, facts}."""
    seller = deal["seller_key"]
    tier = tier_of(policy, deal["band"])
    top = policy["tiers"][-1]["tier"]
    above_t0 = tier is None or tier_index(policy, tier) > 0
    decls = controllers(declarations)
    rows, caps = [], []  # caps: the tiers each LIMIT row allows

    def row(rid, verdict, level, detail):
        rows.append({"id": rid, "verdict": verdict, "level": level, "detail": detail})

    card_ok, card_why = (standin.verify(card, seller) if card is not None else (False, "no card"))
    if card_ok and card["body"].get("derivation") != "history_card/1":
        card_ok, card_why = False, "not a history_card/1"
    if card_ok:
        row("card", "met", None, "signed by the seller's key")
    elif above_t0:
        row("card", "unmet", "LIMIT", card_why)
        caps.append(policy["tiers"][0]["tier"])
    else:
        row("card", "not_required", None, f"optional at T0 ({card_why})")

    held, self_dealt, notes = halves(card, seller, decls) if card_ok else ([], [], [])
    if card_ok:
        age = (utc(deal["offered_at"]) - utc(card["body"]["checkpoint_at"])).total_seconds()
        if 0 <= age <= policy["card_fresh_within_seconds"]:
            row("fresh", "met", None, f"checkpoint {int(age)} s before the offer")
        else:
            row("fresh", "unmet", "ASK", f"checkpoint {int(age)} s before the offer")
    else:
        row("fresh", "not_applicable", None, "no card to read")

    if not above_t0:
        row("no_half", "not_applicable", None, "T0")
    elif held:
        row("no_half", "met", None, f"{len(held)} claims held")
    else:
        row("no_half", "unmet", "LIMIT", "no verified counterparty half outside the seller's controller")
        caps.append(policy["tiers"][0]["tier"])

    if tier is None:
        row("claims_held", "unmet", "LIMIT", f"the band is above {top}")
        caps.append(top)
    elif above_t0:
        need = policy["tiers"][tier_index(policy, tier)]["claims_held"]
        if len(held) >= need:
            row("claims_held", "met", None, f"{len(held)} held of {need} needed; {len(self_dealt)} self-dealt not counted")
        else:
            reach = [t["tier"] for t in policy["tiers"] if t["claims_held"] <= len(held)][-1]
            row("claims_held", "unmet", "LIMIT",
                f"{len(held)} held of {need} needed; {len(self_dealt)} self-dealt not counted")
            caps.append(reach)
    else:
        row("claims_held", "not_applicable", None, "T0")

    counted, ignored = adjudications(adjudication_docs, seller, referees)
    against = [d for d in counted if d["body"]["result"].get("outcome") == "against_seller"]
    with_us = [d for d in against if d["body"]["result"].get("buyer_key") == buyer]
    if with_us:
        row("disputes", "unmet", "NO", f"{len(with_us)} adjudications against the seller in deals with this buyer")
    elif against:
        row("disputes", "unmet", "LIMIT", f"{len(against)} adjudications against the seller in deals with others")
        caps.append(policy["tiers"][0]["tier"])
    else:
        row("disputes", "met", None, f"{len(counted)} adjudications read, none against the seller")

    levels = {r["level"] for r in rows if r["verdict"] == "unmet"}
    outcome = next((o for o in policy["resolution"] if o in levels), "GO")
    limit_to = min(caps, key=lambda t: tier_index(policy, t)) if outcome == "LIMIT" else None
    asks = []
    if outcome == "ASK":
        asks = [dict(policy["ask"], to=seller)]
    return {
        "tier": tier or f"above {top}",
        "outcome": outcome,
        "limit_to": limit_to,
        "rows": rows,
        "asks": asks,
        "claims_held": len(held),
        "self_dealt_excluded": len(self_dealt),
        "facts": {"held": held, "self_dealt": self_dealt, "notes": notes,
                  "adjudications_counted": [hashlib.sha256(standin.canonical(d)).hexdigest() for d in counted],
                  "adjudications_against": [hashlib.sha256(standin.canonical(d)).hexdigest() for d in against],
                  "adjudications_not_counted": [why for _, why in ignored]},
    }
