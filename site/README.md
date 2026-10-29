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

## The watch page

`site/watch.py` builds a second page, served at
`https://evidence-network.github.io/loop/watch/`: the network as a graph, one
day at a time. It runs after `build.py` (which empties the output directory),
from committed files only:

```sh
python3 -B site/watch.py --daily stats/data/daily --rehearsal stats/data/rehearsal --findings findings \
    --nodes nodes --population population --out _site
# writes _site/watch/index.html, _site/watch/data.json and _site/watch/watch.js
```

- The reporter sits in the middle; each node listed in the day's file is a
  dot, by node id round a circle, in its day state. Each question the reporter
  asked a node is an edge: **answered** (a reply came back: solid, a dot at the
  end), **refused** (a signed `refusal`: dashed, a bar) or **absent** (an
  `absence`: dotted, a ring). A node is ringed on the first day it is in a day
  file; a node past its `left_on` is faded and dashed.
- A slider steps through the days (or the rehearsal's 10-minute slots); *Play*
  runs through them and *Replay* lights the shown day's edges in the order of
  their attempts. Live and the rehearsal are a toggle, never one graph.
- Tab to a node or hover it for its card: the operator handle, `host_build`
  and `plugin_version` from `nodes/<node_id>.json`, the day files it is in, its
  state and asks that day, and `records_at_checkpoint`. No amount or band.
- Steps 05 (deal), 06 (settle) and 07 (ask) add node-to-node edges, only from a
  finding of that step for the shown day that passes `steps/finding.py`'s
  check, and only between two nodes of the shown set; otherwise the page says
  "not measured yet" and names the step.
- `watch.js` is the only script on the site: handwritten, no library, nothing
  fetched. The page carries `data.json` inline. With scripts off, the tables
  under the graph hold the same counts. The `/stats/` page is unchanged and has
  no scripts. `tests/test_watch.py` holds the page to all of this, with
  fixtures in `tests/fixtures/watch/` (`make_fixtures.py` there writes them).

### data.json

Sorted keys; the same JSON is in the page. Only fields of the scrubbed set,
`nodes/` and findings: no answer bytes or hashes, no amount, no band.

| key | value |
|---|---|
| `format` | `watch/1` |
| `edge_kinds` | `ask`, `deal`, `settle`, `ask07`, each with what its edge is |
| `outcomes` | `answered`, `refused`, `absent` |
| `nodes` | node id → `{operator, host_build, plugin_version, listed: true}` from `nodes/`, or `{listed: false}` for a node no longer there |
| `reporters` | each reporter key id the days name, with the node id whose `nodes/` entry holds that key (or `null`) |
| `views` | `live` (the daily set) and `rehearsal` (the reporter rehearsal, a TEST), each `{label, unit, test, days}` |

Each of a view's `days`, from the first to the last, with a gap day too:

| key | value |
|---|---|
| `date` | the day, or the rehearsal slot `YYYY-MM-DDTHHMMZ` |
| `run` | `settled`, `missed`, or `no_file` |
| `file` | the day file under `stats/data/`, or `null` |
| `listed` | the day file's `listed`, or `null` |
| `reporter_key_id` | the day file's, on a settled day |
| `nodes` | each node in the file: `node_id`, `state` (both questions read together, as the stats page reads them), `questions` (`checkpoints`, `history_card/1` → its state), `asks` (`answered`, `refused`, `absent`, of 2), `records_at_checkpoint`, `days_listed` (the day files it is in, to this day), `first_day`, `node_line`, `left_on`, `left`; then each node that left and is not in the file: `node_id`, `left_on`, `left: true`, `days_listed` |
| `edges` | the reporter's asks: `kind: ask`, `from: reporter`, `to`, `question`, `state`, `outcome`, `at` (the settling attempt) and `attempts`; then step edges: `kind`, `from`, `to`, `outcome` |
| `measured` | per kind: `ask` → `{measured, source}` or why not; `deal`, `settle`, `ask07` → `{measured: false, step, says: "not measured yet: ..."}`, or `{measured: true, step, finding, result, drawn, not_drawn}` with `why_not_drawn` when an end is not a node of the view |

A step's edge, from its finding's own files: 05, a receiver holding the
sender's half (answered), its refusal reply (refused) or nothing held (absent),
with names mapped to node ids by `population/<population>/population.json`;
06, the exchange's payer to its payee by key id in `nodes/`, answered when the
finding's reference, finality and exchange checks held, else absent; 07, an
`outcome.json` (`artifact`, `refusal`, `recorded_absence`) or the reporter
backend's verification line. A rehearsal finding belongs to the slot its run
started in.
