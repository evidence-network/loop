# Step 09's rehearsal inputs

On Mesh a dispute is an adjudication only: the referee compares two twins'
answers to one request, seals a Result, and delivers it to the nodes it concerns.
There is no party-sealed `dispute`, `dispute_response` or `close` on Mesh: the
capsules plugin dropped them, and seals the referee's verdict instead (its PR #102).

- `dispute.json`: what steps/09-dispute acts out.
  - `nodes`: each node's id. The plugin keeps a node id apart from its key, so the
    rehearsal does too. alice's and bob's keys are the population's; the requester's
    and the referee's keys are `keys/requester/` and `keys/referee/`. The node ids
    are made up for the rehearsal.
  - `exchange`: one request two twins (alice and bob) both served, and each twin's
    own response digest. Their served records are stand-ins for the records two
    serving nodes seal, signed with TEST keys.
  - `requester`: the node that holds both served records and asks the referee.
  - `referee`: who the requester asks; it is neither twin.
  - `ruling`: what the referee rules, `contradicted:alice`, `contradicted:bob`,
    `corroborated` (the referee found the twins' words the same), `inconclusive` or
    `not_comparable`. The outcome is stated per twin, in the plugin's buckets
    (step 09's `outcome.json`).
- `keys/requester/`, `keys/referee/`: the rehearsal requester's and referee's TEST
  keys (Ed25519, PKCS#8 and SPKI). They are published on purpose and never used live.

The announced keys are each node's public key. They stand in for the keys a Mesh
host announces for its peers.
