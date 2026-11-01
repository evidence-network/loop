"""The Go controller-binding verifier (`cross_language binding`, aac-go go/v0.17.0: Ed25519 and BIP-340 keys, AAC#458), as steps 05 and 12 call it.

Exit 0 means "the binding applies" (form, both signatures and window PASS, revocation not FAIL): not a trust
verdict and not which role the key has in it (agent-action-capsule#468). The caller names the key and the times.

Revocation statements (Controller Binding v0 §5) for a lab binding are published by pull request at
`nodes/revocations/<binding_digest>.json` (loop#138): a JSON array of statements, each signed by one of the
binding's two keys; the loop never signs one. CB §5 leaves delivery to the using profile; this folder is the
loop profile's own delivery point, not a registry. A statement that applies at or before a time asked about makes the verifier report revocation FAIL.

CB-1 (option A, Controller Binding v0 §5.1, §9.4, agent-action-capsule#495): the loop run itself declares that
folder complete for the binding as of the time it evaluates, and passes that declaration to the verifier with the
list's exact octets. A binding the repository publishes nothing for is the empty list `[]`, declared complete. The
verifier then reports revocation PASS when no statement in that snapshot applies at the times asked about: the
loop run's statement, scoped to the snapshot (`list_digest`, `as_of`). A declaration the verifier rejects (it is
stale, or earlier than a time asked about) leaves revocation NOT_CHECKED, with the verifier's reason.

The loop profile is stricter than CB §9.4 about its own folder: a published file that is not a revocations file
(malformed, an empty array, a statement for another binding) makes the binding not apply, a FAIL (loop#194), rather
than NOT_CHECKED. The folder is the repository's, checked on every pull request (`scripts/check_revocations.py`), so
a file out of shape is a defect in the profile's own delivery, not an unreadable third-party list.
"""
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

VERDICT_EXIT = {0: "ok", 1: "bad", 3: "unsupported"}
UNSUPPORTED_METHOD_ISSUE = "evidence-network/loop#193"  # exit 3: a binding method or key type the verifier lacks
REVOCATIONS = "revocations"  # the folder under nodes/
REVOCATION_VERSION = "controller-binding-revocation/0"
REVOCATION_MEMBERS = ("revocation_version", "binding", "key_id", "revoked_at", "sig")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
PROFILE = "evidence-network/loop: nodes/revocations/<binding_digest>.json"  # the profile whose list is declared
MAX_AGE = 3600  # seconds: the declaration is made at the step's evaluation time (as_of = evaluated_at)


def _unique(pairs):
    """An object hook that refuses a repeated member: a file showing two revoked_at must not read as one."""
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys):
        raise ValueError(f"a repeated member ({', '.join(sorted({k for k in keys if keys.count(k) > 1}))})")
    return dict(pairs)


def read_revocations(path, text=None):
    """The statements in one `nodes/revocations/<binding_digest>.json` (its text, read here unless given), as JSON
    texts. Raises ValueError when the file is not named by a digest, is not an array of objects with exactly the
    CB §5 members (an object repeating a member, at any depth, is refused), or holds a statement for another
    binding or of another version. Signatures and times are the verifier's to check."""
    path = Path(path)
    digest = path.name[:-len(".json")] if path.name.endswith(".json") else ""
    if not HEX64.match(digest):
        raise ValueError(f"{path.name}: not named <binding_digest>.json")
    try:
        doc = json.loads(path.read_text(encoding="utf-8") if text is None else text, object_pairs_hook=_unique)
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


def checkout_list(nodes_dir, digest):
    """The exact text of `nodes_dir`/revocations/<digest>.json in this checkout, `[]` when there is none. Raises
    ValueError, as read_revocations does, for a file that is not a revocations file. The rehearsal reads its
    fixtures' folder this way; a live run reads main (main_list)."""
    path = Path(nodes_dir) / REVOCATIONS / f"{digest}.json"
    if not (HEX64.match(str(digest)) and path.exists()):
        return "[]"
    try:  # read once: the octets checked are the octets hashed and sent
        text = path.read_bytes().decode("utf-8")
    except (OSError, ValueError) as e:
        raise ValueError(f"{path.name}: not readable ({e})")
    read_revocations(path, text)
    return text


def main_commit(root, at):
    """The commit main held at `at` (YYYY-MM-DDTHH:MM:SSZ): the newest on the first-parent line of origin/main no
    later than `at`, in the repository at `root`; None when there is none. Only origin/main: a local main may carry
    commits never merged."""
    r = subprocess.run(["git", "-C", str(root), "rev-list", "-1", "--first-parent", f"--before={at}", "origin/main"],
                       capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def holds_on_main(root, commit):
    """Whether `commit` is on origin/main in the clone at `root` (an ancestor of it, or it)."""
    return subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", str(commit), "origin/main"],
                          capture_output=True).returncode == 0


def snapshot(root, at):
    """The main commit a live run reads nodes/revocations/ from, as {"commit"} or {"commit": None, "why"}: origin/main
    is fetched first (a clone that last fetched before a revocation was merged would otherwise declare an older list
    complete as of `at`), then main_commit at `at`. A failed fetch is a reason, never a stale read. LOOP_REVOCATIONS_FETCH=0
    skips the fetch (tests; a clone with no origin remote has nothing to fetch). The live step keeps it in its proof,
    so step 08 reads the same commit from its own history without fetching."""
    has_origin = subprocess.run(["git", "-C", str(root), "remote", "get-url", "origin"], capture_output=True).returncode == 0
    if has_origin and os.environ.get("LOOP_REVOCATIONS_FETCH", "1") != "0":
        try:
            # An explicit refspec, so origin/main moves even in a single-branch clone; never a credential prompt.
            f = subprocess.run(["git", "-C", str(root), "fetch", "-q", "origin",
                                "+refs/heads/main:refs/remotes/origin/main"], capture_output=True, timeout=120,
                               stdin=subprocess.DEVNULL, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
            failed = f.stderr.decode(errors="replace").strip() if f.returncode else None
        except (OSError, subprocess.TimeoutExpired) as e:
            failed = str(e)
        if failed:
            return {"commit": None, "why": f"origin/main could not be fetched to read nodes/{REVOCATIONS}/ from ({failed[:200]})"}
    commit = main_commit(root, at)
    if commit is None:
        return {"commit": None, "why": f"this clone holds no origin/main commit at or before {at} to read nodes/{REVOCATIONS}/ from"}
    return {"commit": commit}


def main_list(root, digest, snap):
    """(commit, text) of nodes/revocations/<digest>.json at the snapshot's main commit (snapshot): a key holder's
    statement counts in the loop once it is merged, so a branch cannot drop one from its own head. The text is `[]`
    when that commit holds no file for the binding. Raises LookupError when the snapshot has no commit (with its
    reason) or this clone does not hold it, ValueError for a file that is not a revocations file."""
    commit = snap.get("commit") if isinstance(snap, dict) else None
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise LookupError((snap.get("why") if isinstance(snap, dict) else None)
                          or "no main commit is recorded to read nodes/revocations/ from")
    if subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{commit}^{{commit}}"], capture_output=True).returncode:
        raise LookupError(f"this clone does not hold main commit {commit[:12]} to read nodes/{REVOCATIONS}/ from")
    if not HEX64.match(str(digest)):
        return commit, "[]"
    path = f"nodes/{REVOCATIONS}/{digest}.json"
    if subprocess.run(["git", "-C", str(root), "cat-file", "-e", f"{commit}:{path}"], capture_output=True).returncode:
        return commit, "[]"
    r = subprocess.run(["git", "-C", str(root), "show", f"{commit}:{path}"], capture_output=True)
    try:
        if r.returncode:
            raise ValueError(r.stderr.decode(errors="replace").strip())
        text = r.stdout.decode("utf-8")
    except ValueError as e:
        raise ValueError(f"{digest}.json at main {commit[:12]}: not readable ({e})")
    read_revocations(Path(f"{digest}.json"), text)
    return commit, text


def revocation_list(digest, text, as_of, asserted_by):
    """The `revocation_list` the loop run passes for binding `digest` (CB-1 option A): the list's exact `text`
    (checkout_list or main_list) and the run's declaration that the list is complete as of `as_of` (the step's
    evaluation time, also `evaluated_at`)."""
    declaration = {"profile": PROFILE, "asserted_by": asserted_by, "binding": digest,
                   "list_digest": hashlib.sha256(text.encode("utf-8")).hexdigest(), "as_of": as_of,
                   "max_age": MAX_AGE}
    return {"declaration": declaration, "list": text, "evaluated_at": as_of}


def revocation(report):
    """(state, text) of the binding's revocation in a verifier report: PASS with the snapshot it is scoped to,
    else the state (NOT_CHECKED, FAIL) with the verifier's reason the verifier rejected the declaration, if any."""
    state = (report.get("properties") or {}).get("controller_binding_revocation")
    rl = report.get("revocation_list") if isinstance(report.get("revocation_list"), dict) else {}
    if state == "PASS" and rl.get("counts") is True:
        return "PASS", (f"revocation PASS: no statement applies in the list {rl.get('asserted_by')} declared "
                        f"complete as of {rl.get('as_of')} under profile {rl.get('profile')} (list sha256 "
                        f"{rl.get('list_digest')}); a statement made after {rl.get('as_of')} is not excluded")
    why = (f": the verifier rejected the declaration ({rl.get('reason')}), for the list {rl.get('asserted_by')} declared "
           f"complete as of {rl.get('as_of')} under profile {rl.get('profile')}" if rl.get("counts") is False else "")
    return state, f"revocation {state}{why}"


def verify(verifier, raw, key, times, revocation_list=None):
    """(verdict, report, stderr, returncode) of `verifier binding` on the binding text `raw`, held to naming `key`
    at every time in `times`, with the run's `revocation_list` (above) beside it. verdict is "ok", "bad"
    or "unsupported" (a method or key type the verifier does not implement),
    None for any other exit (usage error, old verifier): the caller stops. report is the verifier's JSON, {} when
    it is not."""
    query = {"binding": raw, "at": times, "key": key}
    if revocation_list is not None:
        query["revocation_list"] = revocation_list
    v = subprocess.run([str(verifier), "binding"], capture_output=True, timeout=120,
                       input=json.dumps(query).encode())
    try:
        report = json.loads(v.stdout.decode(errors="replace"))
    except ValueError:
        report = {}
    return (VERDICT_EXIT.get(v.returncode), report if isinstance(report, dict) else {},
            v.stderr.decode(errors="replace").strip(), v.returncode)
