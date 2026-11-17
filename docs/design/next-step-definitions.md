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
  failure test each step has (`steps/README.md`, A step). It brings a passing rehearsal run, and,
  since it changes a pin, the new live run the ecosystem gate asks for (`baseline/README.md`). If
  the new definition cannot PASS where v1 passed, the pull request does not merge.
- Each ecosystem switches on its own. An ecosystem whose pinned product does not support a
  definition keeps v1 for that step: the Mesh plugin has no shelf yet, and the evidencebook item
  leaves the Mesh shelf to a last, optional step, so on `mesh` steps 02, 03, 04, 05 and 12 stay at
  v1 until it does. The Goose nodes run on EvidenceBook and are where most v2 definitions go first.
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
| 05 Deal | both sides seal halves citing each other; each pushes its half at completion; the receiver holds the foreign bytes and **seals a record citing them** (`steps/05-deal`, `ASSERTS`) | each record is **sent when sealed**; the receiver holds it in its store and **seals nothing** for it; its next record names it in its **seen** field; both agreements name the same witnesses, and the **stamp proofs** travel in the order the profile sets | the new deal profile version (evidencebook#461, its deal profile step) |
| 09 Dispute | the referee's Result on a pair of twins is held by each node it concerns with the same bytes (`steps/09-dispute`, `ASSERTS`) | v1 stays the Mesh path; v2 adds the **personal-deal path**: dispute, ruling, the note that the ruling arrived, remedy | the new deal profile version |
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

The journal is the owner's. The step reads it the way step 04 reads a live node's ledger today,
through the node's own console; a node whose journal the run cannot read is `NOT_CHECKED` naming
that gap.

### 05 Deal v2

- Both sides seal their records of one deal in their own deal books, and the two agreements cite the
  same offer and the same rows digest, and name the same witnesses.
- Each side sends each record to the other **as soon as it has sealed it**, not only at completion.
- The receiver holds the received bytes in its store by digest and seals **nothing** for them. Its
  next record in that deal book names the latest record it held from the other side, by digest, in
  its **seen** field.

- **Stamp proofs.** Each side sends the other a stamp proof for its deal book (02 v2's parts),
  which the receiver verifies against the records it holds, in the order the deal profile version
  sets. The order EvidenceBook's target proposes: the acceptor stamps right after its agreement and
  sends the proof with it, and the offerer verifies it before its first act and does not act
  without it; the offerer stamps after its own agreement and first act. The planted failure: an
  acceptor that sends no stamp proof, and an offerer that then makes no act. This part follows the
  profile version that is released; if it sets another order, this part is amended before it is
  switched on.

v1's "the receiver ... seals a record citing them" is dropped. On Mesh v1 stays until the plugin
has a shelf: the plugin's citing record is today's link between the two books.

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

Three facts the new layout makes checkable that no step asserts today. Each can sit in an existing
step or in a new step after 12. Steps numbered 13 and up are cross-ecosystem steps, so a new
per-ecosystem step would need a number of its own; the maintainers decide. The placement below is
a proposal.

| | Asserts | Proposed home |
|---|---|---|
| (a) whole-shelf replay and card recompute | handed every entry of the shelf log up to a card's anchor, with every fact's bytes and the opening of each book fact's status commitment (journal facts stay closed): there are exactly as many entries as the anchor's size states and they hash to its root; each opening recomputes its fact's status commitment; each `book_id`'s consecutive facts are joined by a consistency proof, or carry the same checkpoint when only received material changed; the card counted again from each `book_id`'s latest fact equals the card, and the verifier reports the **Recomputed** level. The card and the result state the shelf's coverage start (a shelf migrated from today's layout starts at its migration marker), and no completeness is claimed before it. Planted failures: an entry held back, a dropped book, a replaced book and a changed opening each FAIL. The checks cover books, not the journal | 03 Advertise, beside the staple it recomputes |
| (b) the write rule | between two stamps, a fact appears only for a book whose checkpoint grew or that received new material from the other side; a quiet book gets none; every stamp still ends with a shelf checkpoint, even when no book changed | 02 Book |
| (c) the computed status | the status each side computes from the term rows of the reference example deal matches the expected table that EvidenceBook publishes with its row status function (evidencebook#461, acceptance 6), at each of the deal's dates | 05 Deal |

Each comes with the planted failure test every step has, and is switched on with the EvidenceBook
release that implements it, as above.
