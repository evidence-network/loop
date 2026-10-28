#!/usr/bin/env python3
"""The mock rail: a deterministic local rail for the rehearsal population, and its adapter.

Usage: mock_rail.py declare                 print the adapter's declaration
       mock_rail.py verify RAIL_DIR         verify every settlement on RAIL_DIR's ledger

The rail issues settlement objects (README.md has the format), each signed by
the rail's TEST key, and keeps them in order on its public ledger
(ledger.jsonl). Nothing moves: the rail is a file. `issue()` makes an object;
`verify()` is the adapter a step calls on one, shaped like evidencebook's
foreign-evidence adapters (evidencebook v0.8.1 adapter/adapter.go): one report
per property, {property, status, method, detail}, with the five statuses of
agent-action-capsule interop-profile-v0 §7.1. A property is PASS only when its
check ran on the object's octets and held.

Standard library, plus the openssl command (3.0 or later) for Ed25519, through
scripts/check_commitment.py.
"""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import check_commitment as cc  # noqa: E402

TYPE = "mock-rail.settlement/v1"
DECLARATION = "mock-rail/v1"
FIELDS = ("type", "rail", "sequence", "settlement_id", "exchange_id", "payer", "payee", "currency",
          "amount_atomic", "decimals", "fee_atomic", "final", "issued_at", "sig")
PASS, FAIL, NOT_PRESENT, NOT_CHECKED, INCONCLUSIVE = "PASS", "FAIL", "NOT_PRESENT", "NOT_CHECKED", "INCONCLUSIVE"

METHODS = {
    "object_form": f"{TYPE}: JSON object with exactly its members, integer amounts, boolean final",
    "rail_signature": "Ed25519 over the JCS form without sig, under the key rail.json names (openssl pkeyutl)",
    "settlement_id": "settlement_id = SHA-256 of the JCS form without settlement_id and sig",
    "finality": "final is true in the signed object",
    "rail_fee": "fee_atomic > 0 in the signed object, on a rail whose declaration is fee_paying",
    "band": "(currency, decade): decade = floor(log10(amount_atomic / 10^decimals)), exact integer arithmetic",
}


def declare():
    """What the adapter checks, and what a passing check supports (the FR-19 shape, as data)."""
    return {
        "adapter": "mock-rail",
        "declaration": DECLARATION,
        "object": {"name": TYPE, "octets": "one line of the rail's ledger.jsonl, as written",
                   "issuer": "the rail, under the key rail.json names (a TEST key)", "natively_signed": True},
        "properties": [{"property": p, "method": m} for p, m in METHODS.items()],
        "supports": "the rail recorded a final settlement of amount_atomic units of currency from payer to payee "
                    "for exchange_id, charging fee_atomic",
        "not_supported": ["that any value moved: the mock rail is a file and nothing settles",
                          "who controls payer or payee: the relying party's controller declarations say that"],
    }


def body_digest(obj):
    """settlement_id: SHA-256 hex of the JCS form without settlement_id and sig."""
    return hashlib.sha256(cc.canonical({k: v for k, v in obj.items() if k not in ("settlement_id", "sig")})).hexdigest()


def issue(rail_key_pem, *, sequence, exchange_id, payer, payee, currency, amount_atomic, decimals, fee_atomic,
          final, issued_at):
    """A signed settlement object. Deterministic: Ed25519 signatures are."""
    obj = {"type": TYPE, "rail": "mock", "sequence": sequence, "exchange_id": exchange_id, "payer": payer,
           "payee": payee, "currency": currency, "amount_atomic": amount_atomic, "decimals": decimals,
           "fee_atomic": fee_atomic, "final": final, "issued_at": issued_at}
    obj["settlement_id"] = body_digest(obj)
    return cc.sign(obj, rail_key_pem)


def band(currency, amount_atomic, decimals):
    """The value band "<currency>:e<decade>", or None when the amount is not a positive integer."""
    if not (isinstance(amount_atomic, int) and not isinstance(amount_atomic, bool) and amount_atomic > 0
            and isinstance(decimals, int) and decimals >= 0 and isinstance(currency, str) and currency):
        return None
    decade = len(str(amount_atomic)) - 1 - decimals
    return f"{currency}:e{decade:+d}" if decade else f"{currency}:e0"


def rail_key(rail_dir, pub=None):
    """(declaration, SPKI DER of the key it names, read from `pub` or the path it names). Raises ValueError
    when the key is not the one the declaration names."""
    rail_dir = Path(rail_dir)
    decl = json.loads((rail_dir / "rail.json").read_text(encoding="utf-8"))
    spki = cc.spki_from_pem(Path(pub or rail_dir / decl["public_key"]).read_text())
    if cc.key_id_of(spki) != decl["key_id"]:
        raise ValueError("rail.json key_id is not the key it names")
    return decl, spki


def report(prop, status, detail):
    return {"property": prop, "status": status, "method": METHODS[prop], "detail": detail}


def verify(line, decl, spki):
    """The adapter: one report per property for one ledger line (bytes or str), as evidencebook's adapters do."""
    try:
        obj = json.loads(line)
    except ValueError as e:
        return [report("object_form", FAIL, f"not JSON: {e}")] + [
            report(p, NOT_PRESENT, "no object") for p in METHODS if p != "object_form"]
    form = (isinstance(obj, dict) and sorted(obj) == sorted(FIELDS) and obj["type"] == TYPE
            and all(isinstance(obj[k], int) and not isinstance(obj[k], bool)
                    for k in ("sequence", "amount_atomic", "decimals", "fee_atomic"))
            and isinstance(obj["final"], bool))
    if not form:
        got = sorted(obj) if isinstance(obj, dict) else type(obj).__name__
        return [report("object_form", FAIL, f"members {got}")] + [
            report(p, NOT_CHECKED, "object_form failed") for p in METHODS if p != "object_form"]
    out = [report("object_form", PASS, TYPE)]
    signed = cc.verify_sig(obj, spki)
    out.append(report("rail_signature", PASS if signed else FAIL, f"rail key {decl['key_id']}"))
    sid = body_digest(obj)
    out.append(report("settlement_id", PASS if sid == obj["settlement_id"] else FAIL,
                      f"stated {obj['settlement_id']}, computed {sid}"))
    out.append(report("finality", PASS if obj["final"] is True else FAIL, f"final {json.dumps(obj['final'])}"))
    fee = obj["fee_atomic"] > 0 and decl.get("fee_paying") is True
    out.append(report("rail_fee", PASS if fee else FAIL,
                      f"fee_atomic {obj['fee_atomic']}, rail fee_paying {json.dumps(decl.get('fee_paying'))}"))
    b = band(obj["currency"], obj["amount_atomic"], obj["decimals"])
    out.append(report("band", PASS if b else FAIL, b or f"amount_atomic {obj['amount_atomic']} is not positive"))
    return out


def held(reports):
    return all(r["status"] == PASS for r in reports)


def main(argv):
    if argv[1:] == ["declare"]:
        print(json.dumps(declare(), indent=2))
        return 0
    if len(argv) == 3 and argv[1] == "verify":
        decl, spki = rail_key(argv[2])
        bad = 0
        for i, line in enumerate((Path(argv[2]) / decl["ledger"]).read_bytes().splitlines(), 1):
            reports = verify(line, decl, spki)
            bad += not held(reports)
            print(json.dumps({"line": i, "reports": reports}))
        return 1 if bad else 0
    print(__doc__.strip().splitlines()[2], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
