# Step 04's rehearsal inputs

Each scenario under `scenarios/<name>/` is one admission decision that `bob`
(the buyer) takes about `alice` (the seller): `decision.json`, a block in the
shape the capsules plugin seals as `admission_decision` (the verdict, its
source, the operator policy it read by digest, the peer by commitment). The
loop keeps no Terms Pack and decides nothing: why a decision was NO belongs to
Mesh's admission policy. The step proves the gate ran before the deal and the
deal obeyed it.

| Scenario | Verdict | Source |
|---|---|---|
| `go` | GO | `default` |
| `go-allow-list` | GO | `allow_list` |
| `no-deny-list` | NO | `deny_list` |
| `no-default` | NO | `default` |
| `no-unreadable-policy` | NO | `unreadable_policy` (no policy digest) |
