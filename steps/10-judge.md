# Step 10 Judge

`steps/10-judge --population DIR --out RUN/10-judge` runs a deterministic pack over a window of the records of
the loop run `RUN`. It seals the pack's Results and judges them on their bundles, offline. It reads only step
08's manifest (`RUN/08-verify/verified.json`) and the files that manifest lists for steps 08 shows as
`reproduced`. It never writes into another step's directory.

## What it reads

1. **08's manifest.** 08's `finding.json` passes `steps/finding.py`'s check and names `verified.json` in
   `proof`, with the sha256 the file has. The manifest is of this run's population. The as-of instant is 08's
   finding `finished`.
2. **The window.** It covers 12 whole closed calendar months before the as-of month, `[first of as-of month
   minus 12 months, first of as-of month)`. It is never rolling: it moves only when a month closes. The
   The length (12 months) is the pinned pack's `window.months`; the period (a calendar month) is the step's.
3. **The records.** A record is a primary record file of a reproduced step of the pack's kinds, with the sha256 08 lists for it: `deal.json` for 05, `ledger.jsonl` for 06, each `request.json` of an ask for 07, `dispute.json` for 09 (the referee's `outcome.json` stays 08's, beside it; `records` in `steps/10-judge.d/binding.json`). So a count is of deals, settlements, asks and disputes, never of the bundles and proof files beside them, which stay 08's. A reproduced step of the pack's kinds in which 08 lists no such file is a FAIL.
   The kinds are 05 Deal (deals), 06 Settle (settlements), 07 Ask (asks) and 09 Dispute (disputes). The
   step's `finding.json` is read only after its bytes match 08's digest, and only for its `finished`. The
   step's records are in the window when that instant is, not when the dates inside a record's bytes say, since
   a record's own clock is the operator's. A Result's `generated_at` is the window's end, so a rerun over the same
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
and the window length from there. A pinned pack whose terms the binding does not bind, or with a term that is not
`must_have`, is NOT_CHECKED. The loop's own claims (07 asks, 09 disputes, the roll-up) carry the loop's own
`own_contract_ref`, never the pack's. No copy of them is in this repository: `steps/10-judge.d/binding.json` only
says which stand-in record files feed which term. When the pinned pack is not fetched, the step is NOT_CHECKED,
never a PASS. The pack's own evaluator reads a node's real Mesh book records, which the rehearsal stand-ins are
not, so it does not run here: the step applies the pack's terms to the results 08 re-derived. A PASS names that
as a gap on evidence-network/loop#157.

Each step gets one Result, with one claim per term. Each claim cites every record of the step:

| Step | Claim | Verdict |
|---|---|---|
| 06 | `come_through.paid_as_agreed` | from the result 08 re-derived: PASS `met`, FAIL `not_met`, NOT_CHECKED `not_evaluable` |
| 05 | `come_through.served_as_agreed` | the same |
| 05 | `come_through.served_named_model` | "provider states" (`self-attested`, `not_evaluable`) where no record carries `model_attestation`, never a judged match; where one does, a not_checked entry, since the pack has no recomputation for it yet |
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

A run's records are made in the month it runs, so until that month closes they are outside the window. The
window is never shortened for that. Instead, when `RUN` holds no `08-verify/`, the step judges the population's
committed stand-in run, `population/rehearsal/step10/run`: four stand-in findings (05, 06, 07, 09) dated
September 2026 in their findings (the records' own bytes keep the clock of the day the stand-in was generated), and 08's manifest finished in October 2026. Its detail says the records are that committed
stand-in, not this run's, and `window.json` names its `source` as `stand-in`. So `steps/10-judge --population
population/rehearsal --out DIR/10-judge` on a run without 08 is PASS.

Given a real run with 08's manifest, the step judges that run. While its records are all in the open month, it
is NOT_CHECKED with that reason, cited as the pinned pack's window (12 whole closed months,
Steven's 2026-10-29 ruling), and a step 08 did not reproduce adds its own entry under the issue
08 names for it.

**Live:** 08 reproduces live step 07 (its asks, `*/attempts/*/*.request.json`), and not yet the other live
steps (evidence-network/loop#188). A same-day live run is NOT_CHECKED on the window: its asks are judged once
their month has closed. Past that, live 10 seals under the lab's judge key (see Sealing), and it is
NOT_CHECKED where that key is not on the host it runs on.

## Open-month preview (`--preview-open-month`)

To see 10 run on records whose month has not closed, without moving the baseline (loop#242):

```
steps/10-judge --population population/live --preview-open-month --run findings/<date>-live \
               --out preview/<date>-live/10-judge
```

It judges the open month, the calendar month containing 08's as-of instant (for 08 finished in October 2026,
`[2026-10-01, 2026-11-01)`), with the same pinned pack and, on live, the same judge key. `--run` is the loop run
that holds `08-verify/`; the step reads it and never judges the stand-in or writes into it. Its Results carry
`x-window.preview` and `periods` 1, and `window.json` carries `preview: "open-month"` and the sha256 of the 08
manifest it judged (step 11 refuses a preview of another run). The 12-closed-month window
above is unchanged and is still the only one a run without the flag uses (Steven's 2026-10-29 ruling).

A preview is never a baseline result. Every finding it writes, whatever its result, has a `detail` that starts with
`PREVIEW: open month, not a baseline result` (`finding.PREVIEW`), and `scripts/ecosystem_gate.py` refuses a findings
directory holding one. `--out` may not lie inside `--run`, `findings/` (or `LOOP_FINDINGS`) or `baseline/`: write it
to the git-ignored top-level `preview/`, or under `$LAB_DIR/preview/`. Neither `loop.sh`, the gate nor any builder of the site, the stats pages or `reports/` lists
`preview/`; it is never committed, and the status page and the site refuse a findings directory holding a
preview finding (`tests/test_preview_isolation.py`). Step 11 reports over it with the same flag.
