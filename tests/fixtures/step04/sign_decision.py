"""Seal one admission decision as a lab node's plugin does, for step 04's live tests (run in steps/01-seal's venv).

Usage: <01-seal venv python> -I sign_decision.py CROSS_LANGUAGE KEY.pem < block.json > capsule.json

The capsule is decision-template.json (one real live record, findings/2026-10-31-live-8) with its
admission_decision replaced by the block on stdin, its capsule_id recomputed by the AAC Go verifier
(`cross_language seal`) and its producer envelope signed by KEY.pem (a rehearsal TEST key) with capsule-emit's
own signer, so `cross_language verify` and `signature` accept it under that key.
"""
import json
import subprocess
import sys
from pathlib import Path

from capsule_emit.signing import LocalKeypairSigner, sign_producer_envelope

verifier, key = sys.argv[1], sys.argv[2]
cap = json.loads((Path(__file__).resolve().parent / "live" / "decision-template.json").read_text())
cap["model_attestation"]["compute_attestation"]["admission_decision"] = json.loads(sys.stdin.read())
stripped = {k: v for k, v in cap.items() if k not in ("capsule_id", "signature", "key_id")}
cid = json.loads(subprocess.run([verifier, "seal"], input=json.dumps(stripped).encode(), capture_output=True,
                                check=True).stdout)["capsule_id"]
sig, kid = sign_producer_envelope(LocalKeypairSigner(key), cid)
print(json.dumps(dict(stripped, capsule_id=cid, signature=sig, key_id=kid), sort_keys=True))
