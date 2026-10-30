# Step 12 Join, live: the plugin's own join and doctor --probe

On the live population (probe backend `reporter`), step 12 asks each node
checkpoints and history_card/1 through the daily reporter's ask. For each node
whose own console the run holds (`--node-console NAME=URL`, or
`LOOP_NODE_CONSOLES`; the self node's is `--console`), it also calls the
pinned capsules plugin's own tools on that console. That console must first
answer as the node named (its `commitment` tool names the node's id): `join`
is a write, so it is never sent to another node.

## The calls, in order

1. `doctor {"probe": true}` (kept as `doctor-probe.json`). The plugin asks
   its own responder checkpoints, then history_card/1, as a stranger, in
   process.
2. `join {}` (kept as `join.json`), a re-join with no binding argument, so it
   stores no binding. Its commitment pair is kept as `commitment.json` and
   `disclosure-policy.json`.
3. `doctor {}` (kept as `doctor-after.json`), to read the binding again.

## What is judged

| Part | Holds | Fails | NOT_CHECKED |
|---|---|---|---|
| doctor's `probe_checkpoints`, `probe_history_card` | `match` | `mismatch` | the plugin's `NOT_CHECKED` (no checkpoint yet), or no such finding |
| doctor's `policy_in_commitment` | `present` | `missing` for a served commitment that does not verify or advertises another policy | `missing` because no commitment is served yet |
| join's commitment | verifies with step 03's checker, policy bytes included, under the listed `key_id`, for the listed `node_id` | any of those does not hold | join returns none |
| join's `joined` | `true` | | `false`: each `pending` line is the reason |
| the binding | bound before the re-join and the same binding after | bound before the re-join, then unbound or bound by another binding after | unbound before and after (no controller binding recorded: `join {}` sends none), or the state can't be read before join (join is then **not called**) or after it |

A node with no console given is NOT_CHECKED for all of these.

## The published commitment

A re-join does not rebuild the commitment: the pinned plugin keeps the one it
built and rebuilds it only when the node id, the policy digest, the latest
checkpoint or the held binding changes (`commitment_bridge.rs`, `own` and
`cached`). A new checkpoint rebuilds it, though, so the file published under
`nodes/commitments/<node_id>.json` can fall behind the node. When that file
exists, step 12 compares it with join's commitment. If they differ, the
detail says `<node>: commitment changed`, so a findings run republishes it.
A change is never a FAIL, and the line stays in the detail on a FAIL too.
With no published file, the detail says nothing is compared.

## What stays open

- The controller binding's revocation, and a check of the binding record by
  a verifier outside the node. Before doctor reports a binding bound, the
  pinned plugin checks its form, both signatures, its window, and that it
  binds the node key. It reads no revocation (the loop's own read is
  evidence-network/loop#138), and doctor names the binding
  only by its digest, so the loop has no record to check
  (action-state-group/agent-action-capsule#432).
- The signed commitment and the policy digest that the reporter's answers are
  matched against: `nodes/` entries are unsigned.
