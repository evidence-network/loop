# joins/

Signed joins and leaves. A node can join the network's list with one signed
statement from its capsules plugin key, without a pull request and without
naming anyone. Joining stays opt-in: a node signs a join only when its
operator turns on the join switch in mesh-llm setup, which shows the switch
statement below.

The pull request path in `nodes/README.md` stays, for operators who want a
named listing.

This file defines the format and its check. How a join reaches the list is
not defined here.

## The switch statement

The statement shown under the switch, kept byte for byte in
[`SWITCH_CONSENT.txt`](SWITCH_CONSENT.txt) (one line, UTF-8, no trailing newline):

> I add this node to the evidence network to help improve the evidence layer. The network may ask it for its history summary, usually daily, and never for the content of any request, answer or payment. A summary of its answers, or that it did not answer, is published at https://evidence-network.github.io/loop/stats/. To leave, turn this switch off: the asks stop on the next run, and past reports stay.

It is the `nodes/` operator statement with one change: you leave by turning
the switch off, not by removing an entry. A `nodes/` entry still agrees to
`CONSENT` in `scripts/check_nodes.py` (shown in `nodes/README.md`); a join does
not, and a join that carries the digest of `CONSENT` is refused.

## A join

```json
{
  "kind": "join/1",
  "node_id": "<64 lowercase hex: the mesh node id>",
  "key_id": "<64 lowercase hex: the plugin's Ed25519 public key, raw>",
  "consent_sha256": "<64 lowercase hex: sha256 of joins/SWITCH_CONSENT.txt>",
  "at": "<YYYY-MM-DDTHH:MM:SSZ, UTC>",
  "spki": "<base64 of the Ed25519 public key in SubjectPublicKeyInfo DER form>",
  "sig": {"alg": "Ed25519", "canon": "jcs", "value": "<base64url signature, no padding>"}
}
```

- `key_id` is the 32 bytes of the plugin's Ed25519 public key, in hex: the
  same `key_id` as in `nodes/<node_id>.json`, in a commitment, and as
  `scripts/reporter_key.py` reads it from the plugin's `node-key.pub.pem`.
- `spki` carries that public key, so anyone can check that it is the key
  `key_id` names and that it made the signature.
- `consent_sha256` is the sha256 of the bytes of `joins/SWITCH_CONSENT.txt`,
  with nothing added (no newline). By signing it, the node's operator agrees
  to that text.
  If the statement changes, joins made under the old text no longer pass, and
  the node has to join again.
- `at` is when the join was signed.
- `sig` is an Ed25519 signature by the key in `spki` over the RFC 8785 (JCS)
  canonical form of the object without `sig`: the same signing as a commitment
  (`scripts/check_commitment.py`, `sign` and `verify_sig`).

A join holds no handle, no operator field, no Nostr key and no other personal
data. Any field not listed above refuses it.

## A leave

The same object, with `"kind": "leave/1"`, signed by the same key as the join
it ends, with `at` no earlier than that join. After a leave the node is not
asked again from the next run; reports already published stay.

To rejoin, sign a new join with a later `at`.

## The check

`scripts/check_join.py` checks one file, or every `*.json` in a directory
(default `joins`). It refuses a join or leave:

- with a missing or unknown field, or a field of the wrong shape;
- whose `spki` is not the key `key_id` names;
- whose signature does not verify;
- whose `consent_sha256` is not the digest of today's switch statement (if it
  is the digest of the `nodes/` operator statement or of an earlier statement,
  the message says which);
- whose `at` is more than five minutes in the future;
- that holds an email address, a phone number or a public IP address, by the
  rules of `scripts/check_personal_data.py`.

Over a directory, it also refuses a leave with no join for its `node_id` at or
before the leave's `at`, or signed by another key than that join.

Run it locally: `python3 scripts/check_join.py joins`

The test fixtures, signed with the rehearsal TEST keys, are in
`tests/fixtures/join/`, made by `make_fixtures.py` there.
