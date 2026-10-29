# Step 04 Admit: the Terms Pack

At this level admission is a blocking yes/no. The buyer checks each offer
against a mini Terms Pack. The decision is GO or NO, and nothing else.

## Rules

Each rule compares one number on the offer with a bound, and names four things:

- the field on the offer;
- the operator (`<=`, `<`, `>=` or `>`);
- the bound, a decimal written as a string;
- the bound's unit.

The offer states each field as `{"value": "<decimal>", "unit": "<unit>"}`. A
rule applies when the offer states its field in the rule's unit. The decision is
NO when any rule that applies is unmet. It is also NO when the offer leaves out
a field the pack bounds, states it as something other than a plain decimal, or
states it in a unit the pack has no bound for. When none of these happen, the
decision is GO. Numbers are compared as decimals. A rule reads a number on the
offer, never wording and never the seller's past.

## The default

`terms-pack.json` ships these round bounds. They are defaults, and the operator
is expected to adjust them:

| Rule | Field | Bound |
|---|---|---|
| `price-usdc` | `price_per_inference` | at most 0.01 USDC |
| `price-sats` | `price_per_inference` | at most 10 sats |
| `amount-usdc` | `deal_amount` | at most 10 USDC |
| `amount-sats` | `deal_amount` | at most 10000 sats |

To adjust them, the operator passes their own pack with
`steps/04-admit --terms FILE`. Without the flag, the step uses the shipped file.

## The record

The decision names the pack's digest, which is the sha256 of the pack file's
bytes. It also names the pack's id and version, the offer's digest, and every
rule with its verdict. The buyer seals the decision in its book before it seals
the deal record that cites it. A NO has no deal record after it.

## Later levels

ASK and LIMIT, and the higher disclosure levels, belong to a later level. So do
the reads of the seller's history: its history card, the counterparty halves,
the claims held and the adjudications (Mesh-LLM/capsules-plugin#64).

## Live

On the lab the pinned plugin's admission hook decides GO or NO from the
operator's allow and deny lists and seals an `admission_decision` record in the
node's ledger. The step reads those records, read-only. It passes when every
lab node reads, at least one decision is found and each is GO or NO, names its
source, cites the operator policy by digest (a NO sealed with no readable policy has none)
and names the peer by commitment only. It evaluates no Terms Pack there. The plugin seals no rows yet
(Mesh-LLM/capsules-plugin#116), and the routing path does not enforce the
decision (Mesh-LLM/mesh-llm#2061).
