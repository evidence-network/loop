
Decided 2026-11-01: go on one commitment, three carriers (ARD/NANDA, A2A, Nostr), no discovery system of our own; witness required, with a simple design for nodes without one.

## One artifact
The loop step 03 commitment, profile `evidence-commitment/1` (`steps/03-advertise.md`; finders also accept `/0`, which that step still verifies), signed by the node's Ed25519 key `key_id`. Its member table in `steps/03-advertise.md` is the authoritative list; in short: how to ask (endpoint, bindings, and, from evidence-network/loop#320, relay list and mailbox key), what it answers (subjects, derivations, predicate classes), the signed disclosure policy digest, `answers_within`, `human_gated`, the latest checkpoint, and one stapled `history_card/1` per evidence stream. No counts. Same bytes on every carrier, never an edited copy; carriers differ only in how they are found.

**Digests.** A digest is SHA-256 over the commitment octets exactly as served (never a re-serialization). Directory and A2A entries write it `sha256:<64 lowercase hex>`; the Nostr `x` tag writes bare 64 lowercase hex. Because the commitment embeds the latest checkpoint, its bytes change at every checkpoint, so a pinned digest names one version and goes stale.

**Not a discovery object.** The commitment carries a checkpoint, history cards and `issued_at`, which AAC IP §11.4.3 forbids in discovery objects (the manifest, the evidence-request A2A extension entry, the MCP tool definitions). Publishing it is a separate, opt-in disclosure, which is why the switch defaults off. It never goes inside those discovery objects; each carrier below gives it its own place. This is to be recorded in the spec in action-state-group/agent-action-capsule#541.

## Verify (every carrier, every time)
1. Ignore what the carrier says; take the commitment bytes (inline, or fetched from the uri). A uri carrier's digest that does not match means the pointer is stale, not that the bytes are forged; the entry's digest is a hint only. An asker that already holds a commitment from this node keeps the fetched bytes if they verify (rules 2 and 3) under the `key_id` it holds, with a newer `issued_at`. An asker with no earlier version (first contact from a directory record or an `naddr`) accepts the fetched bytes if they verify, takes their `key_id` as the node's key from then on, and shows the mismatch as "directory entry stale".
2. The commitment's signature verifies under `spki`, and `key_id` is that key. `key_id` is then proven only by the Ed25519 signature on the node's evidence answers (envelope or refusal), on whatever channel they arrive. It is never compared with a channel key of another algorithm: the Nostr mailbox key is a route, not an identity (AAC#542 §3), and an A2A card's JWS authenticates the card only.
3. Staple and checkpoint digests match; the newest valid `issued_at` wins.
4. Before relying, check the stream still extends the checkpoint you hold (the advertised one, or your pin, below). The profile has no request that returns one proof straight from an arbitrary earlier checkpoint (AAC IP P-13: a `cll-consistency-proof` only shows a checkpoint's `commitment` extends its own `prev_commitment`). So the asker asks the node for a fresh `history_card/1` (the stranger-safe derivation step 12's probe already asks), whose checkpoints run from the stream's first checkpoint to the current one, and walks that chain: the checkpoint it holds must appear in it, and each later checkpoint's own proof must extend the one before, up to the current checkpoint. A chain that skips, forks or no longer contains the held checkpoint fails. One proof straight from a held checkpoint is open, to be defined in AAC#541. The current commitment itself also comes from the node: over the mesh, the commitment the plugin serves; how it is asked over A2A and the Nostr mailbox is open, to be defined in AAC#541.
5. Discovery confers no trust; admission policy reads the cards.

## Witness required, and the simple fallback
- **Witnessed:** a card counts as witnessed only when its own signature verifies (under the stream's `checkpoint_key`) and its checkpoints' receipts verify offline under a witness key in the verifier's own configuration, as AAC's inference-service history profile already requires. The verifier then shows "witnessed by <witness> since <first receipt time>". Receipts the node stapled from witnesses the verifier does not know count for nothing: a node can mint its own.
- **Not witnessed (allowed, labelled):** a node may still advertise. The label is derived by the verifier from receipts that are missing or that do not verify under a configured witness, never set by the node, so it cannot lie about it: "unwitnessed: log age and timing are the node's own word" (or "receipts by unknown witnesses").
- **Trust on first use for the unwitnessed:** the asker keeps the first checkpoint it saw for each (`key_id`, stream) pair (size, root, when it saw it). Every later card for that stream must contain it and chain forward from it (rule 4). A stream missing from a later commitment is shown as "dropped since <time>", and a new stream as "new stream since <time>", so restarting a log under a new stream name never passes silently. That gives the asker its own private witness from first contact on, with no infrastructure.
- Admission policies may refuse unwitnessed nodes; the default policy admits them labelled.

## Carriers at a glance
| Carrier | What it is | Advertise | Find |
|---|---|---|---|
| ARD file + NANDA Index / AI Catalog | ARD is a per-domain file (`/.well-known/ard.json`); NANDA Index and AI Catalog are the directories that register or crawl records | attestation entry in the record's trust manifest | search / resolve by identifier in the directory; fetch the ARD file once you know the domain |
| Nostr | relay protocol; relays answer filter queries by kind, tag and author. A bulletin board, not a directory: no search by name or description, no ranking, no single index, anyone can write, relays may drop events | addressable event on the node's relays | filter query by kind + tag on known relays |
| A2A | communication protocol; card at a well-known URL, no directory. Hosting, not advertising, but the card is public and fetched *before* first contact | extension in the agent card pointing to the commitment (card stapled inside), not the bare card | only if you know the domain, or a directory points to the card |

## Found vs fetched (v0.1)
- **Found** (a stranger with no address): a directory (NANDA Index, AI Catalog) search, or a Nostr relay filter query.
- **Fetched once you know where:** the ARD file, the A2A card, or asking the node directly for its commitment.
Every path ends at the same verify step above; no carrier is trusted.


## Discussion record
See README.md in this folder.
