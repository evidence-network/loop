# Step 09's rehearsal inputs

On Mesh a dispute is an adjudication only: the referee seals a Result about a
deal's exchange and delivers it to the nodes it concerns. There is no party-sealed
`dispute`, `dispute_response` or `close` on Mesh (Mesh-LLM/capsules-plugin#58).

- `dispute.json`: what steps/09-dispute acts out.
  - `nodes`: each node's id. The plugin keeps a node id apart from its key, so the
    rehearsal does too. alice's and bob's keys are the population's; the referee's
    key is `keys/referee/`. The node ids are made up for the rehearsal.
  - `deal`: the deal both halves record, and who sells. The two halves are a
    stand-in for step 05's halves of one deal. They are AAC capsules signed with
    alice's and bob's TEST keys, not step 05's own output.
  - `requester`: the party that asks the referee.
  - `referee`: who the requester asks.
  - `ruling`: what the referee rules, `contradicted:seller`, `contradicted:buyer`,
    `corroborated`, `inconclusive` or `not_comparable`. Step 04 reads the outcome
    this gives (step 09's `outcome.json`).
- `keys/referee/`: the rehearsal referee's TEST key (Ed25519, PKCS#8 and SPKI). It
  is published on purpose and never used live.

The announced keys are each node's public key. They stand in for the keys a Mesh
host announces for its peers.
