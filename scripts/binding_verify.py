"""The Go controller-binding verifier (`cross_language binding`, aac-go go/v0.15.0), as steps 05 and 12 call it.

Exit 0 means "the binding applies" (form, both signatures and window PASS, revocation not FAIL): not a trust
verdict and not which role the key has in it (agent-action-capsule#468). The caller names the key and the times.
"""
import json
import subprocess

VERDICT_EXIT = {0: "ok", 1: "bad", 3: "unsupported"}
UNSUPPORTED_METHOD_ISSUE = "evidence-network/loop#166"  # exit 3: a binding method the verifier lacks

# INTERIM (loop#140): the plugin writes bindings with key_types ["ed25519", "bip340"] (AAC#457, spec PR AAC#458), which
# the pinned aac-go (Ed25519-only controller binding v0) cannot check. The verifier then says FAIL, but it lacks the
# key type; it did not find a bad binding. Remove this, the branches that use it, and their tests once AAC#458
# merges, aac-go ships bip340, the loop repins and steps 05 and 12 rerun.
UNSUPPORTED_KEY_TYPE_ISSUE = "action-state-group/agent-action-capsule#457"
KNOWN_KEY_TYPES = {"ed25519", "bip340"}  # AAC#457 defines these two; any other token makes the list malformed (bad)
UNSUPPORTED_KEY_TYPES = {"bip340"}


def unsupported_key_types(raw, nkeys):
    """The key types a binding declares in `key_types` that the pinned verifier lacks, sorted; none when any
    declared type is not one AAC#457 defines or the list is not one token per key (the binding is then malformed,
    not unsupported)."""
    try:
        types = json.loads(raw).get("key_types")
    except (ValueError, AttributeError):
        return []
    if not isinstance(types, list) or len(types) != nkeys or not all(isinstance(t, str) and t in KNOWN_KEY_TYPES for t in types):
        return []
    return sorted(set(types) & UNSUPPORTED_KEY_TYPES)


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
