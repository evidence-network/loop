# Step 10 Judge

`steps/10-judge --population DIR --out RUN/10-judge` runs a deterministic pack over a window of the records of
the loop run `RUN`. It seals the pack's Results and judges them on their bundles, offline. It reads only step
08's manifest (`RUN/08-verify/verified.json`) and the files that manifest lists for steps 08 shows as
`reproduced`. It never writes into another step's directory.

## What it reads

1. **08's manifest.** 08's `finding.json` passes `steps/finding.py`'s check and names `verified.json` in
   `proof`, with the sha256 the file has. The manifest is of this run's population. The as-of instant is 08's
   finding `finished`.
2. **The window.** Every record of the run up to the as-of instant, 08's finding `finished`: no month boundary and no
   minimum age, so a record made today is judged today (Ethan, 2026-10-31: "用 10 月份数据赶紧给我 run"). The
   Result's `x-window.through` and `x-evaluator.bounds` name the actual bound.
3. **The records.** A record is a primary record file of a reproduced step of the pack's kinds, with the sha256 08 lists for it: `deal.json` for 05 (rehearsal), and on a live run each held half, `<node>/held-<n>.json`, the counterparty capsule a node holds, when it is a host-served capsule (its `action_id`; a settlement leg held beside it is not a deal; binding.json `only`; its `-citing` and `-inclusion` files stay 08's proof; loop#250), `ledger.jsonl` for 06 on the mock rail and, on live, each node's `<node>/recorded.json` (the settlement legs and observations that node's ledger holds, which 06 keeps; a terms leg is settlement evidence under 06's kind, never a deal; Buzz 34760604), each `request.json` of an ask for 07, `dispute.json` for 09 (the referee's `outcome.json` stays 08's, beside it; `records` in `steps/10-judge.d/binding.json`). A live 09 keeps no `dispute.json` (Mesh disputes are adjudications only), so when 08 lists none its record is the referee's sealed `outcome.json` (`records_if_none`). So a count is of deals, settlements, asks and disputes, never of the bundles and proof files beside them, which stay 08's. A reproduced step of the pack's kinds in which 08 lists no such file is a FAIL.
   The kinds are 05 Deal (deals), 06 Settle (settlements), 07 Ask (asks) and 09 Dispute (disputes). The
   step's `finding.json` is read only after its bytes match 08's digest, and only for its `finished`. The
   step's records are in the window when that instant is, not when the dates inside a record's bytes say, since
   a record's own clock is the operator's. A Result's `generated_at` is the as-of instant, so a rerun over the same
   records has the same document. A record whose bytes no longer match 08's digest
   is a FAIL.
4. **Refused, not read, named in detail:** a file 08 does not list, a step outside the window, and a step
   that is not one of the pack's kinds. A step 08 did not reproduce is refused as not verified by 08, with a
   not_checked entry.

## The pack

The pack is deterministic, with no model call. It is the evidencebook-skills `inference-deals` pack at the
commit `versions.lock` pins (`inference-deals`; the repository has no release tags). `10-judge.setup.sh` fetches
that commit's contract and `compiled.json` into `$LOOP_TOOLS/packs/inference-deals-<sha>`, and the step reads
the terms (`come_through.paid_as_agreed`, `served_as_agreed`, `served_named_model`), their tier (all `must_have`)
from there. A pinned pack whose terms the binding does not bind, or with a term that is not
`must_have`, is NOT_CHECKED. The loop's own claims (07 asks, 09 disputes, the roll-up) carry the loop's own
`own_contract_ref`, never the pack's. No copy of them is in this repository: `steps/10-judge.d/binding.json` only
says which stand-in record files feed which term. When the pinned pack is not fetched, the step is NOT_CHECKED,
never a PASS. The pack's own evaluator reads a node's real Mesh book records. On a live run, live 05 keeps each
node's own chain (`<node>/ledger/capsules.jsonl`, binding.json `inputs`), and step 10 runs the pinned evaluator
(`scripts/evaluators/inference_deals.py` and the three modules it imports, at the `inference-deals` pin, each held to
its git blob id; `10-judge.setup.sh` fetches them beside the pack, none is kept here) over those chains, read whole:
its `collect` and `judge_deal`, per deal, with a `shop_preauth` deal not counted, as the pack's own run does
(loop#157). Only the bounds differ (Sherlock2 confirmed, Buzz b1effd94): a chain is in the window as its step is, by
the step's finding `finished` (item 3 above), never by the dates in its entries, so each chain is read whole, with no
bound on either side, as the halves beside it are; the pack's weekly `period_policy` is not used, and the Result says so
(`x-evaluator.bounds`). A chain is the node's whole ledger, so a later run's chain holds an earlier run's deals again:
each Result counts the deals in its own run's chains, and no reader sums `x-evaluator.exchanges` across runs. The bytes run are
the bytes held to their blobs, compiled from them (never a cached `.pyc`); pinned code that does not load here is a
not_checked entry, never a crash. Each chain is read once and held to 08's digest. Each 05 term's claim then carries
the evaluator's worst verdict over the deal sides that answer the term (`out_of_scope` ones left out;
`not_evaluable` when none does), the readings it used (`provider_states`, or `adjudicated` for a referee's sealed
verdict) and how many exchanges it saw; its grade is `countersigned` only when every answer is. It cites the chains
beside the halves, and the Result says the evaluator ran (`x-evaluator`). The chains are cited but are not records:
step 11 counts no deal from them. The rehearsal stand-ins are no Mesh book records, so there the step applies the
pack's terms to the results 08 re-derived, and a PASS names that as a gap on evidence-network/loop#157; on live, a
PASS names only the steps judged that way, with why: 06, whose kind binding.json gives no evaluator inputs, and a
05 whose proof keeps no node's own chain; step 11 says the same. An
evaluator not fetched or not at its blobs is a not_checked entry.

Each step gets one Result, with one claim per term. Each claim cites every record of the step:

| Step | Claim | Verdict |
|---|---|---|
| 06 | `come_through.paid_as_agreed` | from the result 08 re-derived: PASS `met`, FAIL `not_met`, NOT_CHECKED `not_evaluable` |
| 05 | `come_through.served_as_agreed` | the same; on live, with the nodes' own chains, the pinned evaluator's worst verdict over the deals |
| 05 | `come_through.served_named_model` | on live, with the nodes' own chains, the pinned evaluator's (its "provider states" reading, or a referee's sealed verdict); otherwise "provider states" (`self-attested`, `not_evaluable`) where no record carries `model_attestation`, never a judged match, and where one does, a not_checked entry |
| 07 | an ask is answered or its absence recorded | from the result 08 re-derived |
| 09 | the referee's sealed Result | cited and counted as found, never judged again |

A roll-up Result has one claim per finer Result. Each claim cites that Result's record id, with the worst
verdict of its claims.

## Sealing

The Results are sealed in a fresh scratch book under the population's judge key. On rehearsal that is the
committed TEST key, `population/rehearsal/step10/keys/`. On live it is the lab's own judge key
(evidence-network/loop#189), which `lab/lab.sh judge-key` makes on the lab host at `$LAB_DIR/judge/judge.pem`
(dir 700, file 600). Step 10 reads it from `LOOP_JUDGE_KEY`, default `$LAB_DIR/judge/judge.pem`. Only its public
half is in this tree, `population/live/step10/keys/judge.pub.pem`, and it signs the book's checkpoints too. The
book trusts only the committed public key, so a key that is not its private half is refused (FAIL). A key no
`LOOP_JUDGE_KEY` or `LAB_DIR` names, one not readable here, or one readable by group or others is NOT_CHECKED.
A judge key that is a node's (a population `node_id`, or the `key_id` of any `nodes/` entry) is FAIL.

The lab operator account that runs the nodes can read the live judge key: on the lab host it has passwordless
sudo, so no other account there would keep the key from it. Steven's GO is Buzz 23c5f8fd; "same operator,
disclosed" in place of "no node operator holds it" was ruled by Sherlock2 under Steven's delegation, Buzz
8a67299d. Every live detail says the judge key is held by the same operator and is not an independent judge's. An
independent judge needs a key held outside the organization.

1. Each record is published as a capsule whose payload is `{step, path, sha256}`.
2. `capsulectl book result build` seals each finer Result, then the roll-up. It refuses a claim citing a
   digest the book does not hold.
3. `capsulectl book disclose --root` writes one bundle per Result into `bundles/`.

The tools are capsulectl and capsulectl-book at their `versions.lock` pins, installed by `10-judge.setup.sh`.

## The judge, on the bundles alone

| Check | Holds when | Planted failure (tests/test_step10_judge.py) |
|---|---|---|
| verify | each bundle is `VALID` under `capsulectl verify --bundle` with an empty config, and every record in it is under the repository's judge key | the roll-up's bundle altered after sealing; a judge told another key |
| complete | the finer Results cite every record in the window | a Result that omits one record |
| exact | every record they cite is in the window | a Result that cites a record of a step outside the window |
| rollup | the roll-up cites exactly the finer Results, each the root of a `VALID` bundle | a roll-up that also cites a Result no `VALID` bundle is rooted on; a finer bundle altered after sealing |

Each plant has a mutant test: with that check switched off, the plant reads PASS.

**PASS** means every check held and every step 08 lists was reproduced. **FAIL** means a check did not hold.
**NOT_CHECKED** means the window holds no record, a step is not reproduced, a record carries
`model_attestation`, or 08's manifest or the tools are missing. Results over the records in the window are
still sealed and judged.

The proof is:

- `window.json`: the window, every record read with its sha256, every refusal;
- `bundles/*.bundle.json`;
- `verify/*.verify.json`: the verifier's verdicts.

## Rehearsal

A run is judged from its own `08-verify/` manifest. A run with none is NOT_CHECKED (nothing for 10 to read); there is no
stand-in run to fall back on. The tests copy the committed `population/rehearsal/step10/run` (four rehearsal findings
for 05, 06, 07 and 09 and the 08 manifest that reproduces them) as their base run. A step 08 did not reproduce adds its
own entry under the issue 08 names for it.

**Live:** 08 reproduces live step 07 (its asks, `*/attempts/*/*.request.json`), and not yet the other live
steps (evidence-network/loop#188). Live 10 seals under the lab's judge key (see Sealing), and it is
NOT_CHECKED where that key is not on the host it runs on.
