# Step 11 Report

`steps/11-report --population DIR --out RUN/11-report` renders the lab's report page from step 08's manifest, step
10's Results and the deal and settlement records they cite, and seals the page's byte digest in the reporting node's book. It shows that adjudications
happened over the window and that decisions cited them. It never writes into another step's directory.

## What it reads

1. **08's manifest.** 08's finding passes `steps/finding.py`'s check and names `verified.json` in `proof`.
2. **10's Results.** 10's finding passes the same check and is PASS. Only the bundles its `proof` names are read, and
   no two are the same Result. Each bundle is `VALID` under `capsulectl verify
   --bundle` and under the repository's judge key. The roll-up cites exactly the finer Results. Every record they
   cite has the sha256 08's manifest gives it, in a step 08 reproduced.

3. **The cited deal and settlement records,** for the exchanges figure only. 10's Results judge per step, so they
   can't tell paid from free. Each record's bytes must match 08's digest. A file no Result cites is not read.

An input that does not hold is a FAIL, and nothing is rendered. When 10 is NOT_CHECKED, or an input is absent, 11 is
NOT_CHECKED with the reason. `10-judge/window.json` is read only for its `source`, and the pinned pack (`judge10.load_pack()`, as step 10 reads it) only
for the terms' names.

## The page

Each figure is one claim of a report Result, shown as the claim's summary and citing its records by digest. Each
cited capsule's payload is the record's `{step, path, sha256}`.

| Section | Figure | Number and denominator |
|---|---|---|
| adjudications | deals, settlements and asks judged; disputes counted as found | k of n records of the kind 08 lists for reproduced steps; k of n steps |
| outcomes | one per pack term | met, not met and not evaluable, each k of n Results of the kind |
| decisions | the roll-up | k of n finer Results cited; met, not met and not evaluable, each k of n roll-up claims |
| exchanges | paid exchanges, each from the payer's sealed deal, when a cited record names a deal or an exchange | distinct deals of distinct exchanges |

The count comes from the records' contents, never their names. A deal is a distinct top-level `deal_id` in a cited deal record (05's);
a settlement line that carries a `deal_id` seals no deal. An exchange is a distinct top-level `exchange`
or `exchange_id`. The stand-in's deal names no exchange, so its page reads "1 of 4": the deal against the four exchanges 06's ledger holds.

On a live run, 05's deal records are the host-served halves each node holds (loop#250; a settlement leg held beside
them is no deal record). Each exchange is held on both nodes, the other's half on each, so the two halves are paired
exactly as step 05 pairs them (one requested and one served half of one `client_nonce` on two nodes, with the same
output digest) and count one deal per exchange, never two, citing both halves: the adjudications line names how many
deals they make up, and how many halves pair with no other node's. A live half names no top-level `deal_id`, `exchange`
or `exchange_id`, so it adds nothing to the exchanges line, which counts the payer's sealed deals and 06's exchanges.

For now only the payer seals a deal for a paid exchange, so each deal counts one paid exchange. A provider-side deal is
never expected or reported as missing, and no check compares two sides' deals. A PASS names that gap on
capsules-plugin#134. A free exchange gets no deal and no receipt, by design, so it stays in the denominator and is
never shown as a gap. More deals than exchanges can't be shown as a share, so it fails the denominators check.

There is no score, rank, rating or leaderboard.

## Sealing

The finding commits `report.json` and the page's sha256 (`report_html_sha256`), not the page: it is about 800 KB and
the pinned builder rebuilds it byte for byte from `result.bundle.json`. `--page PATH` leaves a local copy to look at.
`report.json` names what built it (`built_by`: the `--version` line of the pinned `capsulectl` and `capsulectl-book`,
whose build holds the viewer script). The pinned `capsulectl book report build --seal` renders the page from the report Result's bundle and seals the page's
sha256, the bundle digest and the Result root as a `report_page_seal` record, under a checkpoint. `book report check
--page` answers match or mismatch. The seal record is disclosed as `seal.bundle.json`, so the sealed digest stays
checkable after a scratch book is gone. Page text that names the two questions (did the evidence verify, is this the
page the publisher issued) is capsule-viewer#158, and a PASS names that gap.

## The checks

| Check | Holds when | Planted failure (tests/test_step11_report.py) |
|---|---|---|
| seal | `book report check` says match, and the disclosed seal record, `VALID` and under the book's key, holds the page's sha256 | the page's bytes changed after sealing |
| records | each record 10's Results cite has 08's sha256 | a manifest with one record's sha256 changed, with 08's proof digest resealed; without the reseal it fails 08's integrity check |
| denominators | outside "k of n" with k <= n, a figure's text holds a digit only in a step's name or a term id | a figure reading "3 paid exchanges" |
| cited only | the exchanges figure counts only records a Result cites, each matching 08's digest | an extra deal-like file in the step dir, not cited; a cited deal record changed after 10 sealed |
| rebuilds | two rebuilds of the page from `result.bundle.json` have the same bytes, and those are the bytes the seal holds | a rebuild that differs from the other, and rebuilds that are not the sealed bytes |
| result bundle | the report Result's bundle, which the page embeds, is `VALID` | a bundle whose verdict is not VALID |
| no paid product | no enterprise or paid-product text (prices and payments are allowed) in the page's bundle, its HTML outside scripts, or `report.json` | a figure reading "Upgrade to Enterprise" |
| no score | no score-like field or word in the page's bundle, its HTML outside scripts, or `report.json` | a figure with a `score` field |

Each plant has a mutant test: with that check switched off, the plant reads PASS. The builder's viewer script is
pinned code and isn't scanned. It holds a `rank` variable and the sentence "there is no overall score".

Countersigned means a second signer, holding a key the reporting operator does not control, signs the page seal. Step 11
seals under the reporting node's own key, so it countersigns nothing: the status page's "Minutes to the first
countersigned report" row stays at its placeholder even when 11 passes.

The proof is `report.json`, `result.bundle.json`, `seal.bundle.json` and `check.json`, plus
`standin/10-judge/` on the stand-in path.

## Rehearsal

The reporting book is a scratch book under the TEST key in `population/rehearsal/step11/keys/`. A run with neither
`08-verify/` nor `10-judge/` runs step 10 into `OUT/standin/10-judge`, which judges the committed stand-in run
`population/rehearsal/step10/run`, so rehearsal 11 is PASS. Its detail says the records are that stand-in. A real run
with 08's manifest reports on its own 10, which is NOT_CHECKED while its records are all in the open month.

## Live

Records come from each lab node's own data dir, collected by the operator, and 08 still reproduces them offline, so
the transport carries no trust. Never fetch them through a peer. The reporting book is the first population node's
join book (`book_dir`, with a profile named for the node), under its key in `data_dir`. A PASS says the records are
operator-collected from the lab's own nodes, and that this proves the report, not how an operator gets bundles from
peers. With no 08 and 10 outputs in the run, it is NOT_CHECKED with the reason. When 10 is NOT_CHECKED, 11
carries each of 10's not_checked entries over with the issue 10 names, so a same-day live run reads NOT_CHECKED
on the window, and a step 08 did not reproduce on 08's issue for it. 11 checks 10's Results under the committed
live judge public key, `population/live/step10/keys/judge.pub.pem` (evidence-network/loop#189), and a live PASS
says that key is held by the lab operator account that also runs the nodes, not by an independent judge.

## Open-month preview (`--preview-open-month`)

```
steps/11-report --population population/live --preview-open-month --run findings/<date>-live \
                --out preview/<date>-live/11-report [--page FILE]
```

Run it after `10-judge --preview-open-month` (see steps/10-judge.md) with the same `--run`, and `--out` in the same
preview directory: 11 reads 10's open-month Results from `preview/<date>-live/10-judge` beside `--out`, and 08's
manifest and the records from `--run`. It accepts only a preview 10 (a 10 whose detail starts with `finding.PREVIEW`)
whose `window.json` names `--run`'s 08 manifest by sha256, never runs 10 itself and never uses the stand-in; a run without the flag refuses a preview 10. Its `report.json`
says `preview: "open-month"`, and every finding it writes starts its `detail` with
`PREVIEW: open month, not a baseline result`, which `scripts/ecosystem_gate.py` refuses. `--out` may not lie inside
`--run`, `findings/` or `baseline/`. A preview seals the page only in a throwaway scratch book under freshly
generated keys in a temporary directory, on live too (its detail says "not a node's book"): it never opens a node's
`book_dir`, which is append-only and served to other nodes, and needs no node's `data_dir` or `book_dir`, so
nothing of a preview reaches a node's book or a public surface. The flag is the only change: the baseline report is as before (loop#242).
