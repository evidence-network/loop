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

## The steps (live)

| Step | Judge re-run | Keys from the repository | Needs |
|---|---|---|---|
| 05 Deal | `judge_live` on the copied proof: `nodes.json` (the cutoff, the pins missing, which node was read), each node's copied `ledger/` files (held halves, provenance, inclusions, own chain), `controller-binding.json` and sharing report; the pinned driver and `cross_language` check each half again | nodes.json names the population's nodes; the senders' listed keys and revocation statements are the judge's own read of `nodes/`; the copies being the nodes' whole files waits on node-signed ledger checkpoints (Mesh-LLM/capsules-plugin#175); nodes.json's read states and pins missing are 05's record unless every node was read, and a cutoff is always named (#193) | the step 05 driver and aac-go `cross_language` at the pin |
| 09 Dispute | `judge_live` on the copied proof: `nodes.json` (the run's nodes, which were read, each announced key, a key not read), each node's copied ledger files and `twin-fault.json` | each population node is one the run names with the same node_id; each listed node's announced key is its `nodes/` key; an unlisted node's (the twins node's) is 09's reading (evidence-network/loop#209); nodes.json is 09's record unless every node was read with no key error (#193); the copies being whole waits on Mesh-LLM/capsules-plugin#175 | openssl 3+ |
| 01 Seal | `judge_live` on the copied proof: `nodes.json` (the pins the run found; per node unread, sealed or not sealed) and each node's `capsule.json` and `node-key.pub.pem`; the checks run again with the pinned `cross_language` | each kept `node-key.pub.pem` of a listed node is the key its `nodes/` listing publishes; an unlisted node's key is 01's word, and 01's signature check names that as not_checked; nodes.json's per-node state is 01's record (a not_checked entry on evidence-network/loop#193) unless no pin is missing and every node sealed its own capsule under its listed key in this run (the capsule's signed timestamp no earlier than 01's start) | aac-go `cross_language` at the pin (otherwise `not_reproduced`) |
| 02 Book | `judge_live` on the copied proof: `nodes.json` (pins; per node read or why not), each node's `inputs.json` (the keys and book coordinates read, the witness exit codes, a failed stage) and its proof files; `capsulectl verify --bundle` runs again on each bundle | `witnesses.json` is the repository's lab witness listing (and must be cited when a receipt is judged: the judge reads that copy); each listed node's `node_pub` is its `nodes/` key, and an unlisted node's key, book and trusted keys are 02's reading; a listed node's book id and checkpoint key must be named by a join commitment the node signed under `lab/runs`, otherwise they are 02's reading; the keys the book's profile trusts (`bound`) are in nothing the node signs, so they are always named; nodes.json is 02's record unless every node was read with nothing missing, and each node's `node_keys` (what its receipt is held apart from) must include every listed key (each a not_checked entry on evidence-network/loop#193 when not borne out), and each receipt is verified offline from the bundle by 02's judge (the step 02 driver's `lab-check`, and openssl over the signed bytes rebuilt from the bundle's checkpoint, evidence-network/loop#219); a run before that check (no `receipt_check` in inputs.json) keeps a not_checked entry that its receipt's state is capsulectl's reading (evidence-network/loop#219) | capsulectl and capsulectl-book at the pin; the step 02 driver and openssl 3 when a receipt is judged offline |
| 03 Advertise | `judge_live` on the copied proof: `carrier.json` (the carrier's HEAD commit; per node its listed `key_id` and which paths HEAD held) and each `<name>.commitment.json` and `<name>.policy.json` | each listed `key_id` is `nodes/<node_id>.json`'s in this checkout (otherwise `not_reproduced` on evidence-network/loop#193); when `head` is a full commit id this checkout holds, 08 works out each node's paths from that commit (listing, commitment, the policy it names), whatever carrier.json lists: each must match byte for byte and the listed key must be that commit's, otherwise FAIL; a path carrier.json lists that 03 never reads FAILs. A `head` that is missing, not a full id or not in this checkout is a not_checked entry on #193. That the commit was current when 03 ran is held to git history: each listing and commitment it holds must be what main held at 03's `started` (the newest commit on the first-parent line of `origin/main`, else `main`, as 08's checkout holds it). 03's start is the run's clock, bounded below by the node-signed timestamps of the capsules 01 kept (01 runs first; only a 01 this 08 run reproduced as PASS counts, so each signature was verified); with no such capsule, or a start before one, or no main, that is named on #193 | openssl 3+, git |
| 04 Admit | `judge_live` on the copied proof: `nodes.json` (each node read or not, its ledger's record count and the record numbers kept) and each node's `decisions.jsonl` | none: 04 reads the ledger lines as written (their signatures are a gap 04 names, evidence-network/loop#135); that `decisions.jsonl` and the record count are the ledger's whole waits on Mesh-LLM/capsules-plugin#175, and nodes.json's read states are 04's record unless every node was read (#193) | nothing outside the repository |
| 07 Ask (reporter backend) | `judge_reporter` and `conclude` on the copied proof: the attempts, each ask's `<stem>.ledger.json` extract, each node's `exchange-subject.json` and `commitment/` (the commitment its deadline came from, with its policy), and `ask.json` | each node's listed `key_id` (`nodes/<node_id>.json`), read by the judge itself; the kept commitment must be under it and verify | openssl (step 03's checker) |
| 06 Settle (Lightning) | 06's `main_lightning` on the copied proof: each node's `recorded.json` (06's reading of its ledger), then the ruled checks (reference, finality, band, rail_fee, exchange_match) again on them, and `counterparty_count` again from `counterparty.json`'s rows by 06's rule; `checks.json` and `counterparty.json` are written again and must be the cited bytes | none: nothing in the proof is signed by a key. A `recorded.json` for a name outside the population is a FAIL, and so is a counterparty row that is no check of one population node against another | nothing outside the repository |

Live 06 is re-derived on the Lightning rail only (any other live rail is `not_reproduced`). A reproduced live 06 adds
three not_checked entries to 08's finding: `recorded.json` is 06's reading of each ledger (capsules-plugin#175); the
preimage outcomes are 06's (it keeps the outcome, never the preimage); and each settlement bundle's bytes and the
check files are not in the proof, only the counts and the bundle's sha256, so the count is decided again from those
rows (both evidence-network/loop#193). A node with no `recorded.json` in the proof is `NOT_CHECKED` as in 06, so
dropping a node's file from the proof does not pass.

Live 07 decides from its proof alone: 07 collects first (it asks each node and reads the asker's ledger and the
asked node's data dir) and writes what its judge reads, then judges those files, so 08 running the same judge on
the cited files gets the same verdict, however the repository's commitments move later. A verification line is computed again from the reply, the announced key and
the listed key, and must be the one kept. `ask.json` must name every population node, and each node asked must
have exactly one attempt per question. The ledger extract is 07's reading of the asker's ledger, kept as bytes:
08 checks every rule on it, but not that the ledger held nothing more (the ledger is the lab node's; the extract
keeps its line count and sha256 at read time for an audit), which waits on node-signed ledger checkpoints
(Mesh-LLM/capsules-plugin#175). Nor are the replies' signatures checked again: live 07 takes the pinned plugin's
`verification` in each reply, as it did before. Which commitment applied is 07's `choice.json` too. So a
reproduced live 07 still adds three not_checked entries to 08's finding (one on capsules-plugin#175, two on
evidence-network/loop#193), and 08 never reads PASS over it. The listed
keys are read at 08's own checkout, so re-deriving an old run after a key rotation reads the new key (also #193).
Without OpenSSL 3.0 or later (Ed25519), live 07 is `not_reproduced`, never `failed`.

A live 04 or 07 finding whose proof lacks `nodes.json` or `ask.json` predates the shape 08 re-derives: it is
`not_reproduced`, never `failed`. The `<node>/rows.json` files live 04 writes again must be the cited bytes.
Live 04's `decisions.jsonl` is 04's selection of the node's decision lines: a reproduced live 04 adds a not_checked
entry on Mesh-LLM/capsules-plugin#175 that it holds every decision the ledger did.

A step's record of which node was read and which pins were missing is named on #193 only when the bundle does not
bear it out: when every node was read, sealed or answered, there is no node the record could have left unread.
What the bundle never carries stays named: 02's `bound`, 05's cutoff, and 07's reply signatures and commitment in
force. Live 09 is re-derived from
nodes.json and the copied ledgers; the twins run's fourth node has no `nodes/` listing to hold its key to, so its
announced key is a not_checked entry on evidence-network/loop#209.

The other live steps are `not_reproduced` on evidence-network/loop#188: their live judges read a lab node's data
dir or console while they decide, and their findings don't cite those bytes.

**Not reproduced either:** a finding loop.sh wrote for a broken step (no judged result), a NOT_CHECKED finding
of step 01 or 05 that cites no proof at all (rehearsal 01 writes one when a pinned tool is missing, and 05 when a node
holds no pushed half; live 01 always cites its nodes.json and is re-derived; 08 quotes the step's reasons as its claim and skips the key checks, so the ceiling is NOT_CHECKED, as for
a trimmed `run.json`; on a live run this reason comes before the live one), a step whose tool isn't installed where 08 runs (its `setup.sh`), and a step 08 has no re-derivation for. A run that lists fewer steps
than its stage names each step left out, owned by loop.sh's whole-stage run.
