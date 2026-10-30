# Step 08 Verify

`steps/08-verify --population DIR --out RUN/08-verify` reproduces every other finding in the loop run `RUN`,
offline, from the run's bundle and the keys the repository lists. `loop.sh` runs it last in stage 1, after the
steps it reproduces. It never writes into another step's directory and never changes a step's script: each
step's judge is loaded read-only from `steps/` with `importlib`.

## Which steps the run holds

The expected steps come from the run's own `run.json` `steps` list (loop.sh writes the step numbers it runs), never
from the directories present. A listed step with no `<NN>-*/finding.json` is a FAIL naming it, so deleting a step's
directory can't turn a run green or a NOT_CHECKED into a PASS. The stage's own step list is loop.sh's (`STAGE_<n>`), so a `run.json` that lists fewer steps than its stage
(a `--steps` subset, or a trimmed list) gives a NOT_CHECKED entry per step not run, never a PASS; an unknown stage is
NOT_CHECKED too. `verified.json` records `run.json`'s sha256 and its list. A `run.json` with no `steps` list is a NOT_CHECKED
entry: 08 can't tell a step left out from one never run. Step 04's scenarios are the repository's
(`population/<p>/step04/scenarios/`), never the list its own finding gives; one missing from its proof is a FAIL.
The same holds for the other trust anchors: 05's controller declarations match `step05/controllers.json`, 07's proof
holds every ask of every node pair, 09's `dispute.json` is `step09/dispute.json` byte for byte, and 02 with a
witness in the population must carry `witnesses.json`.

## What it does, per step

1. **Integrity.** `finding.json` passes `steps/finding.py`'s check: each proof file it names is there and
   matches its sha256. The finding is of the run's population. Each key the proof relies on is the key the
   repository lists for that party (below). A key read from the bundle is never trusted on its own.
2. **Reproduction.** Only the files the finding names in `proof` are copied into a scratch `<NN>-<name>/`. The
   step's own judge decides again on them, with the step's pinned verifiers (all local), so the signature,
   checkpoint and inclusion checks run again on the cited bytes. The re-derived `result` and `not_checked` must
   equal the recorded ones (`not_checked` compared entry for entry, in any order). For the files the status page
   reads a number from (04 `counts.json`, 06 `volume.json`, 07 `first-answered.json`), the re-derived bytes must
   equal the recorded ones. A mismatch is a FAIL naming the step.
3. **Coverage.** A step whose result can't be re-derived here is never passed on trust. It gets a not_checked
   entry `{what, why, issue}`.

08 is FAIL when any step fails 1 or 2, NOT_CHECKED when none fails but one or more is not reproduced, and PASS
only when every step present was reproduced. `verified.json` is the manifest steps 10 and 11 read: per step, its
recorded and re-derived result and not_checked, its status (`reproduced`, `failed`, `not_reproduced`), what did
not hold, and the sha256 of every file 08 read.

## The steps (rehearsal)

| Step | Judge re-run | Keys from the repository | Needs |
|---|---|---|---|
| 01 Seal | the step's own `--sealed` path on `capsule.json` | `key_id` is the node's key | aac-go `cross_language` |
| 02 Book | `decide` (the driver's `verify` on the dir) | alice's key; `witnesses.json` is the repository's | loop-step02-book |
| 03 Advertise | `main` with `rehearsal_node` reading the carrier from `carrier.bundle` instead of signing; `check_node` verifies what it fetches from HEAD | each commitment's `key_id` | git, openssl |
| 04 Admit | `verify_bundle` and `judge` per scenario; FAIL if a check failed, else PASS (as `main_rehearsal`) | bob's key | capsulectl |
| 05 Deal | `decide` (the driver's `verify` on the dir) | alice's and bob's keys; each declaration's `controller_key` | loop-step05-deal |
| 06 Settle | `decide` on the copies | node keys; `rail-key.pub.pem` is the repository's rail key | openssl |
| 07 Ask | `judge_run` and `conclude` | each `advertised/book.json` `node_key`, each advertised commitment's `key_id` | capsulectl, capsulectl-book |
| 09 Dispute | `decide` | each announced key (alice, bob, the step09 referee and requester) | openssl |
| 12 Join | `decide` on the cited proof (join.sh's kept exit code, the stranger's book) | each commitment's `key_id` | capsulectl |

**Not reproduced either:** a finding loop.sh wrote for a broken step (no judged result), a step whose tool isn't
installed where 08 runs, a step 08 has no re-derivation for, and every live finding. A live judge reads a lab
node's data dir or console, which the bundle doesn't carry, and the repository doesn't list the live node keys.

Owner: evidence-network/loop#24.
