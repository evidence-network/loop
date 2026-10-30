#!/usr/bin/env python3
"""Bind a lab node to its operator's test Nostr key through the capsules plugin's own join ceremony.

    bind.py --console URL --key KEYFILE [--rebind]

The ceremony (Mesh-LLM/capsules-plugin#121): `join {"controller": <pubkey>}` returns an unsigned
kind-3374 event; the lab's test key signs it (lab/nostr_sign.py); `join {"controller_event": <event>}`
completes the binding. The key is made at KEYFILE if absent. The signed event goes only to the node's
own console, is never written to disk and never published to a relay (CB section 9.3). Then `doctor`
must report controller_binding bound. A node that already reports it bound is left as it is
(--rebind runs the ceremony again, which replaces the binding). Prints the binding digest only, never a key.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import nostr_sign  # noqa: E402

TOOLS = "/api/plugins/capsules/tools/"


def post(console, tool, body, timeout=60):
    req = urllib.request.Request(console.rstrip("/") + TOOLS + tool, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"bind: {tool} refused ({e.code}): {e.read().decode(errors='replace')[:300]}")
    except (OSError, ValueError) as e:
        raise SystemExit(f"bind: {tool} did not answer: {e}")


def bound_digest(console):
    """The binding digest doctor reports, or None when the node is unbound."""
    for f in post(console, "doctor", {}).get("findings", []):
        if f.get("check") == "controller_binding":
            return (f.get("records") or [None])[0] if f.get("state") == "bound" else None
    return None


def bind(console, keyfile, rebind=False):
    held = bound_digest(console)
    if held and not rebind:
        return held, False
    key = nostr_sign.load_key(keyfile, make=True)
    pub = nostr_sign.pubkey(key).hex()
    begun = post(console, "join", {"controller": pub})
    event = (begun.get("controller_binding_ceremony") or {}).get("event")
    if not isinstance(event, dict):
        raise SystemExit(f"bind: join returned no ceremony event: {json.dumps(begun)[:300]}")
    done = post(console, "join", {"controller_event": nostr_sign.sign_event(event, key)})
    digest = bound_digest(console)
    if not digest:
        raise SystemExit(f"bind: the node does not report the binding after the ceremony: {json.dumps(done)[:300]}")
    return digest, True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--console", required=True)
    ap.add_argument("--key", required=True, help="the lab's test Nostr secret key file (made if absent, mode 0600)")
    ap.add_argument("--rebind", action="store_true", help="run the ceremony even if the node already reports bound")
    a = ap.parse_args(argv)
    digest, ran = bind(a.console, a.key, a.rebind)
    print(f"bind: bound, binding digest {digest}" + ("" if ran else " (already bound, left as it is)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
