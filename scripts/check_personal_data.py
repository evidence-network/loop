#!/usr/bin/env python3
"""Check that no personal data is in the commitment list or the reports.

Looks, in every regular file under each PATH (default: nodes reports), for:

- an email address, other than on a domain reserved for examples (RFC 2606/6761:
  example.com/.net/.org, *.example, *.invalid, *.test, *.localhost);
- a phone number: `+` and 8 to 15 digits, or 3-3-4 digits with separators;
- a public IP address (IPv4 or IPv6): one that is not loopback, private, link-local or
  reserved for documentation.

A node's entry names its operator by GitHub handle, which the operator chose to make public;
that is not flagged. Nothing here finds a person's name in free text: a reviewer still reads
each nodes/ pull request.

Usage: python3 scripts/check_personal_data.py [PATH ...]
Exit 0 = nothing found; 1 = something found (each hit printed as path:line: kind).
"""
import ipaddress
import os
import re
import sys
from pathlib import Path

# The lookbehind starts a match only where a local-part run starts: without it a long base64 line (a 300 kB wire.json
# line in step 12's proof) is tried from every position of the run, quadratic, minutes per file. finditer's matches
# are the same, as the leftmost match always starts there.
EMAIL = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@(?P<domain>[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})")
EXAMPLE_DOMAINS = ("example.com", "example.net", "example.org")
EXAMPLE_TLDS = (".example", ".invalid", ".test", ".localhost")

# Not inside a base64 or hex run: a `+` or digit there is not a phone number.
_EDGE_L = r"(?<![A-Za-z0-9+/=])"
_EDGE_R = r"(?![A-Za-z0-9+/=])"
PHONE = re.compile(
    _EDGE_L + r"(?:\+\d(?:[ .-]?\d){7,14}|\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4})" + _EDGE_R
)

IPV4 = re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])")
# Groups of hex separated by colons, at least two colons; validated by ipaddress below.
IPV6 = re.compile(r"(?<![\w:.])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![\w:.])")
DOC_NETS = [ipaddress.ip_network(n) for n in
            ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32")]


def _example_domain(domain):
    d = domain.lower()
    return d in EXAMPLE_DOMAINS or d.endswith(tuple("." + x for x in EXAMPLE_DOMAINS)) \
        or d.endswith(EXAMPLE_TLDS) or d in tuple(t[1:] for t in EXAMPLE_TLDS)


def _public_ip(text):
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False
    return ip.is_global and not any(ip in n for n in DOC_NETS)


def find(line):
    """The kinds of personal data on one line."""
    kinds = []
    if any(not _example_domain(m.group("domain")) for m in EMAIL.finditer(line)):
        kinds.append("email address")
    if PHONE.search(line):
        kinds.append("phone number")
    if any(_public_ip(m.group(0)) for m in IPV4.finditer(line)) \
            or any(_public_ip(m.group(0)) for m in IPV6.finditer(line)):
        kinds.append("public IP address")
    return kinds


def files(path):
    """Regular files under `path` (or `path` itself), never following a symbolic link."""
    if path.is_symlink():
        return
    if path.is_file():
        yield path
        return
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if not p.is_symlink() and p.is_file():
                yield p


def scan(paths):
    hits = []
    for root in paths:
        for f in files(Path(root)):
            text = f.read_bytes().decode("utf-8", errors="replace")
            for i, line in enumerate(text.splitlines(), start=1):
                for kind in find(line):
                    hits.append(f"{f}:{i}: {kind}")
    return hits


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    paths = argv or ["nodes", "reports"]
    missing = [p for p in paths if not Path(p).exists()]
    if missing:
        print(f"check_personal_data: no such path: {', '.join(missing)}", file=sys.stderr)
        return 2
    hits = scan(paths)
    for h in hits:
        print(h)
    if hits:
        print(f"check_personal_data: {len(hits)} hit(s). Remove the data; this repository "
              "is public and keeps its history.")
        return 1
    print(f"check_personal_data: {', '.join(paths)}: no personal data found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
