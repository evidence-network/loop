# Advertise and find (design, draft v0.1)

How a node says, to a stranger from another ecosystem, "ask me for evidence of what I did, against my checkpointed log, answered within X", and how the stranger finds and checks that. This is step 03 (Advertise) beyond the git list `nodes/commitments/` that only the loop reads.

| File | What it covers |
|---|---|
| COMMON_RULES.md | the one artifact (`commitment/1`), the verify steps for every carrier, witness rules and the fallback for nodes without a witness |
| CARRIER_ARD_NANDA.md | carrier 1: ARD file and the NANDA Index / AI Catalog directories |
| CARRIER_A2A.md | carrier 2: the A2A agent card |
| CARRIER_NOSTR.md | carrier 3: an addressable event on Nostr relays |
| FLOW.txt | the Nostr flow end to end, steps 01 to 08 |

Tracking: action-state-group/agent-action-capsule#541.

## Decided (2026-11-01)
- One signed `commitment/1`, three carriers: ARD/NANDA attestation entry, A2A card extension, Nostr addressable event. No discovery system of our own.
- Witness required, with a simple path for nodes without one: the verifier derives an "unwitnessed" label from missing receipts (the node never sets it), and the asker pins the first checkpoint it saw (trust on first use). Admission policies may refuse unwitnessed nodes.
- One design doc per carrier (this folder).

## Recommended, not yet decided
1. Switch on = a public announce including the stapled card; default off.
2. Nostr is always inline, since a NAT'd agent has nowhere to host a URI; over the relay size cap the event drops the card bytes and keeps the card digest. ARD/NANDA and A2A use uri + digest.
3. A placeholder addressable kind (30000-39999) for the announce, separate from the mailbox kinds; no NIP until a second implementation.

## Discussion record (2026-11-01)
**What exists today.** `advertise_evidence_requests` (default off) in the capsules plugin is only a value written into the signed commitment; nothing reads it. Step 03's carrier is the git list in this repository. The commitment already staples `history_card/1`. mesh-llm uses Nostr only for mesh discovery (kind 31990 listings), which are unauthenticated self-claims. Evidence requests already work both ways between peers; advertising makes the capability discoverable and changes neither who may ask nor what is answered.

**Questions and answers.**
1. *Loop only, or evidence layer?* Evidence layer. Not needed where the two sides already know each other (mesh peers). Needed for first contact by a stranger: that the node answers, where and over which binding, and the stapled card to read at Admit before paying.
2. *Does anyone else advertise this?* Not in ARD v0.91, nanda-index-v2 or A2A v1. All three advertise capabilities plus static attestations, and all three have the slot (`trustManifest.attestations[]`, `capabilities.extensions[]`); none carries a promise to answer evidence requests against a checkpointed log by a deadline.
3. *Is "we have a checkpoint" useful?* Alone, no. The value is the promise (what it answers, signed policy, `answers_within`, so a refusal or silence becomes a recorded fact), witness receipts (log age, a cheap anti-Sybil signal) and consistency proofs.
4. *Are Nostr and A2A directories?* No. Only the NANDA Index and AI Catalog are directories; ARD is a per-domain file like the A2A card. Nostr relays answer filter queries by kind, tag and author: a bulletin board, findable, with no search, ranking or single index; anyone writes and relays may drop events. A2A is hosting, not advertising, but the card is public and read before first contact. Found = directory search or relay query; fetched = ARD file, A2A card, or asking the node.
5. *Can a personal NANDA entry carry a trust manifest?* Yes at the index API (projnanda/nanda-index-v2 `server/src/routes/orgs.ts`: create :155/:444, update :170/:697, capped at :100; the only personal-path check rejects a domain at :200). host39 cards have no `extensions[]`. The gateway's `{"mode":"auto"}` registration takes no trust manifest, so the manifest is set by an index-API update after registering.

**Not built, on purpose.** A registry, a relay of our own, a NIP, new cryptography.
