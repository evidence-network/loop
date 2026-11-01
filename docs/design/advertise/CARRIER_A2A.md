
Common rules: COMMON_RULES.md. Source: A2A v1 agent card (`capabilities.extensions[]`, optional JWS `signatures[]`).

A2A is a communication protocol. It has no directory: the card sits at `https://<domain>/.well-known/agent-card.json`, so "advertise" means "say it in the card", and "find" needs the domain first. Strictly this is hosting, not advertising, but the card is public and read *before* first contact, which is when an asker decides whether to talk. What we host is a pointer to the signed commitment (its history cards stapled inside), not a bare card.

## Advertise
Two entries in `capabilities.extensions[]`, kept apart:
- The evidence-request entry, exactly as AAC IP §11.4.4 fixes it, when the node answers over A2A: `uri` exactly `https://datatracker.ietf.org/doc/draft-mih-agent-evidence-request/`, `required: false`, `params` = the manifest (IP §11.4.2) and nothing else. It is a discovery object (IP §11.4.3), so the commitment pointer never goes in it.
- A separate commitment entry: `{"uri": "<commitment extension URI, to be defined in AAC#541>", "description": "points to this agent's signed evidence commitment; verify the commitment, not this entry", "required": false, "params": {"commitment_uri": "...", "commitment_digest": "sha256:<hex>"}}`. This entry is the opt-in disclosure (COMMON_RULES "Not a discovery object"); a node with the switch off has none.
`required: false` on both: a client that ignores them still works.

## Find
- You already have the domain (a link, a deal, an earlier contact): fetch the card, read the extension.
- You do not: a directory points to the card (ARD/NANDA entries can list an A2A card URL). So A2A never finds by itself; it is reached through carrier 1 or a known URL.

## Verify
Common rules. The card's JWS, when present, authenticates the card (its signer is usually a domain or organization key, not the node's Ed25519 key) and is never compared with `key_id`. `key_id` is proven by the signature on the evidence answers the A2A endpoint returns (COMMON_RULES rule 2).

## Answering over A2A
The evidence request itself can ride A2A (the binding listed in the commitment), so an agent with an HTTPS endpoint needs no other channel.

## Attacks specific to this carrier
- Domain takeover swaps card and commitment: the new commitment is under a new `key_id`, so an asker who knew the old key sees a different node, and a TOFU asker's pins (per `key_id` and stream) do not carry over; nothing of the old node's history transfers.
- Card without JWS: allowed; trust is in the commitment anyway.

## Open
- Card hosts without `extensions[]` (NANDA host39 today) cannot carry this; such agents advertise through the index record (carrier 1) or Nostr (carrier 3).
- The commitment extension's URI: to be defined in AAC#541 (an AAC spec URL; no registry). The evidence-request entry's URI is already fixed by IP §11.4.4.
- The `ask.endpoint` value and binding token for an A2A node (card URL or service URL; the commitment's binding names, such as `capsules-evidence-request/iroh`, are not IP's tokens `evidence-request/1`, `http`, `a2a`, `mcp`): settled in evidence-network/loop#320 with AAC#541.
