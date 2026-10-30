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
   period and count are in `steps/10-judge.d/pack.json`.
3. **The records.** A record is a primary record file of a reproduced step of the pack's kinds, with the sha256 08 lists for it: `deal.json` for 05, `ledger.jsonl` for 06, each `request.json` of an ask for 07, `dispute.json` and `outcome.json` for 09 (`records` in `steps/10-judge.d/pack.json`). So a count is of deals, settlements, asks and disputes, never of the bundles and proof files beside them, which stay 08's. A reproduced step of the pack's kinds in which 08 lists no such file is a FAIL.
   The kinds are 05 Deal (deals), 06 Settle (settlements), 07 Ask (asks) and 09 Dispute (disputes). The
   step's `finding.json` is read only after its bytes match 08's digest, and only for its `finished`. The
   step's records are in the window when that instant is. A record whose bytes no longer match 08's digest
   is a FAIL.
4. **Refused, not read, named in detail:** a file 08 does not list, a step outside the window, and a step
   that is not one of the pack's kinds. A step 08 did not reproduce is outside the window too, with a
   not_checked entry.

## The pack

The pack is deterministic, with no model call. It uses the terms of the evidencebook-skills `inference-deals`
pack (all `must_have`) on the rehearsal stand-ins. That pack's own code is not pinned in `versions.lock`, so it
does not run here. A PASS names that as a gap on evidence-network/loop#111; pinning the pack is its own PR.

Each step gets one Result, with one claim per term. Each claim cites every record of the step:

| Step | Claim | Verdict |
|---|---|---|
| 06 | term 1, paid as agreed | from the result 08 re-derived: PASS `met`, FAIL `not_met`, NOT_CHECKED `not_evaluable` |
| 05 | term 2, served as agreed | the same |
| 05 | term 3, served the model it named | "provider states" (`self-attested`, `not_evaluable`) where no record carries `model_attestation`, never a judged match; where one does, a not_checked entry, since the pack has no recomputation for it yet |
| 07 | an ask is answered or its absence recorded | from the result 08 re-derived |
| 09 | the referee's sealed Result | cited and counted as found, never judged again |

A roll-up Result has one claim per finer Result. Each claim cites that Result's record id, with the worst
verdict of its claims.

## Sealing

The Results are sealed in a fresh scratch book under the rehearsal TEST judge key,
`population/rehearsal/step10/keys/`.

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
September 2026, and 08's manifest finished in October 2026. Its detail says the records are that committed
stand-in, not this run's, and `window.json` names its `source` as `stand-in`. So `steps/10-judge --population
population/rehearsal --out DIR/10-judge` on a run without 08 is PASS.

Given a real run with 08's manifest, the step judges that run. While its records are all in the open month, it
is NOT_CHECKED with that reason, and a step 08 did not reproduce (today, 12) adds its own entry.

**Live** is NOT_CHECKED: 08 reproduces no live finding, and the live population lists no judge key.

Owner: evidence-network/loop#24.
