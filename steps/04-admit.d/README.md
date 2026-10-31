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
commitment only. A decision found that is malformed, or that breaks a rule the plugin decides by (a verdict its source does not
give, a row value its rules cannot give, rows under another policy or missing under base/1, two decisions
disagreeing under one policy digest), is a FAIL; no decision, or
a node that cannot be read, is NOT_CHECKED. The routing path does not enforce
the decision (Mesh-LLM/mesh-llm#2061).

The verdict follows from its source alone, as the plugin decides: the allow
list gives GO, the deny list NO, an unreadable policy or target NO, and the
default the policy's default. On one node, under one policy digest, the
default is one verdict and one peer commitment gets one verdict and source.
The rows decide nothing, so a row never changes the verdict.

### The shape the current plugin seals

Three additions to the decision, all additive, are judged with the same
strictness as the rest (a member the step does not know is a FAIL):

- Each row carries the evaluator's `effect` (`none`: the five are record rows,
  which never change the decision) and, where it counts, `count` and
  `denominator` (integers, `count` at most `denominator`) (capsules-plugin
  PR #178). The effect is on every row or none, and goes with the next item.
- `reads` cites, after the history card, the price terms pack (`role: price`,
  with the operator profile as a commitment string or null) only when the
  price rule ran, then the base terms pack (`role: base`, its pack id and
  definition digest), in that order (PR #165, #178).
- A `price` member, on a decision about a peer the lists admitted
  (PR #165): `not_checked` or `unavailable` with one of the plugin's reasons,
  or `checked` with the guard's outcome (`allow`, `deny`, `escalate`), the two
  rates (integers or null) and each check's result (the step does not judge
  whether the outcome follows from the checks: that is the engine's). `checked` goes with the
  price pack read. A price that refuses (`unavailable`, or `checked` with an
  outcome other than `allow`) is exactly source `price_rule`, a NO; a profile
  that did not read is source `unreadable_profile`, a NO with no price. The
  price rule is the engine's, so the step judges only that the record is
  consistent, not the caps. A `price_rule` NO says nothing about the lists'
  verdict, so it is left out of the same-peer and default agreement checks.

### The five rows

The pinned plugin (capsules-plugin PR #116) seals the base policy's five rows
with each decision that names a peer, in this order: identity, key age,
performed-and-settled, disputes and corroboration. Each is `pass`, `fail` or
`not_read`. It also adds a second read, the peer's history card, after the
operator policy. A decision sealed by an earlier build has one read and no
rows, and the step accepts both shapes.

The step recomputes each row from what the decision sealed, by the rules in
the plugin's `admission/base.rs`:

- identity is `pass` exactly when the decision cites a card. With no card it
  is `fail` (the last answer did not verify) or `not_read`.
- key age is `not_read` when no card is cited. With a card it comes from the
  card's first checkpoint.
- performed-and-settled, disputes and corroboration come from one snapshot of
  the node's own chain. With no snapshot all three are `not_read`. With one,
  performed-and-settled and corroboration are `pass` or `fail`, and disputes
  is `not_read` only while the node holds no verdict about the peer.

A row the rules cannot give is a FAIL that names the row. Rows sealed under a
policy other than `base/1` are also a FAIL. Some values the record does not
fix: identity's `fail` or `not_read` with no card, key age with a card, and
the three local rows. The record cites the card only by a commitment, and
names the peer only by one, salted from the node's signing key. The plugin
keeps neither the card nor its snapshot. Each of those rows is NOT_CHECKED,
with its reason, in `<node>/rows.json`, and the finding names the gap
(evidence-network/loop#81).
