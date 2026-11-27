# Next step definitions: books on a shelf of facts

Status: **defined, not switched on.** No step runs these definitions yet. Each one is switched on
later, in its own pull request, together with the product versions that support it, as
[Switching a definition on](#switching-a-definition-on) says. Until then every step asserts what
it asserts today.

## Why the definitions change first

EvidenceBook is moving its book, its shelf and its deal profile to a new layout
([evidencebook#461](https://github.com/action-state-group/evidencebook/issues/461)):

- A **shelf** is everything one participant holds under one signing key: its books, a store, and
  one log of its own, the **shelf log**. The shelf log's entries are **book facts**: book X has n
  records at this moment, with this checkpoint. A witness sees only the shelf log.
- A **deal book** opens with its owner's agreement record and is never closed. Each side sends a
  record to the other as soon as it has sealed it. The receiver keeps it in its store, outside
  every book, and seals nothing for it; its own next record names it by digest in its **seen**
  field.
- The agreement lists **term rows**, each with a due date and a contest window. Each side says in
  its own records whether a row was met. A row's **status** is computed from both sides' statements
  and the dates; no record holds it.
- Admission decisions, and the checks a participant's own rules make before it acts, are records
  in its **journal**, one per shelf, which only the owner or a reader it appoints sees. A party's
  overall rules verdict on a deal is not one of them: it stays a record in its deal book, bound to
  the deal id and the pack digest, and reaches the counterparty as today.
- A new card format, `standard_history/3`, is counted from each book's latest fact, anchored at a
  witnessed shelf checkpoint, and can be counted again by a reader handed the whole shelf.
  `standard_history/1` and `/2` stay as they are; a reader of either given a `/3` card reports an
  unknown format.

Several steps assert something this layout removes: a witness receipt on a book's own checkpoint
(02), a record the receiver seals to cite the half it holds (05), a decision of GO or NO only, in
the buyer's deal book (04). The loop is the regression gate for that move: no step may go from
PASS to FAIL, or from PASS to `NOT_CHECKED`. So the definitions the code will be judged by are
written here first, reviewed here, and switched on only once a pinned release can meet them.

## Switching a definition on

- A next definition is named by its step and a version: **02 Book v2** is the next definition of
  step 02. Today's definition is v1. A step's finding says which one it judged in its `asserts`
  text, as today.
- One pull request switches one step on in one ecosystem. It changes `versions.lock` to product
  versions that support the new definition, changes the step to assert it, and adds the planted
  failure test each step has (`steps/README.md`, A step). It brings a passing rehearsal run:
  `loop.yml` dispatched on its branch (`gh workflow run loop.yml --ref <branch>`; pull requests do
  not run the loop, a heavy suite, CONTRIBUTING.md) and linked in the pull request with its result,
  and,
  since it changes a pin, the new live run the ecosystem gate asks for (`baseline/README.md`). If
  the new definition cannot PASS where v1 passed, the pull request does not merge.
- Each ecosystem switches on its own. An ecosystem whose pinned product does not support a
  definition keeps v1 for that step: the Mesh plugin has no shelf yet, and the evidencebook item
  leaves the Mesh shelf to a last, optional step, so on `mesh` steps 02, 03, 04, 05 and 12 stay at
  v1 until it does. For 03 and 12 there is a second reason: a node that serves the
  inference-service history profile does not also serve `standard_history/1` under the same key
  (Steven's ruling spec u508, the profile's §6.1 item 8), and whether a Mesh node may carry a
  `standard_history/3` card is open (evidencebook#461, its Mesh card question); these
  definitions do not assume it. The Goose nodes run on EvidenceBook and are where most v2
  definitions go first.
- One pin can serve both versions. `versions.lock` is one file: the `evidencebook` pin, for one,
  is what step 02's and step 05's drivers build with in every ecosystem. So a release a switch-on
  pull request pins must still meet v1 in each ecosystem that still runs that step's v1, or the
  ecosystem gate holds it; where it cannot, the step's driver keeps a pin of its own for v1 until
  that ecosystem switches on.
- A part that cannot be checked is `NOT_CHECKED` with the `{what, why, issue}` entry every step
  writes (`steps/finding.py`), naming the issue that owns the gap.
- Switching on never resets `baseline/`. Step 08 re-derives each new part from the run's bundle,
  so every new part keeps its proof bytes in the bundle.
- A product release that changes behavior a v1 step checks, before that step's v2 is switched on,
  is held by the ecosystem gate as any pin change is.

## The changes, step by step

| Step | v1 asserts (today) | v2 asserts | Switched on with |
|---|---|---|---|
| 02 Book | the record is in the book under a signed checkpoint, and the **book checkpoint's** witness receipt verifies (`steps/02-book`, `ASSERTS`) | the record is in the book under a signed book checkpoint, and a **stamp proof** carries that checkpoint to a **witnessed shelf checkpoint** | the EvidenceBook release with the shelf of facts (evidencebook#461, its library step) |
| 03 Advertise | the commitment is published, fetched and verifies; no stapled `history_card/1` or `standard_history/1` fails its checks (`steps/03-advertise`, `ASSERTS`, `ASSERTS_STAPLE`) | as v1, and the staple is a `standard_history/3` card **anchored at a shelf checkpoint**, whose stated level matches its anchor | a Go card builder that writes a `standard_history/3` card |
| 04 Admit | the decision is exactly one of GO or NO, sealed in the buyer's book before the deal record that cites it; no deal record cites a NO (`steps/04-admit`, `ASSERTS`) | four outcomes; the decision is a **journal** record; the agreement cites the digest of the final decision that allowed it; ASK and LIMIT have their own checks | the deal plugin release that seals admission in the journal |
| 05 Deal | both sides seal halves citing each other; each pushes its half at completion; the receiver holds the foreign bytes and **seals a record citing them** (`steps/05-deal`, `ASSERTS`) | each record is **sent when sealed**; the receiver holds it in its store and **seals nothing** for it; its next record names it in its **seen** field; both agreements name the same witnesses; each side's **first act waits on the other side's stamp proof** | the x-deal-v1 deal profile (evidencebook#461, its deal profile step), from the EvidenceBook release with its first-act gate |
| 09 Dispute | the referee's Result on a pair of twins is held by each node it concerns with the same bytes (`steps/09-dispute`, `ASSERTS`) | v1 stays the Mesh path; v2 adds the **personal-deal path**: dispute, ruling, the note that the ruling arrived, remedy | the x-deal-v1 deal profile, which defines the dispute and ruling records |
| 12 Join | one command joins the node; a stranger's probe (`checkpoints`, then `history_card/1`) verifies and matches the advertised policy (`steps/12-join`, `ASSERTS`) | the commitment's staple is the `standard_history/3` card **of the shelf**, anchored at a shelf checkpoint, and the stranger's probe compares against **that card** | the same release as 03 v2 |

Steps 01, 06, 07, 10, 11 and 13 do not change. Step 08 keeps its definition and its open gap: it
re-derives the run's findings, and an outside verifier that is not the loop's own is still missing
([loop#193](https://github.com/evidence-network/loop/issues/193)).

### 02 Book v2

The record is in the node's book at a stated position, under a book checkpoint the node signed that
verifies. A **stamp proof** for that book verifies:

- the **book fact** names the book (its `book_id`, the hash of the book's first record, and its
  `log_id`), and its `checkpoint_sha256` is the digest of a book checkpoint that covers the record:
  the same checkpoint, or a later one joined to it by a consistency proof that verifies;
- the fact's entry, in the encoding EvidenceBook's shared shelf vectors define (`testdata/` in
  that repository), is included in the shelf log under a shelf checkpoint the node signed, by an
  inclusion proof that verifies;
- that shelf checkpoint, or a later one joined to it by a consistency proof, carries a witness
  receipt that verifies under the key rules `witnesses.json` already has
  (`lab/witness/witnesses.json`, `about`): the entry is picked by the receipt's key id, each key
  counts from its `not_before`, and entries are only added, so a rotated-out key's receipts still
  verify.

A book checkpoint with no witness receipt of its own is no longer a gap: in the new layout a
witness never sees a book, only the shelf log. A stamp proof whose shelf checkpoint has no
witnessed successor yet is `NOT_CHECKED` for the witness part, as today's step reads a missing
receipt.

### 03 Advertise v2

As v1, and the commitment's staple is a `standard_history/3` card anchored at a checkpoint of the
shelf log the commitment names, and the level the card states matches its anchor:

- a card that states the **witnessed** level is anchored at a shelf checkpoint whose witnessed
  stamp has a time: a witness receipt that verifies under `witnesses.json` and carries a
  registration time;
- a card anchored at a shelf checkpoint with no such receipt states that it is **unwitnessed**. A
  shelf with no witness is a supported default, not a gap: its card passes when it says so, and
  fails when it claims the witnessed level.

A node still stapling a `standard_history/1` card keeps v1's check of it.

### 04 Admit v2

- The admission decision is exactly one of **GO, ASK, LIMIT or NO**, a record signed by the
  deciding node and included in its **journal** under a journal checkpoint it signed; the record's
  signature and that inclusion verify.
- **Order.** The deal's agreement record cites, by digest, the final decision that allowed it, and
  that decision is GO, or LIMIT. The digest reference proves the order: the step compares no
  witnessed times across the journal and the deal book. No agreement cites a NO, and after a NO
  there is no act. The agreement carries the digest only: the decision itself never reaches the
  counterparty, and its place in the journal is not shown.
- **ASK.** For a decision of ASK, the answer to the derived evidence request is received, admission
  runs again on it, and the act follows the new decision: GO, LIMIT within its limit, or NO with no
  act.
- **LIMIT.** The act stays within the limit the decision states.

The four outcomes are the admission packs' (`outcomes: ["GO", "ASK", "LIMIT", "NO"]` in
[`packs/admission-baseline/pack-source.yaml`](https://github.com/action-state-group/evidencebook-skills/blob/a24b28f5b77212c77cdc92f8c394ca8df47bef55/packs/admission-baseline/pack-source.yaml#L43)
at evidencebook-skills `a24b28f5`). v1, which judges the plugin-shaped GO or NO decisions
the step reads today (Steven's ruling evidence-network u140: step 04 judges sealed decisions only), stays for every
node whose release seals only those; v2 does not reverse it. A demo that lets a pick follow a NO,
as an assessment rather than a gate, is not a loop population and is not judged by this step.

The journal is the owner's. The step reads it the way step 04 reads a live node's ledger today,
through the node's own console, as the owner and never as a stranger; so this part is not one a
stranger can check, and a node whose journal the run cannot read is `NOT_CHECKED` naming that
gap.

### 05 Deal v2

- Both sides seal their records of one deal in their own deal books, and the two agreements cite the
  same offer and the same rows digest, and name the same witnesses, each by its key id (x-deal-v1
  `PROFILE.md` §5; Steven's ruling u604).
- Each side sends each record to the other **as soon as it has sealed it**, not only at completion.
- The receiver holds the received bytes in its store by digest and seals **nothing** for them. Its
  next record in that deal book names the latest record it held from the other side, by digest, in
  its **seen** field.

- **Stamp proofs and the first-act gate.** Each side sends the other a stamp proof for its deal
  book (02 v2's parts), which the receiver verifies against the records it holds. The order is the
  one the released x-deal-v1 profile sets (`PROFILE.md` §8, "The first-act gate";
  `xdealv1.ReceiveProfile.GateAct`; Steven's rulings u610 and u611):
  - each side's **first** `perform` or payer `settle` of the deal is made only after it holds a
    verified stamp proof from the other side that covers the other side's agreement. This holds on
    both sides, whether or not the agreements name a witness;
  - a payee leg, a statement and any act after the first are not gated, and may come before the
    other side's proof;
  - the acceptor stamps right after its agreement and sends the proof with it; the offerer stamps
    its agreement at once when its first act waits on the acceptor's, so neither waits on the
    other.

  The planted failures: an offerer whose first act comes before it holds a verified stamp proof
  covering the acceptor's agreement FAILs, and so does an acceptor whose first act comes before
  it holds one covering the offerer's agreement. A payee leg sealed before the other side's proof
  is not a failure. With a counterparty that runs no shelf layer (the holder's agreement says
  `counterparty_layer` `false`), the first act goes ahead and is not refused, and the holder's
  receipt and card say that the other side sent no stamp proof and the deal is counted from the
  holder's receipt only (the sealed report's `counterparty_proof` is `no_layer`); a receipt or
  card that leaves this out FAILs. Both sides of a loop deal run the layer, so the pull request
  that switches 05 v2 on plants one counterparty that runs none, and this case is checked, not
  only described.

- **The witnessed checkpoint.** Before either side treats the deal as settled, or relies on the
  other side's card, it asks for, and verifies, the other side's witnessed checkpoint: a shelf
  checkpoint with a witness receipt from a witness the agreements name, which verifies under
  `witnesses.json` as 02 v2 says; the consistency proof from the shelf checkpoint it kept to that
  one; and the stamp proof for the deal book's newest fact under it (evidencebook
  `docs/design/461-deal-profile.md` §7.2, "Later"; EvidenceBook's target design §4.3, "The witnessed
  checkpoint"). This part applies to a deal whose agreements name at least one witness and whose
  counterparty runs the layer. A deal whose agreements name no witness, or whose counterparty runs
  no layer, has no witnessed checkpoint to ask for: this part does not apply to it, and how such a
  deal counts on a card is the card's rule (`standard_history/3`), not this step's. The planted
  failure: a side whose sealed report says the deal is settled without having verified that
  checkpoint FAILs. Relying on the other side's card without it is not asserted yet. The record the
  step reads to see that a side verified it is defined by the pull request that switches this part
  on, together with the wire form. No wire form carries the receipts and the shelf consistency proof
  between the sides yet (that design note's Q8; agent-action-capsule#621 closed with the record push
  and stamp proof forms only), so this part is switched on in its own pull request once a form
  exists and a pinned release asks for the checkpoint. Until then 05 v2's `asserts` text does not
  claim it, so 05 v2 can PASS without it.

What becomes of each part of v1's assertion (`steps/05-deal`, `ASSERTS`):

| v1 part | In v2 |
|---|---|
| both sides seal halves of one deal | kept: each side's records in its own deal book |
| each citing the other half | replaced by the seen field: each record names the latest record held from the other side |
| and the other side's checkpoint | replaced by the stamp proofs above, which carry the other side's book checkpoint to its shelf checkpoint |
| each pushes its half at completion with `share_record_at_completion` on | replaced: each record is sent as soon as it is sealed |
| the receiver holds the foreign bytes by digest | kept: in its store, by digest |
| and seals a record citing them | dropped: the receiver seals nothing for a received record |
| acceptance is keyed on the evidence key under a controller declaration | kept unchanged: the receiver accepts the other side's records under that side's evidence key as v1 checks it, controller declaration and binding included |

On Mesh v1 stays until the plugin has a shelf: the plugin's citing record is today's link between
the two books.

### 09 Dispute v2

v1, the referee's adjudication between two nodes, stays the Mesh path, where a dispute is an
adjudication only. v2 adds the **personal-deal path**, beside it:

- one party seals a **dispute** record in its deal book that names one term row and the statements
  that exist on it;
- the adjudicator the agreement names seals a **ruling** on that row in its own case book, under its
  key; of its rulings on the row, the first one marked final is the one that decides it;
- each party's deal book holds a record that names the ruling that **arrived**, by digest, with the
  same ruling bytes as the adjudicator's;
- where the ruling orders a **remedy**, the remedy is a record in the deal book that points at the
  ruling, made by the date the ruling sets; a row whose remedy is not made by then computes as a
  ruling not honoured.

The step stays agnostic about which contract was disputed: it checks that the path happened and
that its records verify, not what the terms said. A referee that the deal terms name, recomputing
the terms over both halves and sealing a Result, stays a path of its own beside this one. On Goose,
step 09 has no input yet ([loop#315](https://github.com/evidence-network/loop/issues/315)).

### 12 Join v2

As v1, except the staple: the commitment's staple is the `standard_history/3` card of the node's
shelf, anchored at a shelf checkpoint with its level stated as 03 v2 says, and the stranger's probe
asks for that card and compares its answer against it. v1's `STAPLE_ASSERT` (no stapled
`history_card/1` of the commitment fails its checks) and v1's check of a stapled
`standard_history/1` card stay for a node that still staples one.

## New assertions

Three facts the new layout makes checkable that no step asserts today. Each sits in the existing
step named below, as a part of that step's v2, and no new step is added after 12. This is the loop maintainers' call and carries no product
meaning (Sherlock2's door confirmation, recorded on
[loop#456](https://github.com/evidence-network/loop/issues/456)). A new step would have needed a
number of its own, since steps numbered 13 and up are cross-ecosystem steps, and each of the three
checks what its home step already handles: the card 03 fetches, the stamps 02 reads and the rows 05
seals.

| | Asserts | Home |
|---|---|---|
| (a) whole-shelf replay and card recompute | handed every entry of the shelf log up to a card's anchor, with every fact's bytes and the opening of each book fact's status commitment (journal facts stay closed): there are exactly as many entries as the anchor's size states and they hash to its root; each opening recomputes its fact's status commitment; each `book_id`'s consecutive facts are joined by a consistency proof, or carry the same checkpoint when only received material changed; the card counted again from each `book_id`'s latest fact equals the card, and the verifier reports the **Recomputed** level. The card and the result state the shelf's coverage start, and no completeness is claimed before it: for a shelf migrated from today's layout that is its migration marker's checkpoint, or the shelf's first entry when the holder also handed over the material to recompute the old tree roots and it verified. Planted failures: an entry held back, a dropped book, a replaced book and a changed opening each FAIL. The checks cover books, not the journal | 03 Advertise, beside the staple it recomputes |
| (b) the write rule | between two stamps, a fact appears only for a book whose checkpoint grew or that received new material from the other side; a quiet book gets none; every stamp still ends with a shelf checkpoint, even when no book changed | 02 Book |
| (c) the computed status | the status each side computes from the term rows of the reference example deal matches the loop's own expected table, at each of the deal's dates. The table is the loop's: it is added to this repository by the pull request that switches (c) on, written from the example deal of EvidenceBook's target design (the one evidencebook#461 follows), and changed only through a reviewed loop pull request, never taken from the product's vectors. Where the product's result and the table differ, the step FAILs | 05 Deal |

Each comes with the planted failure test every step has, and is switched on with the EvidenceBook
release that implements it, as above.
