"""The Go controller-binding verifier (`cross_language binding`, aac-go go/v0.16.0: Ed25519 and BIP-340 keys, AAC#458), as steps 05 and 12 call it.

Exit 0 means "the binding applies" (form, both signatures and window PASS, revocation not FAIL): not a trust
verdict and not which role the key has in it (agent-action-capsule#468). The caller names the key and the times.

Revocation statements (Controller Binding v0 §5) for a lab binding are published by pull request at
`nodes/revocations/<binding_digest>.json` (loop#138): a JSON array of statements, each signed by one of the
binding's two keys; the loop never signs one. CB §5 leaves delivery to the using profile, and this folder is the
lab's. A statement that applies at or before a time asked about makes the verifier report revocation FAIL. With
none that applies it reports NOT_CHECKED, never PASS (CB-1).
"""
import json
import re
import subprocess
from pathlib import Path

VERDICT_EXIT = {0: "ok", 1: "bad", 3: "unsupported"}
UNSUPPORTED_METHOD_ISSUE = "evidence-network/loop#166"  # exit 3: a binding method or key type the verifier lacks
REVOCATIONS = "revocations"  # the folder under nodes/
REVOCATION_VERSION = "controller-binding-revocation/0"
REVOCATION_MEMBERS = ("revocation_version", "binding", "key_id", "revoked_at", "sig")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def read_revocations(path):
    """The statements in one `nodes/revocations/<binding_digest>.json`, as JSON texts. Raises ValueError when the
    file is not named by a digest, is not an array of objects with exactly the CB §5 members, or holds a statement
    for another binding or of another version. Signatures and times are the verifier's to check."""
    path = Path(path)
    digest = path.name[:-len(".json")] if path.name.endswith(".json") else ""
    if not HEX64.match(digest):
        raise ValueError(f"{path.name}: not named <binding_digest>.json")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"{path.name}: not readable JSON ({e})")
    if not isinstance(doc, list) or not doc:
        raise ValueError(f"{path.name}: not a non-empty array of revocation statements")
    for n, s in enumerate(doc):
        if not isinstance(s, dict) or sorted(s) != sorted(REVOCATION_MEMBERS):
            raise ValueError(f"{path.name}[{n}]: members are not exactly {', '.join(REVOCATION_MEMBERS)}")
        if s["revocation_version"] != REVOCATION_VERSION or s["binding"] != digest:
            raise ValueError(f"{path.name}[{n}]: not a {REVOCATION_VERSION} statement for binding {digest}")
    return [json.dumps(s, separators=(",", ":"), sort_keys=True) for s in doc]


def revocations(nodes_dir, digest):
    """The statements published for binding `digest` under `nodes_dir`/revocations/, [] when none is."""
    path = Path(nodes_dir) / REVOCATIONS / f"{digest}.json"
    return read_revocations(path) if HEX64.match(str(digest)) and path.exists() else []


def verify(verifier, raw, key, times, revocations=()):
    """(verdict, report, stderr, returncode) of `verifier binding` on the binding text `raw`, held to naming `key`
    at every time in `times`, with these revocation statements (texts) received beside it. verdict is "ok", "bad"
    or "unsupported" (a method or key type the verifier does not implement),
    None for any other exit (usage error, old verifier): the caller stops. report is the verifier's JSON, {} when
    it is not."""
    query = {"binding": raw, "at": times, "key": key}
    if revocations:
        query["revocations"] = list(revocations)
    v = subprocess.run([str(verifier), "binding"], capture_output=True, timeout=120,
                       input=json.dumps(query).encode())
    try:
        report = json.loads(v.stdout.decode(errors="replace"))
    except ValueError:
        report = {}
    return (VERDICT_EXIT.get(v.returncode), report if isinstance(report, dict) else {},
            v.stderr.decode(errors="replace").strip(), v.returncode)
