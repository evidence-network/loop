
Common rules: COMMON_RULES.md. Builds on the experimental Nostr mailbox carriage for the evidence exchange (action-state-group/agent-action-capsule#542): placeholder kinds, no NIP until a second implementation, relay list and mailbox key in the commitment.

Nostr is a relay protocol, not a directory, but relays answer filter queries (`REQ` by kind, author, tag). mesh-llm's own `discover` works exactly so (kind 31990 listings). That makes relays a dumb public bulletin board: findable by filter, nothing verified, no ranking.

## Advertise
One addressable event (placeholder kind in 30000-39999, so it replaces itself; `d` = node_id), signed by the mailbox key the commitment names, published to the relays the commitment lists (by default the relays mesh-llm already uses):
- tags: `["d", node_id]`, `["t", "evidence-commitment"]`, `["x", "<sha256 of commitment>"]`
- content: the commitment bytes (base64). This carrier is inline because a NAT'd agent has nowhere to host a URI.
- Switch `advertise_evidence_requests`: off publishes nothing (default); on publishes and republishes when the commitment changes.

## Find
`REQ {"kinds":[<kind>], "#t":["evidence-commitment"]}` on known relays, or `"#d":[node_id]` for a node you already know of. Mesh members read the same relays they discover meshes on.

## Size
Relays cap event size (limits vary by relay; unmeasured). If the commitment is over the cap, the event carries the commitment without the card bytes (card digest stays); the card is asked for directly (`history_card/1` is always askable).

## Attacks specific to this carrier
- Spam/Sybil events: discovery confers nothing; client caps per relay.
- Re-post by another key: shown only as "relayed", not "announced by the node".
- Relays drop/withhold: several relays; deadline -> recorded absence for the ask itself.
- Relay timestamps are not evidence: time comes from witness receipts or TOFU only.

## Open
- Placeholder kind number (addressable range), same no-NIP rule.
- Optional later: a tag in mesh-llm's 31990 listing pointing to this event (upstream PR, not needed).
