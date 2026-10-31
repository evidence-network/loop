# Step 08 Verify

`steps/08-verify --population DIR --out RUN/08-verify` reproduces every other finding in the loop run `RUN`,
offline, from the run's bundle and the keys the repository lists. `loop.sh` runs it last in stage 1, after the
steps it reproduces. It never writes into another step's directory and never changes a step's script: each
step's judge is loaded read-only from `steps/` with `importlib`.

## What 08 is, and where it stops

**A self-check, not step 8.** 08 is the loop's own offline check of a run's findings. It is not step 8 of the loop,
which needs a verifier that is not ours: an outside developer who reproduces every finding offline
(evidence-network/evidencebook#41). Step 8 stays red until that verifier passes, whatever 08 reads (Steven's ruling,
`buzz://message?channel=054513b2-0a66-4c1e-885e-2af75c56233c&id=343238d4df741c30171f3ba0453bd8f9a133c70abb3f0ca6d6753a0cb03ee008&thread=cb1918aa2f3df0e05c201e7a0b1f4225713ac5555247454e02e4c8f519edd654`).

**What 08 catches.** Whoever publishes a run's bundle changing what a finding says after the run:

- a finding whose cited bytes were altered, or do not support its verdict;
- a verdict that rests on a key the repository does not list;
- a step whose decision cannot be reproduced from the cited bytes with the pinned tools.

It does not catch a listed node that lies consistently under its own key. Steps 09 and 10 and corroboration
between nodes cover that.

**What 08 trusts.** These are the trust roots. 08 does not re-derive them, and each is stated here once:

- **T1.** What a listed node signs about itself: its ledger content, its ledger checkpoints, its capsule times.
- **T2.** main's history and who can write to it, deploy keys included. This covers which `nodes/`, `lab/witness/`,
  `lab/runs/` and `nodes/revocations/` the checkout holds: 08 reads them from the checkout it runs in, whatever that
  checkout's `origin` is, so a fork or another clone of the same history re-derives a run the same way.
- **T3.** The pinned verifiers (`versions.lock`).
- **T4.** The run's own unsigned readings: `started`, 12's `bind_at`, 05's cutoff, which node was read, console
  replies, 04's build mark, 02's `bound`, and that the commit 03's carrier names was current when 03 ran.

Values withheld by design, such as 06's payment preimages, are not in the bundle, so 08 cannot check them.

Where a step below still names one of these as a not_checked entry (02's `bound`, 05's cutoff, 06's preimage
outcomes and others), the entry records that trust root for the run; it is not a check 08 could make. Moving those
entries into their own list in `verified.json` is not decided, and this file does not change that shape. Every
other not_checked entry is a step, or part of one, that 08 did not re-derive: a missing tool, an older proof shape,
a broken step.

**The stop rule.** A review finding becomes a new check in 08 only if the bundle's publisher could get a false
result from 08 after the run without holding a listed key or write access to main. Any other finding goes to one
issue for the next iteration and blocks nothing.

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
3. **Coverage.** A step whose result 08 does not re-derive here gets a not_checked entry `{what, why, issue}`. The
   trust roots above are what 08 takes as given.

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

## The steps (live)

| Step | Judge re-run | Keys from the repository | Needs |
|---|---|---|---|
| 05 Deal | `judge_live` on the copied proof: `nodes.json` (the cutoff, the pins missing, which node was read), each node's copied `ledger/` files (held halves, provenance, inclusions, own chain), `controller-binding.json` and sharing report; the pinned driver and `cross_language` check each half again | nodes.json names the population's nodes; the senders' listed keys are the judge's own read of `nodes/`; the revocations commit nodes.json keeps must be on this clone's origin/main, and when `nodes/revocations/` differs between it and origin/main the judge runs again with the lists there and must give the recorded result and not_checked (#281); each copy is checked against the node's signed ledger checkpoint (below); nodes.json's read states and pins missing are 05's record unless every node was read, and a cutoff is always named (#193) | the step 05 driver and aac-go `cross_language` at the pin; loop-ledger-check for a run since loop#288 |
| 09 Dispute | `judge_live` on the copied proof: `nodes.json` (the run's nodes, which were read, each announced key, a key not read), each node's copied ledger files and `twin-fault.json` | each population node is one the run names with the same node_id; each listed node's announced key is its `nodes/` key; an unlisted node's (the twins node's) is 09's reading (evidence-network/loop#209); nodes.json is 09's record unless every node was read with no key error (#193); each copy is checked against the node's signed ledger checkpoint (below) | openssl 3+; loop-ledger-check for a run since loop#288 |
| 01 Seal | `judge_live` on the copied proof: `nodes.json` (the pins the run found; per node unread, sealed or not sealed) and each node's `capsule.json` and `node-key.pub.pem`; the checks run again with the pinned `cross_language` | each kept `node-key.pub.pem` of a listed node is the key its `nodes/` listing publishes; an unlisted node's key is 01's word, and 01's signature check names that as not_checked; nodes.json's per-node state is 01's record (a not_checked entry on evidence-network/loop#193) unless no pin is missing and every node sealed its own capsule under its listed key in this run (the capsule's signed timestamp no earlier than 01's start) | aac-go `cross_language` at the pin (otherwise `not_reproduced`) |
| 02 Book | `judge_live` on the copied proof: `nodes.json` (pins; per node read or why not), each node's `inputs.json` (the keys and book coordinates read, the witness exit codes, a failed stage) and its proof files; `capsulectl verify --bundle` runs again on each bundle | `witnesses.json` is the repository's lab witness listing (and must be cited when a receipt is judged: the judge reads that copy); each listed node's `node_pub` is its `nodes/` key, and an unlisted node's key, book and trusted keys are 02's reading; a listed node's book id and checkpoint key must be named by a join commitment the node signed under `lab/runs`, otherwise they are 02's reading; the keys the book's profile trusts (`bound`) are in nothing the node signs, so they are always named; nodes.json is 02's record unless every node was read with nothing missing, and each node's `node_keys` (what its receipt is held apart from) must include every listed key (each a not_checked entry on evidence-network/loop#193 when not borne out), and each receipt is verified offline from the bundle by 02's judge (the step 02 driver's `lab-check`, and openssl over the signed bytes rebuilt from the bundle's checkpoint, evidence-network/loop#219); a run before that check (no `receipt_check` in inputs.json) keeps a not_checked entry that its receipt's state is capsulectl's reading (evidence-network/loop#219) | capsulectl and capsulectl-book at the pin; the step 02 driver and openssl 3 when a receipt is judged offline |
| 03 Advertise | `judge_live` on the copied proof: `carrier.json` (the carrier's HEAD commit; per node its listed `key_id` and which paths HEAD held) and each `<name>.commitment.json` and `<name>.policy.json` | each listed `key_id` is `nodes/<node_id>.json`'s in this checkout (otherwise `not_reproduced` on evidence-network/loop#193); when `head` is a full commit id this checkout holds, 08 works out each node's paths from that commit (listing, commitment, the policy it names), whatever carrier.json lists: each must match byte for byte and the listed key must be that commit's, otherwise FAIL; a path carrier.json lists that 03 never reads FAILs. A `head` that is missing, not a full id or not in this checkout is a not_checked entry on #193. That the commit was current when 03 ran is the run's reading (T4) | openssl 3+, git |
| 04 Admit | `judge_live` on the copied proof: `nodes.json` (each node read or not, its ledger's record count, the record numbers kept and its plugin build mark, which is 04's reading of the lab host) and each node's `decisions.jsonl` | each node's listed key, from the repository's `nodes/` (04 holds each decision record's signature to it, in the pinned aac-go, evidence-network/loop#135); `decisions.jsonl` and the record count are picked again from the node's checkpointed ledger copy (below), and nodes.json's read states are 04's record unless every node was read (#193) | aac-go `cross_language` at the pin (otherwise `not_reproduced`, even when no node kept a record); loop-ledger-check for a run since loop#288 |
| 07 Ask (reporter backend) | `judge_reporter` and `conclude` on the copied proof: the attempts, each ask's `<stem>.ledger.json` extract, each node's `exchange-subject.json` and `commitment/` (the commitment its deadline came from, with its policy), and `ask.json` | each node's listed `key_id` (`nodes/<node_id>.json`), read by the judge itself; the kept commitment must be under it and verify | openssl (step 03's checker) |
| 06 Settle (Lightning) | 06's `main_lightning` on the copied proof: each node's `recorded.json` (06's reading of its ledger), then the ruled checks (reference, finality, band, rail_fee, exchange_match) again on them, and `counterparty_count` from `counterparty.json`'s rows by 06's rule (each row's status against the signed bundle, its sha256 and disclosed count are 06's word; 08 decides again that the paired count is the books' pair count); `checks.json` is written again and must be the cited bytes, `counterparty.json` is validated as rows and carried | none: nothing in the proof is signed by a key. A `recorded.json` for a name outside the population is a FAIL, and so is a counterparty row that is no check of one population node against another | nothing outside the repository |
| 12 Join (reporter backend) | `judge_live` on the copied proof: `nodes.json` (the self node, whether a second console was given, which own consoles were held, the binding time and the main commit `nodes/revocations/` was read from), each node's reporter attempt, its plugin calls (`wire.json` and the reply bytes `doctor-probe.json`, `join.json`, `doctor-after.json`) and kept `controller-binding.json`; each kept verification line is computed again from its reply, join's commitment is verified under the listed key and written again, and a kept binding is checked again by `cross_language` | each node's listed `key_id`, read by the judge itself (`nodes/<node_id>.json`); the revocations commit is held to origin/main as live 05's (#281); doctor's findings and the fetch replies are console reads no key signs, and the replies' signatures are the pinned plugin's verification (both named on evidence-network/loop#193) | aac-go `cross_language` at the pin when a binding is kept (otherwise `not_reproduced`) |

Live 06 is re-derived on the Lightning rail only (any other live rail is `not_reproduced`). A reproduced live 06 adds
three not_checked entries to 08's finding: `recorded.json` is 06's reading of each ledger (capsules-plugin#175); the
preimage outcomes are 06's (it keeps the outcome, never the preimage); and each settlement bundle's bytes and the
check files are not in the proof, only each check's status, counts and the bundle's sha256, so the status against the
bundle is 06's word and 08 decides again only the paired count (both evidence-network/loop#193). A node with no `recorded.json` in the proof is `NOT_CHECKED` as in 06, so
dropping a node's file from the proof does not pass.

Live 07 decides from its proof alone: 07 collects first (it asks each node and reads the asker's ledger and the
asked node's data dir) and writes what its judge reads, then judges those files, so 08 running the same judge on
the cited files gets the same verdict, however the repository's commitments move later. A verification line is computed again from the reply, the announced key and
the listed key, and must be the one kept. `ask.json` must name every population node, and each node asked must
have exactly one attempt per question. The ledger extract is picked from the asker's ledger copy that 07 keeps beside the asker's signed ledger
checkpoint reply (`<stem>.checkpoint.json`, `asker-ledger/<asker>/capsules.jsonl`; Mesh-LLM/capsules-plugin#175, loop#276):
08 runs `loop-ledger-check` (steps/ledger-check.d, the checkpointed-local-log calls `capsules ledger-check` makes) on
the copy's first lines the reply covers, under the key the asker's `nodes/` entry lists, never the reply's own;
picks the ask's requests and outcomes again from those lines and must get the extract's, at the same line positions;
and every record must sit below the covered line count (an older genuine reply over a shorter copy fails). A refusal
or no reply, no named asker (`--second-node`) and a missing checker are NOT_CHECKED, never a pass. The copy has a
withheld payment preimage's line marked `x-loop-redacted`, so a chain line is taken by the capsule_id it states and
the records are read as written (loop#135). A run before loop#276 kept no checkpoint and still names the old gap.
Nor are the replies' signatures checked again: live 07 takes the pinned plugin's
`verification` in each reply, as it did before. Which commitment applied is 07's `choice.json` too. So a
reproduced live 07 still adds three not_checked entries to 08's finding (one on capsules-plugin#175, two on
evidence-network/loop#193), and 08 never reads PASS over it. The listed
keys and commitments are this checkout's (T2).
Without OpenSSL 3.0 or later (Ed25519), live 07 is `not_reproduced`, never `failed`.

A live 04 or 07 finding whose proof lacks `nodes.json` or `ask.json` predates the shape 08 re-derives: it is
`not_reproduced`, never `failed`. The `<node>/rows.json` files live 04 writes again must be the cited bytes.
Live 04's `decisions.jsonl` is 04's selection of the node's decision lines.

Live 04, 05 and 09 keep each copied ledger file with the node's signed checkpoint reply beside it
(`<node>/ledger/<file>.checkpoint.json`, loop#288): the step asks the node's console (`LOOP_NODE_CONSOLES`) first,
then copies the file and keeps the lines the reply covers. Their judges, run again here, check each copy with
`loop-ledger-check` under the key the node's `nodes/` entry lists (09's unlisted twins node: the key its data dir
announces, named above as 09's reading), and fail a copy that does not check, a reply with no copy, a copy longer
than its checkpoint, a lost reply, and a redaction marker anywhere but a chain settlement observation's withheld
preimage (`model_attestation.compute_attestation.x-mesh-settlement-v1.preimage`); 04 also picks its decisions again
from the copy, and fails a read node `nodes.json` does not mark when it marks others. A refusal, no console, no
listed key or no checker is NOT_CHECKED, never a pass, and 08 without the checker is `not_reproduced`. Such a step still rests on two
things the check does not cover, each a not_checked entry: a chain line, a withheld preimage's line included, is
taken by the capsule_id it states (loop#135); and that each reply was asked before its copy was read, and a file with
no copy held no line, is the step's word, since every genuine reply keeps checking a copy of its own size
(evidence-network/loop#193). A run before loop#288 kept no checkpoint and
names that its copies are whole on Mesh-LLM/capsules-plugin#175.

A step's record of which node was read and which pins were missing is named on #193 only when the bundle does not
bear it out: when every node was read, sealed or answered, there is no node the record could have left unread.
What the bundle never carries stays named: 02's `bound`, 05's cutoff, and 07's reply signatures and commitment in
force. Live 09 is re-derived from
nodes.json and the copied ledgers; the twins run's fourth node has no `nodes/` listing to hold its key to, so its
announced key is a not_checked entry on evidence-network/loop#209.

## Which repository inputs a live step is held to

A live step that holds keys to the repository (01, 02, 03, 04, 05, 07, 09 and 12) is re-derived with this checkout's
`nodes/` listings and commitments, `lab/witness/witnesses.json` and the join commitments under `lab/runs/*/join/`
(`LOOP_NODES_DIR` and `LOOP_LAB_RUNS` when set). Which of these main held, and when, is T2: 08 does not ask which
pull request published a step, and its result does not depend on this checkout's `origin`. 03's carrier commit
and 05's and 12's revocations commit are read from this checkout's history, so a clone that lacks them names that
(a not_checked entry, or `not_reproduced`: fetch main's history). A key rotated since a run is read as the new one.

Live 05 and 12 also read `nodes/revocations/` at the main commit their `nodes.json` keeps (`revocations_main`), which
must be on this clone's `origin/main` (a commit it lacks is `not_reproduced`: fetch main's history). `nodes.json` is
not signed, so that commit alone could be an older one, from before a statement was merged; 08 holds the list to
this clone's `origin/main` instead (evidence-network/loop#281). When `nodes/revocations/` differs between the kept
commit and `origin/main`, the judge runs again on a fresh copy reading it at `origin/main` and must give the
recorded result and not_checked (by what and issue). What this clone's `origin/main` holds is T2, so fetch first to
apply a statement merged since. The run's own start or binding time, which no node signs, never picks the list.

The other live steps are `not_reproduced` on evidence-network/loop#188: their live judges read a lab node's data
dir or console while they decide, and their findings don't cite those bytes.

**Not reproduced either:** a finding loop.sh wrote for a broken step (no judged result), a NOT_CHECKED finding
of step 01 or 05 that cites no proof at all (rehearsal 01 writes one when a pinned tool is missing, and 05 when a node
holds no pushed half; live 01 always cites its nodes.json and is re-derived; 08 quotes the step's reasons as its claim and skips the key checks, so the ceiling is NOT_CHECKED, as for
a trimmed `run.json`; on a live run this reason comes before the live one), a step whose tool isn't installed where 08 runs (its `setup.sh`), and a step 08 has no re-derivation for. A run that lists fewer steps
than its stage names each step left out, owned by loop.sh's whole-stage run.
