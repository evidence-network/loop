#!/usr/bin/env python3
"""Check the published revocation statements: every nodes/revocations/<binding_digest>.json (loop#138).

Usage: check_revocations.py [DIR]   (default: nodes/revocations; a missing folder is clean)

Each file is a non-empty JSON array of Controller Binding v0 §5 statements for the binding its name gives:
exactly the members revocation_version (controller-binding-revocation/0), binding (the file's digest), key_id,
revoked_at and sig. Signatures and times are not checked here: step 05 passes the statements to the pinned
verifier (`cross_language binding`), which ignores one that does not apply. Step 12 does not read them yet.
Exit 0 = clean, 1 = refused.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import binding_verify as bv  # noqa: E402


def check(folder):
    """[refusal] for every file in `folder` that is not a revocations file."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    refused = []
    for path in sorted(folder.iterdir()):
        if path.name == "README.md":
            continue
        try:
            bv.read_revocations(path)
        except ValueError as e:
            refused.append(f"revocation.malformed: {e}")
    return refused


def main(argv):
    refused = check(argv[0] if argv else "nodes/revocations")
    for r in refused:
        print(r, file=sys.stderr)
    return 1 if refused else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
