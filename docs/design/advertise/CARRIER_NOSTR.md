
Common rules: COMMON_RULES.md. Builds on the experimental Nostr mailbox carriage for the evidence exchange (action-state-group/agent-action-capsule#542): placeholder kinds, no NIP until a second implementation, relay list and mailbox key in the commitment.

Nostr is a relay protocol, not a directory, but relays answer filter queries (`REQ` by kind, author, tag). mesh-llm's own `discover` works exactly so (kind 31990 listings). That makes relays a dumb public bulletin board: findable by filter, nothing verified, no ranking.

## Advertise
One addressable event (placeholder kind in 30000-39999, so it replaces itself; `d` = node_id), signed by the mailbox key the commitment names, published to the relays the commitment lists (by default the relays mesh-llm already uses):
- tags: `["d", node_id]`, `["t", "evidence-commitment"]`, `["x", "<sha256 of the commitment octets, bare 64 lowercase hex>"]`
- content: the commitment octets in base64url without padding, as the mailbox kinds carry octets (AAC#542 §4). This carrier is inline because a NAT'd agent has nowhere to host a URI.
- Its address, for a directory entry's `uri` (CARRIER_ARD_NANDA "Needs a URI"), is a NIP-19 `naddr` with the kind, the mailbox key, `d` = node_id and at least one relay hint, as a `nostr:` URI (NIP-21). It resolves only once the placeholder kind is fixed.
- Switch `advertise_evidence_requests`: off publishes nothing (default); on publishes and republishes when the commitment changes.

## Find
`REQ {"kinds":[<kind>], "#t":["evidence-commitment"]}` on known relays, or `"#d":[node_id]` for a node you already know of. Mesh members read the same relays they discover meshes on.

## Size
Relays cap event size (limits vary by relay; unmeasured), and the checkpoint and cards count toward it. The commitment is signed over every member, card bytes included, so the event never carries an edited commitment: a commitment with its card bytes removed fails its signature and step 03's member check. Over the cap, the event carries no commitment, only the `x` digest; a node that can host the bytes adds a `["r", "<https URI>"]` tag pointing to them, and one that cannot is asked for them directly. How a stranger asks for the commitment over the mailbox is open (AAC#541); until it is settled, a node over the cap with nowhere to host is not findable on this carrier.

## Attacks specific to this carrier
- Spam/Sybil events: discovery confers nothing; client caps per relay.
- Re-post by another key: an event whose author is the mailbox key the verified commitment names is "announced by the node"; any other author is "relayed". The author is never compared with `key_id` (COMMON_RULES rule 2).
- Relays drop/withhold: several relays; deadline -> recorded absence for the ask itself.
- Relay timestamps are not evidence: time comes from witness receipts or TOFU only.

## Open
- Placeholder kind number (addressable range), same no-NIP rule.
- Asking for the current commitment over the mailbox (COMMON_RULES rule 4; the size fallback above): AAC#541.
- Optional later: a tag in mesh-llm's 31990 listing pointing to this event (upstream PR, not needed).
