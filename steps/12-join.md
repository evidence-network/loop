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
| the nodes/ entry against that commitment (loop#161) | the entry's `stranger_safe` is the signed policy's, and the commitment advertises each question it covers (`checkpoints`; `full_history` with derivation `history_card/1`) | a difference: the listing misstates what the node signs | no verified commitment this run: the answers are matched against the entry alone |
| the reporter's answers | verify under the listed key; a refusal only of a subject the **signed** policy does not list as stranger-safe | an answer under another key, one that does not verify, or a refusal of a subject the signed policy lists | no verifiable answer |
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

- The controller binding's revocation (evidence-network/loop#138: no
  revocation statement is read from the lab). The node key's role in its
  binding is checked from the verifier's report (claim and `delegator`,
  action-state-group/agent-action-capsule#468): in a `delegated_by` binding
  the node key must be the delegate, and a node key that is the `delegator`
  is a failure; `same_controller_asserted` and
  `same_organization_credentialed` give a key no role. The
  record itself is checked in a second language: `cross_language binding`
  (aac-go go/v0.16.0) runs on the record the node holds
  (`controller-binding.json` in its `data_dir`, or `binding.json` for a
  node `join.sh` joined), which must have the digest doctor reports, name
  the node key and apply now (form, both signatures and window). That is
  "the binding applies", not a trust verdict. For a Mesh node, a binding that
  does not apply is a failure; a record that cannot be read, another binding
  than doctor's, or a method or key type the verifier lacks is NOT_CHECKED.
  For a node `join.sh` joined, a record that does not hold in any of those
  ways is a failure, and the record checked is kept in the finding's proof.

## Rehearsal (probe backend `doctor`): proof step 08 reproduces from

Each node's finding cites, by digest: `<node>.join.log` and `<node>.join.exit`
(join.sh's exit code, kept even when it is non-zero), the commitment, policy,
doctor and probe files, and the stranger's book the kept answers are verified
again in (`<node>/probe/stranger/**`; its seeds are throwaway test keys).
`decide(work, population, started, pop_dir)` judges from those files alone,
with the same code a run uses and the population's nodes (a node whose exit
code is not kept is a FAIL), so 08 calls it to reproduce 12. A change to a file
the judge reads (exit code, commitment, policy, doctor, probe answers, the
stranger's book) gives FAIL; a change to any cited file is also caught by
08's digest check. `decide` writes into `work`: pass a scratch copy.
