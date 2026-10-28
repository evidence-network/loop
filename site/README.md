# site/

The stats site: one static page, served at
`https://evidence-network.github.io/loop/stats/`, built from the scrubbed day
files in `stats/data/daily/` and the loop's findings, and nothing else. It is
the evidence network's, with an explainer for an operator new to the network.

```sh
python3 -B site/build.py stats/data/daily --findings findings --lock versions.lock --out _site
# writes _site/stats/index.html, _site/stats/status/ and _site/stats/data/daily/
```

- `stats_model.py` reads the day files, one `<YYYY-MM-DD>.json` per day in the
  fixed scrubbed form (sorted keys, 2-space indent, trailing newline), with
  the states of `scripts/report_layout.py`. It is strict: a file it cannot
  read, a key it does not know, an answer hash on an absence or a state its
  attempts do not settle fails the build, and nothing is written.
- `build.py` draws the network over time, newest day on the right of every
  chart, in order:
  - today in words, each count out of what it counts (nodes that answered on
    the newest day, records sealed, days missed in the window);
  - the pulse band, across the content width: one column per day for the
    newest 30 days, as wide as the width allows up to a cap and right-aligned
    (older days as thin columns, spare width on the left; on a phone it scrolls
    in its box), one square per listed node in its day state, the upper half
    `checkpoints` and the lower `history_card/1`. A missed day is hatched, a day
    with no file dashed; a triangle marks a node's first day and the day it left;
  - small multiples on one day axis, two per row on a laptop and one on a
    phone: nodes answering, records sealed per day (stacked by node; a count
    that covers more than one day is lighter, under a bracket), book growth per
    node, asks by outcome, days missed, and nodes listed with joins and leaves.
    Each is an SVG with a `<title>` and `<desc>`, a caption with its
    denominator and window, and its numbers in a table under `<details>`;
  - the loop: the twelve steps as tiles, rehearsal above live, never added
    together. PASS green, FAIL red, MALFORMED red with a dotted border,
    NOT_CHECKED slate; a ring for a PASS with a named gap; dashed for the held
    rows (04, 05, 06, 09) with the status page's note. Under each strip, the
    steps that passed per run. Each tile links its status row;
  - the five numbers, each as a sparkline over runs per population, or a
    dotted track with the milestone it waits for, never a value; the median
    time to the first sealed record over the last seven days of runs. Then
    exchanges with both halves held, a count of exchanges, not measured until
    step 05 has findings;
  - the nodes, day by day, as a heatmap, with each node's answers (attempts,
    log size, `answer_sha256`) in a table under `<details>`;
  - how to read the page, collapsed at the end.
  `stats_model.sealed_per_day` gives the records sealed per node per day: a
  node's `records_at_checkpoint` minus its last earlier one. A day with no day
  file, a missed run, a node whose count fell or no count to compare with is a
  gap, never zero or negative. Rehearsal nodes are not in the daily set and are
  never added to it.
- `template.html` is the page around them, including the explainer.
- With `--rehearsal stats/data/rehearsal` (the `stats` workflow passes it) and
  any slot file there, a second page, `_site/stats/rehearsal/`, shows the
  reporter rehearsal, a test: a **TEST** label, one row per 10-minute UTC slot
  from the first to the last, newest first, and **MISSED** for a slot the
  reporter marked missed or one with no file, as a missed day. The daily page
  gets one TEST line linking it and no rehearsal row. With no slot file the
  site is byte for byte as without it. `tests/test_rehearsal.py` holds the page
  to this.

The page has no scripts and loads nothing from elsewhere. Every count reads
"N of M ..."; other numbers are quoted record values or times. Nodes are
listed by node id, and the page says the order means nothing. There is no
score, rank or total per node. The page's own words say "checked", "verified"
or "witnessed" only for a `PASS` step. Nothing links under `reports/`: the
reporter's raw files are kept elsewhere and are not part of this site.
The page is static: inline SVG and CSS, no scripts, no fonts or images from
elsewhere, one set of colour tokens with a dark scheme, labels 12px or larger
at 4.5:1. Nothing in it comes from the clock, so the same inputs give the same
bytes. `tests/test_site.py` holds the page to all of this;
`tests/test_stats_redesign.py` builds it from `tests/fixtures/stats/` (day
files, a rehearsal findings fixture, a negative fixture with a missed day and
a count that fell), compares it byte for byte with
`tests/fixtures/stats/expected/index.html`, and reconciles every `<details>`
table with its SVG; `tests/test_stats_contrast.py` computes the contrast of
every text colour in both schemes. After a deliberate change to the page,
rebuild the expected page:

```sh
python3 -B site/build.py tests/fixtures/stats/daily --findings tests/fixtures/stats/findings \
    --lock versions.lock --out /tmp/site && cp /tmp/site/stats/index.html tests/fixtures/stats/expected/index.html
```

The `stats` workflow runs those checks, then builds the site on every pull request, push to main and
nightly (the `site` check), runs `.github/leak_lint.py` over the built site
(staged in a scratch git repository, fail-closed without the `LEAK_LINT_TERMS`
secret), and on main deploys it to GitHub Pages with `actions/deploy-pages`.
Pages must first be enabled by a repository admin (Source: GitHub Actions).
