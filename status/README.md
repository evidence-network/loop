# status/

- `findings.py` (loop.sh): writes one run's `index.md`, the link list.
- `status_page.py`: the loop status page. Twelve rows, one per step of the
  loop, each with a rehearsal cell and a live cell, the five numbers, and a
  badge (`badge.svg`) counting each population's rows (`4 PASS · 0 FAIL · 8
  not checked`), coloured by its worst row. The stats site
  (`site/build.py`) builds it into `_site/stats/status/`, draws its rows as
  the loop's path and its five numbers, embeds the badge and links here.

```sh
python3 -B status/status_page.py findings --lock versions.lock --out _site/status
```

- It reads the newest `findings/<date>-rehearsal[-N]/` and the newest
  `findings/<date>-live[-N]/` (loop.sh's and the lab's runs; the newest date, then the
  highest N) and shows them side by side; a rehearsal result is never added to
  a live one.
- An Ecosystems section shows one column per registered ecosystem (`ECOSYSTEMS`:
  `mesh` is `findings/`, `muse` is `findings/muse/`), each its newest run, with
  its own badge (`badge-<ecosystem>.svg`). An ecosystem with no run keeps its
  column, `NOT_CHECKED` with the issue that owns it. Nothing is added across
  ecosystems.
- A cell is green only when the step's `finding.json` is `PASS` and passes
  `steps/finding.py`'s check (every proof file present, each matching its
  sha256). `FAIL`, and a finding the check refuses (`MALFORMED`), are red.
  `NOT_CHECKED` lists each `{what, why, issue}`. A step with no finding reads
  `NOT_CHECKED` and the row names the issue that owns it.
- The page's own words say "checked", "verified" or "witnessed" only in a
  `PASS` cell. Text a finding supplies is shown as a quote of the finding.
- A `PASS` cell shows each gap its finding names: a sentence in `detail`
  starting `Outside this step's assertion: ` and ending with the issue as
  `owner/repo#N`. The cell shows it on a gap line, with the issue linked.
- Row 8 (Verify) is step 08's own finding, read like any other row. Step 08
  runs last in stage 1 and re-derives every other step's finding in the run
  from its proof bytes; a step it could not re-derive is in its `not_checked`,
  never in its PASS. Its `verified.json` is what steps 10 and 11 read.
- Row 10 (Judge) is step 10's own finding, read like any other row. Step 10
  reads only 08's manifest and the records of the steps it shows reproduced,
  in a window of every record up to the instant 08 ran, with no month boundary. A run without 08's manifest reads
  `NOT_CHECKED`; a step 08 did not reproduce is a `not_checked` entry on the row.
- Row 11 (Report) is step 11's own finding, read like any other row. Step 11
  renders only from 08's manifest, 10's Results and the deal and settlement
  records they cite, with a denominator beside every number and no score
  anywhere, and seals the page's digest in the reporting node's book. A run whose 10 is `NOT_CHECKED`
  reads `NOT_CHECKED`; a live `PASS` says its records are operator-collected
  from the lab's own nodes.
- Rows 4, 5, 6 and 9 are in `HELD_FOR_FLIP`: built and shown, marked held,
  until their place on the page is settled. Setting `DROP_HELD = True` drops them.
- The five numbers, each per population: minutes to the first sealed record
  (step 01), to the first answered request (step 07) and to the first
  countersigned report (held at its placeholder, even when step 11 passes,
  since nothing is countersigned yet). Step 01 uses
  the marker step's PASS finding `finished`; step 07 uses `answered_at` in the
  `first-answered.json` its finding names in `proof`, from a valid PASS or
  NOT_CHECKED finding (never a FAIL or malformed one), and the cell names the `ask` that file gives beside the number ("first answer: <ask>", plus "the request was not sealed before it was sent" when `sealed` is false). Each is minus the run's
  `run.json` `started` (loop.sh writes it before its first step, as the earliest start of the day's
  directory, a step `lab.sh` ran there earlier included), in seconds
  when under one minute. Admission decisions that were NO
  (M3) read step 04's `counts.json` proof (`refused` of `decisions`: the NO decisions) when its finding is a valid
  `PASS`, and show both numbers or neither. Filtered volume beside raw (M2) reads `raw` and
  `filtered` from `volume.json`, a proof file of step 06's PASS finding, and
  shows both or neither. A number with no valid event (a marker step with no
  `PASS`; for step 07, no valid PASS or NOT_CHECKED finding naming an answered ask) reads
  `not measured yet (M1|M2|M3)`, never 0. When the marker step has its event but the run has no start time, the cell says why: "not measured: this
  run did not record its start time (run.json)" when `run.json` is missing. `NUMBERS` in the source says
  which finding fields each one reads.

`tests/test_status_page.py` holds the page to this. The `stats` workflow runs
stage 1 and then step 07 (not in stage 1) on the rehearsal population itself and builds the page into
`_site/stats/status/` on every pull request, push to main, and nightly after the
loop run. Later it can read the loop run's findings artifact instead.
