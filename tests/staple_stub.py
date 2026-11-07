"""A stand-in for the pinned capsule-cli, so the step tests need no network or Go toolchain and do not depend on
the fixtures' synthetic cards (the real verifier refuses those synthetic cards).

    import staple_stub
    setUpModule, tearDownModule = staple_stub.module_hooks()   # CAPSULE_CLI -> the stub for this test module only

The stub reports every property PASS (for a served standard_history/1 answer, those a served answer can carry). STUB_MODE=fail makes it report `commitment_signature FAIL` on every staple,
STUB_MODE=nokey `checkpoint_key NOT_CHECKED`, and STUB_KEYED=1 reports `checkpoint_key` PASS for a card only when
its own entry's key is among the --checkpoint-key arguments (as the real binary does); STUB_LOG, when set, receives
its argv as one JSON line per call. The real binary is exercised by tests/test_staple_verify.py and
tests/test_step12_join.py when it is installed.
"""
import atexit
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

SCRIPT = '''#!{python}
import base64, json, os, sys
argv = sys.argv[1:]
if os.environ.get("STUB_LOG"):
    open(os.environ["STUB_LOG"], "a").write(json.dumps(argv) + "\\n")
doc = json.load(open(argv[argv.index("--history") + 1]))
if doc.get("history") == "standard_history/1":  # a served answer, its parts carried as exact octets
    env = {{p: "PASS" for p in ("anchor_match", "artifact_digest", "envelope_signature", "issued_at_form", "profile_version",
                              "request_binding", "responder_trust", "subject_binding")}}
    env.update(anchor_inclusion="NOT_CHECKED", responder_key_current="NOT_CHECKED", verification_binding="NOT_PRESENT")
    hist = {{"card_form": "PASS", "counts_integral": "PASS", "denominator": "PASS", "as_of": "NOT_CHECKED",
            "counts_recompute": "NOT_CHECKED", "window_from_policy": "NOT_CHECKED"}}
    if os.environ.get("STUB_MODE") == "fail":
        env["envelope_signature"] = "FAIL"
    print(json.dumps({{"spec_version": "capsule-cli-result/v1", "kind": "standard_history/1", "verdict": "ACCEPTED",
                      "history_properties": hist, "envelope_properties": env, "findings": []}}))
    sys.exit(0)
streams = [c.get("stream") for c in doc.get("history_cards", [])] or ["card"]
mode = os.environ.get("STUB_MODE", "")
keys = {{c.get("stream"): base64.urlsafe_b64decode(c["checkpoint_key"] + "==")[-32:].hex()
         for c in doc.get("history_cards", []) if c.get("checkpoint_key")}}
given = [argv[i + 1] for i, a in enumerate(argv) if a == "--checkpoint-key"]
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
    if os.environ.get("STUB_KEYED") and keys.get(stream) not in given:
        props["checkpoint_key"] = "NOT_CHECKED"
    staples.append({{"stream": stream, "properties": props, "findings": findings}})
print(json.dumps({{"spec_version": "capsule-cli-result/v1", "kind": "history_card/1", "verdict": "NOT_ACCEPTED",
                  "findings": [], "staples": staples}}))
sys.exit(3)
'''


def path():
    """The stub's file, made once per process."""
    global _PATH
    if _PATH is None:
        d = tempfile.mkdtemp()
        atexit.register(shutil.rmtree, d, True)
        _PATH = Path(d) / "capsule-cli-stub"
        _PATH.write_text(SCRIPT.format(python=sys.executable))
        _PATH.chmod(_PATH.stat().st_mode | stat.S_IXUSR)
    return _PATH


_PATH = None


def module_hooks():
    """(setUpModule, tearDownModule): CAPSULE_CLI names the stub while the module's tests run, and is restored after."""
    saved = []

    def up():
        saved.append(os.environ.get("CAPSULE_CLI"))
        os.environ["CAPSULE_CLI"] = str(path())

    def down():
        old = saved.pop()
        if old is None:
            os.environ.pop("CAPSULE_CLI", None)
        else:
            os.environ["CAPSULE_CLI"] = old
    return up, down
