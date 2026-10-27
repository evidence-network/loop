# status/

- `findings.py` (loop.sh): writes one run's `index.md`, the link list.
- `status_page.py`: the loop status page. Twelve rows, one per step of the
  loop, each with a rehearsal cell and a live cell, the five numbers, and a
  badge (`badge.svg`) with the worst row of each population. The report page
  embeds the badge and links here.

```sh
python3 -B status/status_page.py findings --lock versions.lock --out _site/status
```

- It reads the newest `findings/<date>-rehearsal/` and the newest
  `findings/<date>-live/` (loop.sh's runs) and shows them side by side; a
  rehearsal result is never added to a live one.
- A cell is green only when the step's `finding.json` is `PASS` and passes
  `steps/finding.py`'s check (every proof file present, each matching its
  sha256). `FAIL`, and a finding the check refuses (`MALFORMED`), are red.
  `NOT_CHECKED` lists each `{what, why, issue}`. A step with no finding reads
  `NOT_CHECKED` and the row names the issue that owns it.
- The page's own words say "checked", "verified" or "witnessed" only in a
  `PASS` cell. Text a finding supplies is shown as a quote of the finding.
- Rows 4, 5, 6 and 9 are in `HELD_FOR_FLIP`: built and shown while the
  repository is private. Setting `DROP_HELD = True` drops them at the flip.
- The five numbers, each per population: minutes to the first sealed record
  (step 01), to the first answered request (step 12) and to the first
  countersigned report (step 11), each from finding `started`/`finished` and
  an upper bound; decisions limited or refused on evidence (M3) and filtered
  volume beside raw (M2) are placeholders. A number that cannot be measured
  reads `not measured yet (M1|M2|M3)`, never 0. `NUMBERS` in the source says
  which finding fields each one reads.

`tests/test_status_page.py` holds the page to this. The `site` workflow runs
stage 1 on the rehearsal population and builds the page into `_site/status/`
on every pull request, push to main, and nightly after the loop run.
