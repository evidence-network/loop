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
  `git show`. Rehearsal nodes are never added to this repository's list.
- **live**: the harness never signs for a live node. A live commitment is
  signed by the node itself: the pinned capsules plugin signs and serves it
  (capsules-plugin PR #75, in the pin) and returns it to its operator, who
  adds it under `nodes/commitments/` by pull request. None is published yet,
  and no open issue tracks publishing it. The step reads the carrier's `HEAD`
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

Profile `evidence-commitment/0`. Exactly these members; any other is refused.

| Field | Type | Meaning |
|---|---|---|
| `profile` | string | `evidence-commitment/0` |
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
| `sig.alg` | string | `Ed25519` |
| `sig.canon` | string | `jcs` |
| `sig.value` | base64url | the signature |

The staple must be presented by its subject: the answer's `responder` is the
commitment's `key_id`.

The policy file is `{"profile": "disclosure-policy/0", "key_id", "policy":
{...}, "sig"}`, signed the same way by the same key.

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
checkpoint and of the staple are not scanned, by ruling: they are signed
artifacts the commitment carries whole, and a checkpoint or a history card
may hold sizes of its own. The commitment itself never states one.

## The check

`python3 -B scripts/check_commitment.py nodes/commitments` (the `commitments`
CI job) refuses a commitment with a missing or unknown field, a count, a bad
signature, a policy digest that does not match the policy bytes, a policy not
signed by the commitment's key, staple or checkpoint bytes that do not match
their digest, a staple not presented by its subject, or a file not named
`<node_id>.json`. A `policy_uri` with a scheme other than `file:` is reported
as not fetched, not refused.
