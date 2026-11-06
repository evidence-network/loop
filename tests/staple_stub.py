"""A stand-in for the pinned capsule-cli, so the step tests need no network or Go toolchain and do not depend on
the fixtures' synthetic cards (the real verifier refuses those: their checkpoints are not the cards' anchors).

    import staple_stub; staple_stub.use()          # CAPSULE_CLI -> a stub that reports every property PASS

STUB_MODE=fail makes it report `commitment_signature FAIL` on every staple, STUB_MODE=nokey `checkpoint_key
NOT_CHECKED`, and STUB_LOG, when set, receives its argv as one JSON line per call. The real binary is exercised by
tests/test_staple_verify.py when it is installed.
"""
import atexit
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

SCRIPT = '''#!{python}
import json, os, sys
argv = sys.argv[1:]
if os.environ.get("STUB_LOG"):
    open(os.environ["STUB_LOG"], "a").write(json.dumps(argv) + "\\n")
doc = json.load(open(argv[argv.index("--history") + 1]))
streams = [c.get("stream") for c in doc.get("history_cards", [])] or ["card"]
mode = os.environ.get("STUB_MODE", "")
staples = []
for stream in streams:
    props = {{p: "PASS" for p in ("card_digest", "card_form", "checkpoint_anchor", "checkpoint_consistency",
              "checkpoint_key", "checkpoint_signature", "commitment_anchor", "commitment_signature",
              "envelope_signature", "responder_trust", "stream_match", "subject_scope")}}
    props["caller_invariance"], props["request_binding"] = "NOT_CHECKED", "NOT_PRESENT"
    findings = []
    if mode == "fail":
        props["commitment_signature"] = "FAIL"
        findings = ["commitment_signature FAIL: the commitment's key_id is not the counterparty key you hold"]
    if mode == "nokey":
        props["checkpoint_key"] = "NOT_CHECKED"
    staples.append({{"stream": stream, "properties": props, "findings": findings}})
print(json.dumps({{"spec_version": "capsule-cli-result/v1", "kind": "history_card/1", "verdict": "NOT_ACCEPTED",
                  "findings": [], "staples": staples}}))
sys.exit(3)
'''


def use():
    """Point CAPSULE_CLI at a stub, once per process."""
    if os.environ.get("CAPSULE_CLI", "").endswith("capsule-cli-stub"):
        return Path(os.environ["CAPSULE_CLI"])
    d = tempfile.mkdtemp()
    atexit.register(shutil.rmtree, d, True)
    path = Path(d) / "capsule-cli-stub"
    path.write_text(SCRIPT.format(python=sys.executable))
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    os.environ["CAPSULE_CLI"] = str(path)
    return path
