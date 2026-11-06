#!/usr/bin/env python3
"""G1's own check of a counterparty's deal half, run for step 05 on the Goose rehearsal (loop#330, evidencebook#303).

Usage (in the Goose venv): goose_verify_half.py HALF KEY_ID CHECKPOINT_KEY_ID

Calls goose_node.verify_half on the half's bytes with the checkpoint key the caller pins (the counterparty's, as the node's
admission pins it): every step's Capsule passes Class 1 and its producer envelope verifies under the half's one key, each
step chains to the one before it and commits its payload, the profile's record and chain checks hold, and the half's
checkpoint is signed by the pinned key and commits exactly these steps. Prints one JSON object: the verdict (`ok`), and on
success the deal id, the signing key and what the half's close cites; the step compares those with the other bytes it holds.
Exit 0 when the half verifies, 1 when it does not (the reason is in `error`).
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["LOOP_GOOSE_SRC"]) / "python" / "examples" / "goose"))
import goose_node as gn  # noqa: E402


def main(half, key_id, checkpoint_key_id):
    try:
        got = gn.verify_half(json.loads(Path(half).read_text()), checkpoint_key_id)
    except (gn.Refused, OSError, ValueError) as e:
        print(json.dumps({"ok": False, "error": str(e)}))
        return 1
    ok = got["key_id"] == key_id
    print(json.dumps({"ok": ok, "error": None if ok else f"the half is signed by {got['key_id']}, not {key_id}",
                      **{k: got[k] for k in ("deal_id", "key_id", "close_half_digest", "final_record_id",
                                             "checkpoint_digest", "counterparty_checkpoint_cites")},
                      "steps": len(got["records"])}, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit("usage: goose_verify_half.py HALF KEY_ID CHECKPOINT_KEY_ID")
    sys.exit(main(*sys.argv[1:]))
