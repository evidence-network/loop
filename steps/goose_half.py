"""G1's check of a Goose deal half, for the steps that read one (04 and 05, loop#330).

verify() runs scripts/goose_verify_half.py in the Goose venv, which calls goose_node.verify_half on the half's bytes with the
checkpoint key the caller pins. The halves are checked by G1's own verify_half, not by a second implementation. Standard
library only.
"""
import base64
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPKI_ED25519 = bytes.fromhex("302a300506032b6570032100")


def key_of(pem):
    """The raw Ed25519 public key of an SPKI PEM, as hex, or None."""
    der = base64.b64decode("".join(ln for ln in Path(pem).read_text().splitlines() if not ln.startswith("-----")))
    return der[12:].hex() if len(der) == 44 and der.startswith(SPKI_ED25519) else None


def verify(goose, half, key_id, checkpoint_key):
    """G1's verdict on the half file: {ok, error, deal_id, key_id, steps, ids, half_digest, final_record_id, checkpoint_digest, ...}.
    `goose` is population.json's `goose.verify` ({python, src})."""
    r = subprocess.run([goose["python"], "-B", str(ROOT / "scripts" / "goose_verify_half.py"), str(half), key_id, checkpoint_key],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       env=dict(os.environ, LOOP_GOOSE_SRC=goose["src"], PYTHONDONTWRITEBYTECODE="1"))
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "error": f"goose_verify_half exited {r.returncode}: {r.stderr.strip()[-200:]}"}
