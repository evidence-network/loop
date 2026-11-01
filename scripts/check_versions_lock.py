#!/usr/bin/env python3
"""Check versions.lock: every pin is a full commit SHA or a released version.

Usage: check_versions_lock.py [FILE]   (default: versions.lock)

One pin per line, `<name> <repository> <ref>`; blank lines and lines starting
with `#` are ignored. Refuses a ref that is not a full 40-hex commit SHA or a
release tag ending in a full version (`0.1.5`, `v0.1.0-rc23`,
`crates/evidencebook-v0.0.7`): a branch name or a short SHA moves or is
ambiguous, so a report run under it could not be rebuilt. Also refuses a
malformed line or a repeated name. Standard library only.
Exit 0 = clean, 1 = refused.
"""
import re
import sys
from pathlib import Path

NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA = re.compile(r"^[0-9a-f]{40}$")
SHORT_SHA = re.compile(r"^[0-9a-fA-F]{4,39}$")
# A tag whose last part is a full MAJOR.MINOR.PATCH, with an optional
# pre-release; anything before it is the tag's prefix (`crates/evidencebook-`).
RELEASE = re.compile(r"^(?:[A-Za-z0-9_.-]+/)*(?:[A-Za-z0-9_.]+-)*v?\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?$")


def check_ref(ref):
    """None when ref is a pin; otherwise why not."""
    if SHA.fullmatch(ref):
        return None
    if SHORT_SHA.fullmatch(ref) and not ref.isdigit():
        return f"{ref} is a short or uppercase SHA: give the full 40-hex commit SHA"
    if RELEASE.fullmatch(ref):
        return None
    return f"{ref} is not a full commit SHA or a released version (a branch name moves)"


def check(text):
    errs, names = [], set()
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 3:
            errs.append(f"line {n}: want `<name> <repository> <ref>`, got {line!r}")
            continue
        name, repo, ref = parts
        if not NAME.fullmatch(name):
            errs.append(f"line {n}: name {name!r} is not lowercase letters, digits and dashes")
        elif name in names:
            errs.append(f"line {n}: {name} is pinned twice")
        names.add(name)
        if not REPO.fullmatch(repo):
            errs.append(f"line {n}: {name}: repository {repo!r} is not <owner>/<repo>")
        why = check_ref(ref)
        if why:
            errs.append(f"line {n}: {name}: {why}")
    if not names and not errs:
        errs.append("no pins")
    return errs, len(names)


def main(argv):
    path = Path(argv[1] if len(argv) > 1 else "versions.lock")
    try:
        errs, count = check(path.read_text(encoding="utf-8"))
    except OSError as e:
        print(e, file=sys.stderr)
        return 1
    for e in errs:
        print(f"{path}: {e}", file=sys.stderr)
    if errs:
        return 1
    print(f"{path}: {count} pin{'' if count == 1 else 's'} ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
