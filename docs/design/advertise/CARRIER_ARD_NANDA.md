
Common rules: COMMON_RULES.md. Sources: ARD v0.91 (https://agenticresourcediscovery.org/spec/), projnanda/nanda-index-v2 `server/src/types/api/index-record.ts`.

## Advertise
ARD is a per-domain file; the NANDA Index and AI Catalog are the directories. Add one entry to the record's `trustManifest.attestations[]` (ARD entry or NANDA index record; same AI Catalog shape):
`{"type": "evidence-commitment/1", "uri": "<where the commitment bytes are served>", "mediaType": "application/json", "digest": "sha256:<hex>", "description": "Answers evidence requests about its own records; verify the commitment, not this entry"}`. `type` is the profile the bytes carry (`evidence-commitment/0` for a `/0` node). `digest` names one version and goes stale at the next checkpoint (COMMON_RULES "Digests" and rule 1); a node that re-signs updates the entry when it can, and a reader never rejects a newer valid commitment because of it. The entry sits in the trust manifest, not in the evidence-request discovery objects (COMMON_RULES "Not a discovery object").
- ARD: in `/.well-known/ard.json` (domain owners).
- NANDA Index: record create/update (`trust_manifest`). **Verified 2026-11-01** in projnanda/nanda-index-v2 main `server/src/routes/orgs.ts`: personal (no-domain) and domain registrations share the create (:155, passed at :444) and update (:170, passed at :697) handlers, both accept `trust_manifest` (capped at :100), and the only personal-path check rejects a domain (:200). Stored self-asserted.
- Gateway `{"mode":"auto"}` registration: its published contract (my.nandaindex.org/agent/SKILL.md, step 2) takes exactly `{"mode":"auto"}`, so it carries no `trust_manifest`. Register, then set `trust_manifest` with an index-API update (`PUT /api/v1/orgs/:org_id`, needs an `admin` token on the org; untested).
- host39 (card host, projnanda/host39 main 9a346c5): no `trust_manifest`, and its card `capabilities` is only `{streaming, pushNotifications}` with no `extensions[]`. So for a host39-hosted card the manifest goes in the index record, not the card.
- Add the tag `evidence-request` to the record's `tags` (string array in nanda-index-v2 6d08daa `index-record.ts`), so search can filter on it. Whether a `capabilities` token also helps is open.

## Find
- Search (`representativeQueries`/capabilities/tags) for `evidence-request`, or resolve a known identifier (`urn:air:...`), then read the attestation entry.
- The index marks manifests `trust_manifest_verified: false`; that matches our rule: verify the commitment itself.

## Needs a URI
The commitment must be hosted (domain, gateway, or a static host). A NAT'd agent with no host uses the Nostr carrier and puts that event's `naddr` address, with relay hints, as the `uri` (CARRIER_NOSTR "Advertise"); without the hints a reader would not know which relays to ask.

## Attacks specific to this carrier
- Registrant lists another node's commitment: harmless; key must match on the channel used.
- Index or host serves stale bytes: digest + newest `issued_at` + live check.
- Index takedown/hiding: other carriers still work.

## Open
- ~~Ask NANDA whether personal entries can carry `trust_manifest`~~ answered from the index code (above); the gateway path is an update after registering (above).
- Register the `evidence-commitment/1` type string? Rec: no registry; a URL to the AAC spec section as the type later.
