# Step 03 Advertise: the commitment

A node's **commitment** is one signed JSON object that says how the node can
be asked for evidence and what it can answer. It says what the node *can*
answer, never what it has done: it carries no counts.

The first carrier is this repository: `nodes/commitments/<node_id>.json`,
with the node's signed disclosure policy beside it at
`nodes/commitments/policies/<node_id>.json`. The entries in `nodes/*.json`
are unchanged; `scripts/check_nodes.py` reads only those, and
`scripts/check_commitment.py` reads only `nodes/commitments/*.json`.

## What the step asserts

`steps/03-advertise --population DIR --out DIR` signs each node's commitment
with the node key, publishes it to the carrier, reads it back with
`git show HEAD:nodes/commitments/<node_id>.json`, and verifies the bytes it
read back. The finding names the carrier.

- **rehearsal**: the carrier is a scratch git repository, kept in the out
  directory as `carrier.bundle` so anyone can clone it and re-run the
  `git show`. Rehearsal nodes are never added to this repository's list. On
  the Goose rehearsal population (`scripts/goose_rehearsal.py`), the harness
  signs nothing: each node's commitment is the `/1` one the node signed itself
  with evidencebook G1's `loop_advertise`, published byte for byte, and it must
  be under the node's own key.
- **live**: the harness never signs for a live node. A live commitment is
  signed by the node itself: the pinned capsules plugin signs and serves it
  (capsules-plugin PR #75, in the pin) and returns it to its operator, who
  adds it under `nodes/commitments/` by pull request. The step reads the carrier's `HEAD`
  (this repository by default, `--carrier` to name another), publishes
  nothing, and requires the commitment to be under the `key_id` that
  `nodes/<node_id>.json` lists. A node with no node id or no commitment there
  is `NOT_CHECKED`.

Only the rehearsal harness holds signing keys, and those are test keys. The
field names and canonicalization below are what this repository checks; a
node-side implementation can adopt them or diverge, and the checker then
changes with it.

PASS means, for every node: all required fields present and well formed, no
count, the signature verifies under the key in `spki`, the policy digest is
the sha256 of the policy bytes, the policy is signed by the same key, and the
staple and checkpoint bytes match their digests. The result covers what the
step asserts, and the step does not assert that the staple verifies. Two
parts are listed as `not_checked` on every PASS, because nothing pinned
verifies them here yet:

- the stapled `history_card/1`'s own signature (its bytes, digest, profile,
  derivation and responder are checked);
- the checkpoint attestation (step 02 Book's assertion; here only its bytes
  against the committed digest).

Per-node inputs are read from `DIR/advertise/<name>/` (`commitment.json`
with the settings below, `history_card.json`, `checkpoint.json`). A
rehearsal node without them gets stand-ins, and the finding says so.

## Fields

Two profiles verify. `evidence-commitment/0` staples one history card;
`evidence-commitment/1` staples one per evidence stream (see below). Exactly
these members; any other is refused.

| Field | Type | Meaning |
|---|---|---|
| `profile` | string | `evidence-commitment/0` (or `/1`, below) |
| `node_id` | 64 hex | the node's id in the list (`nodes/<node_id>.json`) |
| `key_id` | 64 hex | the node's Ed25519 public key, raw, in hex |
| `spki` | base64 | the same key as DER SubjectPublicKeyInfo; the signature is checked with it |
| `issued_at` | `YYYY-MM-DDTHH:MM:SSZ` | when the node signed this commitment |
| `ask.endpoint` | string | where an ask is sent, e.g. `mesh:<node_id>` |
| `ask.bindings` | list of names | the bindings an ask may use at that endpoint, e.g. `capsules-evidence-request/iroh` |
| `answers.subjects` | list of names | the kinds of subject the node answers about, e.g. `record` |
| `answers.derivations` | list of names | the derivations it answers, e.g. `checkpoints`, `history_card/1`; must include `history_card/1` |
| `lcap.policy_digest` | 64 hex | sha256 of the published policy file's bytes |
| `lcap.policy_uri` | string | where the signed policy is published; a relative path resolves against the commitment's directory |
| `lcap.predicate_classes` | list of `<kind>:<source>` | the kinds of claim the node can answer, e.g. `performed:counterparty_half` |
| `checkpoint.bytes` | base64 | the node's latest checkpoint attestation, whole |
| `checkpoint.sha256` | 64 hex | sha256 of those bytes |
| `human_gated` | boolean | true when a person must approve each answer |
| `answers_within` | ISO 8601 duration | how long an asker should wait before recording absence, e.g. `PT24H` |
| `history_card.derivation` | string | `history_card/1` |
| `history_card.bytes` | base64 | the stapled `history_card/1` answer, whole, in the capsules plugin's `evidence-interop/0` answer format |
| `history_card.sha256` | 64 hex | sha256 of those bytes |
| `controller_binding.digest` | 64 hex | optional: the binding digest (AAC Controller Binding v0 §4) of the record that binds the node key to its operator's key. Only the digest: the record, which names the operator's key, goes only to the counterparties that verify it. A node with no binding leaves the member out |
| `sig.alg` | string | `Ed25519` |
| `sig.canon` | string | `jcs` |
| `sig.value` | base64url | the signature |

The staple must be presented by its subject: the answer's `responder` is the
commitment's `key_id`.

### evidence-commitment/1: one card per book

A history card covers one evidence stream: the checkpoints of one book's log,
the witness receipts on them, and the consistency proofs between them. A node
that keeps more than one book staples one card for each. Profile
`evidence-commitment/1` has every member above except `history_card`, which
is replaced by:

| Field | Type | Meaning |
|---|---|---|
| `history_cards` | list, at least one entry, sorted by `stream` | one stapled card per evidence stream |
| `history_cards[].stream` | string | the log id of the book the card covers: plain names (letters, digits, `.`, `_`, `-`, each starting with a letter or digit) joined by `/`, e.g. `alice-book` or `goose/node` |
| `history_cards[].community` | string | the community the stream belongs to: `mesh-llm`, `muse` or `goose` (a closed list); `join.sh` writes `mesh-llm`, and evidencebook's Goose node `goose` |
| `history_cards[].checkpoint_key` | base64url, optional | the SPKI (DER) of that stream's checkpoint signer; with it, a verifier can check the card's signature |
| `history_cards[].derivation` | string | `history_card/1` |
| `history_cards[].bytes` | base64 | the card's `evidence-interop/0` answer, whole, as `history_card.bytes` in /0 |
| `history_cards[].sha256` | 64 hex | sha256 of those bytes |

Each card is checked as the /0 staple is (bytes against digest, the
`evidence-interop/0` answer format, derivation `history_card/1`, presented by
the commitment's own subject), and also: its `stream` is the card's own log
id (the `evidence_stream` its result names), no stream is stapled twice (compared without case, since step 12 writes a file per stream), its
community is on the list, and a `checkpoint_key`, where present, is an Ed25519
SPKI. Each refusal of a card is named, for example
`history_cards.duplicate_stream`, `history_cards.bytes_mismatch`,
`history_cards.stream_mismatch` or `history_cards.unknown_community`. Step 12
verifies the signature of each card whose entry names a `checkpoint_key`; a
card without one is listed as not checked.

The witnesses a card carries are not a field of the commitment: a reader takes
them from the receipts in the decoded card, and a card with none is
unwitnessed. A reader treats a /0 commitment's `history_card` as a list of one,
whose stream is the card's own log id, in the `mesh-llm` community.
`scripts/history_cards.py` reads both profiles this way: `history_cards(doc)`
returns `[{stream, community, derivation, sha256, witnesses, stream_started,
has_history}]`. `stream_started` is the date of the stream's first checkpoint;
`has_history` is false when the stream's checkpoints cover no record yet.

A node that clears a book's history starts a new stream, under a new log id,
and staples that stream's card. A cleared stream and one never started read
the same (`has_history: false`); nothing marks a clear. Rewriting or
truncating the old log is not a clear: it fails the card's consistency check
as before.

`join.sh` writes /1, one card per book the node serves, and so does
evidencebook G1's Goose node (`loop_advertise`), which staples its node book
`goose/node`. The rehearsal harness of this step still signs /0 for its mesh
test nodes; a published /1 commitment verifies here the same way.

The policy file is `{"profile": "disclosure-policy/0", "key_id", "policy":
{...}, "sig"}`, signed the same way by the same key.

### evidence-commitment/1: the Nostr mailbox route

A /1 commitment may also say how it is asked over Nostr, in one optional
member of `ask`. It settles the open items N-4 and N-5 of the AAC Nostr
mailbox binding v0 (`spec/bindings/nostr-mailbox-v0.md`), which leaves the
relay list and the mailbox key to this profile:

| Field | Type | Meaning |
|---|---|---|
| `ask.nostr` | object, optional | the node's Nostr mailbox route; exactly the two members below, both required. A node with no mailbox leaves it out |
| `ask.nostr.mailbox_key` | 64 lowercase hex | the mailbox key (binding §3): a secp256k1 x-only public key (BIP-340), on the curve. Requests are addressed to it, it signs the replies, and it signs the node's Nostr announce event |
| `ask.nostr.relays` | list of `wss://` URLs, at least one, distinct | the relays the node reads (binding §4.3, NIP-65 read relays): a requester publishes there, and the node announces there |

The key and the relays travel together: either alone is no route, and is
refused. The mailbox key is a route, not an identity: a verifier never
compares it with `key_id`, and it proves nothing about who answers (binding
§3); the answer's own signature does. It is learned from the commitment
because the commitment is signed by `key_id` (binding N-5). They are two plain
members, not a NIP-19 `nprofile` in a retention commitment's `route` (the
other place binding N-4 names): NIP-19's bech32 forms are for display and
sharing (binding §5.3), and an absence record's `route` `nprofile` is built
from these members. Profile /0 has no `ask.nostr`.

A relay URL is written in one form, so that "distinct" compares relays, not
spellings: `wss://host[:port][/path]`, the host a lowercase DNS name or a
bracketed IPv6 literal in its compressed form, a port from 1 to 65535 other
than the default 443, no path that is a bare `/`, and no credentials, query
or fragment. `wss://RELAY.example`, `wss://relay.example:443` and
`wss://relay.example/` are refused; write `wss://relay.example`.

Each refusal is named: `ask.nostr.missing_member`, `ask.nostr.bad_mailbox_key`,
`ask.nostr.bad_relay`, `ask.nostr.duplicate_relay`, `ask.nostr.no_relays`, and
`ask.nostr.malformed` for a wrong type or an unknown member.

## Signing

The signed bytes are the commitment without `sig`, in the canonical form of
**RFC 8785, JSON Canonicalization Scheme (JCS)**, the canonicalization the
capsule format (`canonicalization_id: "jcs"`) and capsule-emit use. The
signature is Ed25519 (RFC 8032) over those bytes by the node key, and is
verified with the SubjectPublicKeyInfo key in `spki`, which must match
`key_id`. The checker canonicalizes only what this schema uses: ASCII keys,
strings, booleans, null, and integers below 2^53 (in the policy); anything
else is refused, not canonicalized approximately. Signing and verifying run
the `openssl pkeyutl` command (OpenSSL 3.0 or later).

## The count rule

A commitment is refused when, anywhere in its own members:

- a key's name contains a count word as a token (split on `_`, `-`, `.` and
  camelCase): `count`, `counts`, `total`, `totals`, `tally`, `num`, `number`,
  `quantity`, `size`, `n`, `nb`; or
- a value is a JSON number, or a string made only of decimal digits.

No field above is a number; a duration is a string. The `bytes` of the
checkpoint and of each staple are not scanned, by ruling: they are signed
artifacts the commitment carries whole, and a checkpoint or a history card
may hold sizes of its own. The commitment itself never states one.

## The check

`python3 -B scripts/check_commitment.py nodes/commitments` (the `commitments`
CI job) refuses a commitment with a missing or unknown field, a
`controller_binding` that is not `{"digest": <64 hex>}`, an `ask.nostr` that
is not a mailbox key with its relays (above), a count, a bad
signature, a policy digest that does not match the policy bytes, a policy not
signed by the commitment's key, staple or checkpoint bytes that do not match
their digest, a staple not presented by its subject, a /1 card whose stream is
not its own log id or repeats another's, or a file not named
`<node_id>.json`. A `policy_uri` with a scheme other than `file:` is reported
as not fetched, not refused.
