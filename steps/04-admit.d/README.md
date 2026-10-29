# Step 04 Admit: the gate before the deal

Admission is the gate before step 05's action. It answers "may this deal
happen?" with GO or NO and seals the answer. The loop keeps no Terms Pack and
evaluates no rule: why a decision was NO belongs to Mesh's admission policy
(Mesh-LLM/capsules-plugin). This step proves only that the gate ran first and
that the deal obeyed it.

## Rehearsal

Each scenario is an `admission_decision` block in the plugin's shape
(`population/rehearsal/step04/`). The buyer seals it in its book, then, on GO,
seals its record of the deal citing the decision; on NO, no deal record. The
judge decides on the bundle bytes: the bundle verifies under
`capsulectl book verify --bundle`; the decision has the plugin's shape and is
exactly GO or NO; a GO is sealed before the deal record that cites it; no
record cites a NO, up to the bundle's checkpoint.

`counts.json` holds the NO decisions out of all decisions, for the status page.

## Live

On the lab the pinned plugin's admission hook decides GO or NO from the
operator's allow and deny lists and seals an `admission_decision` record in the
node's ledger (the lab grants the hook, `lab/lab.sh`). The step reads those
records, read-only. It passes when every lab node reads, at least one decision
is found and each is GO or NO, names its source, cites the operator policy by
digest (a NO sealed with no readable policy has none) and names the peer by
commitment only. Otherwise it is NOT_CHECKED. The plugin seals no rows yet
(Mesh-LLM/capsules-plugin#116), and the routing path does not enforce the
decision (Mesh-LLM/mesh-llm#2061).
