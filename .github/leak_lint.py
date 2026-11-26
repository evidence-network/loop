#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""leak-lint: fail the build if internal workflow text reaches this public repository.

This repository is public. It must not carry the ids, vocabulary or paths of any private
workspace that works on it: to an outside operator, that reads as the network being steered
from somewhere they cannot see.

Two kinds of rule:

  1. **Bracketed internal ids (public rule, always on)**: `[` + 3-or-more hyphen-separated
     lowercase-alnum segments + `]`, e.g. `[some-internal-task-id]`. The 3-segment floor
     excludes TOML table headers (`[build-system]`) and the lowercase-only class excludes
     citation tags (`[RFC2119]`). Markdown links, reference links and HTML attribute selectors
     are excluded by what surrounds the bracket (see `_has_bracket_id_leak`).
  2. **Term classes (from a secret)**: literal substrings grouped into named classes. The list
     is deliberately NOT stored in this repository: a public gate that enumerated the terms
     would itself publish them. It is supplied at run time in the ``LEAK_LINT_TERMS``
     environment variable (a repository secret in CI), as a JSON object mapping a class name to
     its terms, e.g. ``{"class-a": ["term", ...], "class-b": [...]}``. Matching is
     case-sensitive.

Fail-closed: if ``LEAK_LINT_TERMS`` is unset, empty, malformed or carries no terms, the lint
exits 2 rather than passing on the public rule alone. A missing list never reads as clean.

Output redaction: unless ``LEAK_LINT_REVEAL`` is truthy, a run on which any term matched prints
one constant verdict (no path, line, class or count), because on a fork run the scanned content
is the submitter's and detailed output would say which candidate matched. The pass/fail result
still says whether some candidate string matches some term; redaction does not hide that.
Bracketed-id hits carry nothing secret and are printed in full, but only when no term matched
anywhere (otherwise which lines are printed would itself be the oracle).

The same pattern as evidencebook's `.github/leak_lint.py`, without its lockfile exemptions:
this repository has no lockfiles. Re-vendor them from there if one is ever added.

  - **Exact-text allowlist**, `leak_lint_allowlist.txt` next to this script, for genuine
    historical record, loaded from the trusted scanner checkout, never the scanned PR. The
    exact stripped line text, never line numbers.
  - **Excludes this script, its allowlist, its CI workflow and its tests by file name.**
  - **Scans the COMMITTED tree** (`git ls-files -z`) of ROOT, and never follows a symbolic link
    or reads outside ROOT: on a fork run ROOT is untrusted content.
  - **Every tracked text file**, whatever its name or suffix. Only a binary file, one holding a
    NUL byte, is skipped.
  - **Encoded bytes are not text.** In a file that is exactly a sealed capsule
    (`_proof_encoded_values`: closed key sets, the capsule id and the base64 capsule naming it, the
    producer's base64 COSE_Sign1 parsed whole, each artifact's base64 content and the sha256 it
    hashes to), a term match wholly inside one of those encoded values is not a hit: random signature and digest bytes spell short terms by
    chance (loop#485), and base64 of real text never spells the term out. Every other string, and
    every other file, is scanned as before.

Usage: python3 leak_lint.py [ROOT=.]
Exit 0 = clean; 1 = leak(s) found; 2 = misconfiguration (no term list).
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

SELF_NAMES = {
    "leak_lint.py",
    "leak_lint_allowlist.txt",
    "leak-lint.yml",
    "test_leak_lint.py",
}

# Lowercase-alnum segments only (excludes uppercase citation tags structurally), each segment
# 2+ chars (excludes a hex regex character class like `[0-9a-f]`), 3+ segments (excludes
# 2-segment TOML headers like [build-system]).
BRACKET_ID = re.compile(r"\[[a-z0-9]{2,}(?:-[a-z0-9]{2,}){2,}\]")

BRACKET_CLASS = "bracketed-id"

UNTRUSTED_VERDICT = (
    "leak-lint: content check failed. Details are withheld on fork runs; a maintainer can "
    "re-run this check on a trusted event."
)


def load_terms() -> dict[str, tuple[str, ...]]:
    """The term classes from ``LEAK_LINT_TERMS``; exits 2 when there are none."""
    raw = os.environ.get("LEAK_LINT_TERMS", "").strip()
    if not raw:
        print(
            "error: LEAK_LINT_TERMS is empty or unset. leak-lint is fail-closed: configure "
            "the repository secret. (On fork PRs a pull_request run gets no secrets; this "
            "check runs under pull_request_target for that reason.)",
            file=sys.stderr,
        )
        raise SystemExit(2)
    try:
        cfg = json.loads(raw)
        if isinstance(cfg, str):
            # Some secret-setting paths double-encode the JSON.
            cfg = json.loads(cfg)
    except json.JSONDecodeError as exc:
        print(f"error: LEAK_LINT_TERMS is not valid JSON: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    if not isinstance(cfg, dict):
        print("error: LEAK_LINT_TERMS must decode to a JSON object.", file=sys.stderr)
        raise SystemExit(2)
    classes: dict[str, tuple[str, ...]] = {}
    for name, terms in cfg.items():
        if not isinstance(terms, list) or not all(isinstance(t, str) and t for t in terms):
            print(
                "error: each LEAK_LINT_TERMS class must be a list of non-empty strings.",
                file=sys.stderr,
            )
            raise SystemExit(2)
        if terms:
            classes[str(name)] = tuple(terms)
    if not classes:
        print("error: LEAK_LINT_TERMS carries no terms.", file=sys.stderr)
        raise SystemExit(2)
    return classes


def reveal_matches() -> bool:
    """Whether matched lines may be printed. Redaction is the default; the workflow grants the
    opt-in only on trusted runs (not a fork's pull_request_target). This variable is the whole
    security boundary for the term list, not a convenience toggle."""
    return os.environ.get("LEAK_LINT_REVEAL", "").strip().lower() in ("1", "true", "yes", "on")


def _tracked_files(root: Path) -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True)
    return [root / os.fsdecode(p) for p in out.stdout.split(b"\0") if p]


def _read_regular_file(root: Path, path: Path) -> str | None:
    """The text of *path* when it is safe to read, else ``None``: no component from *root* down
    is a symbolic link, it resolves inside *root*, and it is a regular file (checked again after
    opening without following a final link)."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        return None
    current = root
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            return None
    try:
        if not path.resolve().is_relative_to(root.resolve()):
            return None
        if not stat.S_ISREG(path.lstat().st_mode):
            return None
        fd = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        )
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, "rb") as fh:
            fd = -1
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    finally:
        if fd >= 0:
            os.close(fd)


#: A comment line in the allowlist file is `#` followed by whitespace or end-of-line, not just
#: `#` as the first character, so an allowlisted Markdown heading is not dropped as a comment.
_COMMENT_LINE = re.compile(r"^#(\s|$)")


def _load_allowlist(root: Path) -> set[str]:
    text = _read_regular_file(root, root / ".github" / "leak_lint_allowlist.txt")
    if text is None:
        return set()
    return {
        line.rstrip("\n")
        for line in text.splitlines()
        if line.strip() and not _COMMENT_LINE.match(line.lstrip())
    }


def _has_bracket_id_leak(line: str) -> bool:
    """True if `line` contains a bracketed id that is NOT a markdown link/reference/citation.

    Checked per match, because the exempt shapes need different context:
      - a pip extra, e.g. `pkg[some-extra-name]`: exempt when the match is immediately
        preceded by an identifier character (letter/digit/`_`/`-`).
      - inline link `[text](url)` or the first half of `[text][ref]`: exempt when the match is
        immediately followed by `(` or `[`.
      - reference-link DEFINITION `[ref]: url`: exempt only when the bracket is the first thing
        on the line and is followed by `:`.
      - uppercase citation tags (`[RFC2119]`): excluded by BRACKET_ID's lowercase-only class.
      - HTML attribute selectors (`[data-report-date]`): exempt when the content starts with
        `data-` or `aria-`.
    """
    for m in BRACKET_ID.finditer(line):
        if m.group(0)[1:].startswith(("data-", "aria-")):
            continue
        before = line[m.start() - 1 : m.start()]
        if before and (before.isalnum() or before in "_-"):
            continue
        after = line[m.end() : m.end() + 1]
        if after and after in "([":
            continue
        if after == ":" and line[: m.start()].strip() == "":
            continue
        return True
    return False


_HEX64 = re.compile(r"[0-9a-f]{64}")
#: The only keys a sealed capsule file and each of its artifacts may carry; any other key and the file is not one.
_CAPSULE_FILE_KEYS = {"capsule_id", "capsule", "producer_envelope", "artifacts"}
_ARTIFACT_KEYS = {"name", "binding", "content", "state", "content_sha256"}


def _b64(value: object) -> bytes | None:
    """The bytes of *value* when it is a string of strictly valid base64, else ``None``."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return None


def _cbor_head(buf: bytes, i: int) -> tuple[int, int, int]:
    """(major type, argument, next offset) of the CBOR item head at *i*; raises ValueError on a
    malformed or indefinite-length head."""
    if i >= len(buf):
        raise ValueError("truncated")
    major, info = buf[i] >> 5, buf[i] & 0x1F
    if info < 24:
        return major, info, i + 1
    if info > 27:
        raise ValueError("indefinite or reserved length")
    n = 1 << (info - 24)
    if i + 1 + n > len(buf):
        raise ValueError("truncated")
    return major, int.from_bytes(buf[i + 1 : i + 1 + n], "big"), i + 1 + n


def _cbor_skip(buf: bytes, i: int, depth: int = 0) -> int:
    """The offset just past the whole CBOR item at *i*; raises ValueError when it is malformed."""
    if depth > 16:
        raise ValueError("too deep")
    major, arg, i = _cbor_head(buf, i)
    if major in (2, 3):
        if i + arg > len(buf):
            raise ValueError("truncated")
        return i + arg
    if major == 4:
        for _ in range(arg):
            i = _cbor_skip(buf, i, depth + 1)
        return i
    if major == 5:
        for _ in range(2 * arg):
            i = _cbor_skip(buf, i, depth + 1)
        return i
    if major == 6:
        return _cbor_skip(buf, i, depth + 1)
    return i  # 0, 1 (integers) and 7 (simple values and floats: the head carries them)


def _is_cose_sign1(buf: bytes) -> bool:
    """True when *buf* is exactly one tagged COSE_Sign1 message (RFC 9052, section 4.2): tag 18 on
    [protected: bstr, unprotected: map, payload: bstr or nil, signature: bstr], nothing after it."""
    try:
        major, arg, i = _cbor_head(buf, 0)
        if (major, arg) != (6, 18):
            return False
        major, arg, i = _cbor_head(buf, i)
        if (major, arg) != (4, 4):
            return False
        for allowed in ({2}, {5}, {2, 7}, {2}):
            major, arg, _ = _cbor_head(buf, i)
            if major not in allowed or (major == 7 and arg != 22):
                return False
            i = _cbor_skip(buf, i)
        return i == len(buf)
    except ValueError:
        return False


def _artifact_ok(art: object) -> bool:
    if not isinstance(art, dict) or not set(art) <= _ARTIFACT_KEYS:
        return False
    if any(not isinstance(art.get(k, ""), str) for k in ("name", "binding", "state")):
        return False
    content, digest = _b64(art.get("content")), art.get("content_sha256")
    return (content is not None and isinstance(digest, str) and bool(_HEX64.fullmatch(digest))
            and hashlib.sha256(content).hexdigest() == digest)


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """A JSON object's dict, refusing a repeated key: `json.loads` keeps only the last one, so a
    repeated key could hide a field from the shape check while its raw text is still matched."""
    out: dict[str, object] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"repeated key {key!r}")
        out[key] = value
    return out


def _proof_encoded_values(text: str) -> set[tuple[str, str]]:
    """The (field, value) pairs of a sealed capsule file's encoded values, when *text* is one, else nothing.

    A sealed capsule file (each step's ``capsule.json``, which the status page publishes) is one
    JSON object with only the keys in _CAPSULE_FILE_KEYS: ``capsule_id`` (64 lowercase hex),
    ``capsule``, base64 of a capsule naming that id, ``producer_envelope``, base64 of one COSE_Sign1
    message, and ``artifacts``, each with only the keys in _ARTIFACT_KEYS and base64 ``content``
    whose sha256 is its ``content_sha256``. Every check must hold, or nothing is exempt: the
    closed key sets, with no key repeated at any depth, mean each field name occurs only at its
    own place, and text that merely looks encoded is never exempt.
    """
    try:
        doc = json.loads(text, object_pairs_hook=_unique_keys)
    except ValueError:
        return set()
    if not isinstance(doc, dict) or set(doc) != _CAPSULE_FILE_KEYS:
        return set()
    cid, raw = doc["capsule_id"], _b64(doc["capsule"])
    if not (isinstance(cid, str) and _HEX64.fullmatch(cid) and raw is not None):
        return set()
    try:
        inner = json.loads(raw)
    except ValueError:
        return set()
    if not isinstance(inner, dict) or inner.get("capsule_id") != cid:
        return set()
    envelope = _b64(doc["producer_envelope"])
    if envelope is None or not _is_cose_sign1(envelope):
        return set()
    artifacts = doc["artifacts"]
    if not isinstance(artifacts, list) or not all(_artifact_ok(a) for a in artifacts):
        return set()
    out = {("capsule_id", cid), ("capsule", doc["capsule"]), ("producer_envelope", doc["producer_envelope"])}
    for art in artifacts:
        out.update((("content", art["content"]), ("content_sha256", art["content_sha256"])))
    return out


def _encoded_spans(line: str, encoded: set[tuple[str, str]]) -> list[tuple[int, int]]:
    """The spans of *line* that hold a checked value as the whole JSON string of its own field."""
    return [
        m.span(1)
        for field, value in encoded
        for m in re.finditer(r'"%s"\s*:\s*"(%s)"' % (re.escape(field), re.escape(value)), line)
    ]


def _term_in_text(line: str, word: str, spans: list[tuple[int, int]]) -> bool:
    """True when *word* occurs in *line* outside every encoded span. Inside random signature or
    digest bytes a short term turns up by chance (evidence-network/loop#485), and base64 of real
    text never spells the term out, so a match wholly inside one is not a leak."""
    start = line.find(word)
    while start != -1:
        end = start + len(word)
        if not any(a <= start and end <= b for a, b in spans):
            return True
        start = line.find(word, start + 1)
    return False


def classify(
    line: str, terms: dict[str, tuple[str, ...]], encoded: set[tuple[str, str]] = frozenset()
) -> tuple[bool, list[str]]:
    """(bracketed-id hit?, the term classes that match) for one line. A term counts only where it
    occurs outside the file's checked encoded values (`_proof_encoded_values`, `_term_in_text`)."""
    spans = _encoded_spans(line, encoded) if encoded else []
    return _has_bracket_id_leak(line), [
        name for name, words in terms.items() if any(_term_in_text(line, w, spans) for w in words)
    ]


def scan(root: Path, terms: dict[str, tuple[str, ...]]) -> tuple[list[str], list[str]]:
    """(bracket-only hits, hits on lines where a term matched), each `path:line:classes: text`."""
    allow = _load_allowlist(Path(__file__).resolve().parent.parent)
    bracket_only: list[str] = []
    term_hits: list[str] = []
    for f in _tracked_files(root):
        if f.name in SELF_NAMES:
            continue
        text = _read_regular_file(root, f)
        if text is None:
            # Not a readable regular file inside ROOT (deleted-but-staged, a link, a
            # directory): not a leak either way.
            continue
        if "\0" in text:
            # A binary file: NUL bytes never appear in text. Every other file is scanned,
            # whatever its name or suffix (versions.lock, .verification, ATTEMPT, .pem).
            continue
        encoded = _proof_encoded_values(text)
        for i, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped in allow:
                continue
            bracket, classes = classify(line, terms, encoded)
            if not bracket and not classes:
                continue
            labels = ([BRACKET_CLASS] if bracket else []) + classes
            entry = f"{f.relative_to(root)}:{i}:{','.join(labels)}: {stripped}"
            (term_hits if classes else bracket_only).append(entry)
    return bracket_only, term_hits


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    root = Path(argv[0] if argv else ".")
    terms = load_terms()
    bracket_only, term_hits = scan(root, terms)
    if term_hits and not reveal_matches():
        print(UNTRUSTED_VERDICT)
        return 1
    hits = term_hits + bracket_only
    if hits:
        print(f"leak-lint: {len(hits)} internal-leak hit(s) found in the committed tree.")
        print(
            "If a hit is a genuine historical record (not live drift), add its exact stripped "
            "line text to .github/leak_lint_allowlist.txt. Otherwise, fix it: an allowlist "
            "seeded with a real leak teaches the next person the lint is advisory."
        )
        for h in hits:
            print(" ", h)
        return 1
    print("leak-lint: clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
