"""The Go controller-binding verifier (`cross_language binding`, aac-go go/v0.16.0: Ed25519 and BIP-340 keys, AAC#458), as steps 05 and 12 call it.

Exit 0 means "the binding applies" (form, both signatures and window PASS, revocation not FAIL): not a trust
verdict and not which role the key has in it (agent-action-capsule#468). The caller names the key and the times.
"""
import json
import subprocess

VERDICT_EXIT = {0: "ok", 1: "bad", 3: "unsupported"}
UNSUPPORTED_METHOD_ISSUE = "evidence-network/loop#166"  # exit 3: a binding method the verifier lacks

def verify(verifier, raw, key, times):
    """(verdict, report, stderr, returncode) of `verifier binding` on the binding text `raw`, held to naming `key`
    at every time in `times`. verdict is "ok", "bad" or "unsupported" (a method the verifier does not implement),
    None for any other exit (usage error, old verifier): the caller stops. report is the verifier's JSON, {} when
    it is not."""
    v = subprocess.run([str(verifier), "binding"], capture_output=True, timeout=120,
                       input=json.dumps({"binding": raw, "at": times, "key": key}).encode())
    try:
        report = json.loads(v.stdout.decode(errors="replace"))
    except ValueError:
        report = {}
    return (VERDICT_EXIT.get(v.returncode), report if isinstance(report, dict) else {},
            v.stderr.decode(errors="replace").strip(), v.returncode)
