
Decided 2026-11-01: go on one commitment, three carriers (ARD/NANDA, A2A, Nostr), no discovery system of our own; witness required, with a simple design for nodes without one.

## One artifact
`commitment/1` (loop step 03 `evidence-commitment/1`), signed by the node key: how to ask (endpoint, bindings, relay list, mailbox key), what it answers (subjects, derivations), signed disclosure policy digest, `answers_within`, `human_gated`, latest checkpoint, stapled `history_card/1`. No counts. Same bytes on every carrier; carriers differ only in how they are found.

## Verify (every carrier, every time)
1. Ignore what the carrier says; take the commitment bytes (inline or uri+digest; digest must match).
2. Signature under `spki`; `key_id` equals the key the counterparty proves on the channel used (mesh host key, A2A card signer, mailbox key).
3. Staple digests; newest valid `issued_at` wins.
4. Before relying: ask the node for its current commitment and a consistency proof from the advertised checkpoint to the live one.
5. Discovery confers no trust; admission policy reads the card.

## Witness required, and the simple fallback
- **Witnessed:** card checkpoints carry witness receipts. Verifier shows "witnessed since <first receipt time>".
- **Not witnessed (allowed, labelled):** a node may still advertise. The label is derived by the verifier from the absence of receipts, never set by the node, so it cannot lie about it: "unwitnessed: log age and timing are the node's own word".
- **Trust on first use for the unwitnessed:** the asker keeps the first checkpoint it saw (size, root, when it saw it). Every later answer must prove consistency to it. That gives the asker its own private witness from first contact on, with no infrastructure.
- Admission policies may refuse unwitnessed nodes; the default policy admits them labelled.

## Carriers at a glance
| Carrier | What it is | Advertise | Find |
|---|---|---|---|
| ARD file + NANDA Index / AI Catalog | ARD is a per-domain file (`/.well-known/ard.json`); NANDA Index and AI Catalog are the directories that register or crawl records | attestation entry in the record's trust manifest | search / resolve by identifier in the directory; fetch the ARD file once you know the domain |
| Nostr | relay protocol; relays answer filter queries by kind, tag and author. A bulletin board, not a directory: no search by name or description, no ranking, no single index, anyone can write, relays may drop events | addressable event on the node's relays | filter query by kind + tag on known relays |
| A2A | communication protocol; card at a well-known URL, no directory. Hosting, not advertising, but the card is public and fetched *before* first contact | extension in the agent card pointing to the commitment (card stapled inside), not the bare card | only if you know the domain, or a directory points to the card |

## Found vs fetched (v0.1)
- **Found** (a stranger with no address): a directory (NANDA Index, AI Catalog) search, or a Nostr relay filter query.
- **Fetched once you know where:** the ARD file, the A2A card, or asking the node directly (`commitment/1`).
Every path ends at the same verify step above; no carrier is trusted.


## Discussion record
See README.md in this folder.
