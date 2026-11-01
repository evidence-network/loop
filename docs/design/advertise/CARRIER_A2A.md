
Common rules: COMMON_RULES.md. Source: A2A v1 agent card (`capabilities.extensions[]`, optional JWS `signatures[]`).

A2A is a communication protocol. It has no directory: the card sits at `https://<domain>/.well-known/agent-card.json`, so "advertise" means "say it in the card", and "find" needs the domain first. Strictly this is hosting, not advertising, but the card is public and read *before* first contact, which is when an asker decides whether to talk. What we host is a pointer to the signed `commitment/1` (the history card stapled inside), not the bare card.

## Advertise
In `capabilities.extensions[]`:
`{"uri": "<AAC spec URL for evidence-request>", "description": "answers evidence requests about its own records", "required": false, "params": {"commitment_uri": "...", "commitment_digest": "sha256:<hex>", "bindings": ["a2a"]}}`.
`required: false`: a client that ignores it still works.

## Find
- You already have the domain (a link, a deal, an earlier contact): fetch the card, read the extension.
- You do not: a directory points to the card (ARD/NANDA entries can list an A2A card URL). So A2A never finds by itself; it is reached through carrier 1 or a known URL.

## Verify
Common rules; plus `key_id` must equal the key that signs the card's JWS if present, or the key the A2A endpoint proves in its evidence answers.

## Answering over A2A
The evidence request itself can ride A2A (the binding listed in the commitment), so an agent with an HTTPS endpoint needs no other channel.

## Attacks specific to this carrier
- Domain takeover swaps card and commitment: new key fails consistency with the previous checkpoint; TOFU askers see the break.
- Card without JWS: allowed; trust is in the commitment anyway.

## Open
- Card hosts without `extensions[]` (NANDA host39 today) cannot carry this; such agents advertise through the index record (carrier 1) or Nostr (carrier 3).
- Extension URI: rec the AAC spec section URL; no registry.
