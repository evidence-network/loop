#!/usr/bin/env python3
"""Verify the history_card/1 a commitment staples with the pinned capsule-cli (`capsulectl verify --history`).

Usage: staple_verify.py COMMITMENT.json NODE_KEY_ID_HEX

Steps 03 and 12 call verify(). The binary is the capsule-cli release versions.lock pins, built by
steps/capsule-cli.setup.sh into ${LOOP_TOOLS:-~/.cache/evidence-network-loop}/capsule-cli/bin/capsulectl
(CAPSULE_CLI names another file). The keys come from the loop's own records, never from the staple: the
sender's key is the node key the carrier lists, and each card's checkpoint key is the one its commitment
entry names.

capsulectl reports a result per staple property. A commitment staples the reply octets only, so the whole
history never "answers a request" and capsulectl's overall verdict stays NOT_ACCEPTED (exit 3); the loop
reads the properties instead:

- every property in SIGNATURE_PROPERTIES PASS, each card judged under its own entry's checkpoint key: the staple's signatures, digest, anchors and responder verify;
- any property FAIL: a failure;
- a property that is not PASS and not FAIL (a key not given, a form capsulectl cannot evaluate, no binary):
  not checked, with capsulectl's own words as the reason.

caller_invariance (needs `--also-served` answers from another caller) and request_binding (a commitment
carries no request) are outside the check; verify() names them in "outside".
"""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_commitment as cc  # noqa: E402
import history_cards as hc  # noqa: E402

SIGNATURE_PROPERTIES = (
    "card_digest", "card_form", "checkpoint_anchor", "checkpoint_consistency", "checkpoint_key",
    "checkpoint_signature", "commitment_signature", "envelope_signature", "responder_trust", "stream_match",
    "subject_scope")
# commitment_anchor is left out as capsule-cli's own `find` leaves it out: the commitment's top-level checkpoint is
# read for its digest only (ECC §6 V3), so a card anchored at another checkpoint, as a second book's always is, is
# not a failure.
OUTSIDE = ("caller_invariance", "request_binding")
SETUP = "steps/capsule-cli.setup.sh"
TIMEOUT = 60


def binary():
    """The capsulectl of the pinned capsule-cli, or None when it is not installed."""
    path = Path(os.environ.get("CAPSULE_CLI") or
                Path(os.environ.get("LOOP_TOOLS", Path.home() / ".cache/evidence-network-loop"))
                / "capsule-cli/bin/capsulectl")
    return path if path.is_file() and os.access(path, os.X_OK) else None


def pinned_version():
    """The capsule-cli tag versions.lock pins, or None."""
    for line in (ROOT / "versions.lock").read_text().splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] == "capsule-cli":
            return parts[2]
    return None


def unchecked(why):
    return {"state": "NOT_CHECKED", "errors": [], "not_checked": [why], "outside": [], "report": None}


def verify(commitment_path, node_key_id, binary_path=None):
    """{"state": PASS|FAIL|NOT_CHECKED, "errors": [...], "not_checked": [...], "outside": [...], "report": dict|None}
    for the history_card/1 staples of the commitment file, sent by the node whose key id (hex) is given."""
    exe = binary_path or binary()
    if exe is None:
        return unchecked(f"the pinned capsule-cli {pinned_version()} is not installed ({SETUP})")
    try:
        doc = json.loads(Path(commitment_path).read_text())
        entries = hc.entries(doc)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        return unchecked(f"the commitment could not be read for capsule-cli ({e})")
    if not entries:
        return unchecked("the commitment staples no history_card/1")
    # One run per distinct checkpoint key, each card judged by the run that holds only its own entry's key: capsulectl
    # accepts a card signed by any key it is given, so a union would let one entry's key vouch for another's card.
    by_key = {}
    for e in entries:
        by_key.setdefault(cc.card_checkpoint_key(e), []).append(e.get("stream"))
    staples, toplevel = [], []
    for key, streams in sorted(by_key.items(), key=lambda kv: kv[0] or ""):
        cmd = [str(exe), "verify", "--history", str(commitment_path), "--counterparty-key", node_key_id]
        if key:
            cmd += ["--checkpoint-key", key]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT, stdin=subprocess.DEVNULL)
            report = json.loads(r.stdout)
        except (OSError, subprocess.TimeoutExpired, ValueError) as e:
            return unchecked(f"capsule-cli verify --history gave no report ({type(e).__name__}: {e})")
        if not isinstance(report, dict) or report.get("kind") != "history_card/1":
            why = (report.get("reason") or "; ".join(report.get("findings") or [])) if isinstance(report, dict) else ""
            return {**unchecked(f"capsule-cli does not evaluate this commitment's cards as history_card/1 "
                                f"({why or r.stderr.strip()})"), "report": report}
        # A single run covers every card; with several, each card is taken from the run holding its own key.
        staples += [x for x in report.get("staples") or [] if len(by_key) == 1 or x.get("stream") in streams]
        toplevel += [f for f in report.get("findings") or [] if not f.startswith("staples[") and "FAIL" in f]
    report = {"kind": "history_card/1", "staples": staples, "findings": toplevel,
              "capsule_cli_runs": len(by_key)}
    errors, missing, outside = list(toplevel), [], set()
    for staple in staples:
        props, name = staple.get("properties") or {}, staple.get("stream")
        for prop in SIGNATURE_PROPERTIES:
            state = props.get(prop)
            detail = next((f for f in staple.get("findings") or [] if f.startswith(prop)), "")
            if state == "FAIL":
                errors.append(f"history_card/1 of {name}: {detail or prop + ' FAIL'}")
            elif state != "PASS":
                missing.append(f"history_card/1 of {name}: {prop} {state or 'absent'}"
                               + (" (no checkpoint_key on its entry, or no key given)" if prop == "checkpoint_key" else ""))
        outside.update(p for p in OUTSIDE if props.get(p) != "PASS")
    if not staples:
        missing.append("capsule-cli reported no staple")
    state = "FAIL" if errors else "NOT_CHECKED" if missing else "PASS"
    return {"state": state, "errors": errors, "not_checked": missing, "outside": sorted(outside), "report": report}


def main(argv):
    if len(argv) != 3:
        print(__doc__.split("\n\n")[0], file=sys.stderr)
        return 2
    got = verify(argv[1], argv[2])
    print(json.dumps({k: v for k, v in got.items() if k != "report"}, indent=2))
    return {"PASS": 0, "FAIL": 1}.get(got["state"], 3)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
