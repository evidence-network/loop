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
python3 -B site/watch.py --daily stats/data/daily --rehearsal stats/data/rehearsal \
    --ondemand stats/data/ondemand --findings findings --nodes nodes --population population \
    --commitments lab/runs --bindings bindings --lock versions.lock --out _site
# writes _site/watch/index.html, data.json, watch.js, watch-layout.js and vendor/d3-7.9.0/
```

- The graph sits on a dark, full-width canvas (dark in both colour schemes,
  with its own `--cv-*` tokens); the text is below it. Each node listed in
  the day's file is a glowing dot in its day state. The reporter's two asks to
  it are two rings, inner `checkpoints` and outer `history_card/1`:
  **answered** (solid), **refused** (a signed `refusal`: dashed) or **absent**
  (an `absence`: dotted). A node has a bright edge on its first day in a day
  file; a node past its `left_on` is faded and dashed.
- Every pair of shown nodes is joined by a dotted line. A pair is solid only
  where a finding of step 05 (deal), 06 (settle) or 07 (ask) for the shown
  day, passing `steps/finding.py`'s check, holds an exchange between the two
  that brought something back (a reply or a signed refusal); the line is
  labelled with the steps. Otherwise that step says "not measured yet". The
  reporter is not drawn: the card names the node it runs on. On the live
  view a pair's exchanges are drawn only when both nodes' `nodes/` entries opt
  in (`disclosure.pair_edges: "public"`): an edge says who dealt with whom. A
  pair with an end that does not opt in stays dotted, and the charts still
  count its exchanges (evidence-network/loop#77). The rehearsal is the loop's
  own TEST population and is not gated.
- A slider steps through the days (or the rehearsal's 10-minute slots). *Play*
  runs through them; *Replay* settles the day's asks one at a time in the
  order of their attempts, a ring spreading from the node, then sends a pulse
  along each solid pair. Under `prefers-reduced-motion` nothing travels or
  spreads: the state just changes. Live and the rehearsal are a toggle, never
  one graph.
- Zoom, pan and rotate (d3-zoom): wheel or pinch zooms, drag pans, Shift and
  drag rotates the layout about its middle. With the graph focused, `+` and
  `-` zoom, the arrow keys pan, `[` and `]` rotate, `0` resets; buttons do the
  same. The layout (d3-force, `watch-layout.js`) starts each node at a seed
  from its node id, so it is the same on every load. Days are laid out in
  order; when a node joins, the nodes already placed are pinned while it
  settles, so they never move, and it eases in.
- Tab to a node or hover it for its card, in its own slot beside the graph
  (never over it): the operator handle, `host_build` and `plugin_version`
  from `nodes/<node_id>.json`, its history cards, the day files it is in, its
  state and asks, `records_at_checkpoint`, and the node the reporter runs on.
  No amount or band.
- A node's badge is the number of history cards stapled to its commitment;
  its card lists each one's stream (log id) and the witnesses whose receipts
  the card carries, or "unwitnessed". The commitment is read where it is
  committed: `nodes/commitments/<node_id>.json` when there is one (the carrier
  step 03 reads), else the join's `lab/runs/<date>/join/<name>/commitment.json`
  whose `key_id` is the node's. `evidence-commitment/1`'s `history_cards` list
  counts its entries; `/0`'s single `history_card` counts 1, its stream the
  card's own `evidence_stream`. A card's witnesses are the `witness` of each
  receipt on a checkpoint of the decoded card's `history`.
- Each community a node's cards cover is a ring round it: `mesh-llm`, or
  `personal-agents` (a `/1` card's `community`; `/0` reads as `mesh-llm`).
  A card with no checkpoint in its history draws its ring thin, with "no
  history since" the day its stream started (the card's `stream_started`,
  else the day it was issued): a new stream and a cleared one started again
  look the same, with no warning and no score.
- Bound groups: `bindings/<controller_key_id>.json`, a `binding-verdict/0`
  (`controller_key_id`, `subjects`, `status`, `verifier`, `checked_at`,
  `cites`). Its subjects are drawn as one dot labelled "bound", a ring per
  community, only when `status` is `verified` and `verifier` (`<name>@<pin>`)
  is a line of the lock the build reads (`--lock`). Revoked, not checked, or
  verified by a tool the lock does not pin: each subject is its own dot, and
  its card says "binding not verified" and cites the verdict's issue. With no
  `--bindings` directory there are no groups; main has none today.
- Under the graph, one small chart per count, x = day: nodes listed, records
  sealed (at each node's checkpoint, all nodes), asks answered, refused and
  absent, deals (05) and settlements (06), each with a table of the same
  numbers. A count with no finding reads "not measured yet", never 0.
- On-demand runs (`stats/data/ondemand/<YYYY-MM-DD>T<HHMMSS>Z.json`, the
  scrubbed format with `date` the run's name) are, in the live view, points
  of their own between the days, labelled with their time (`on-demand
  14:05:09Z`), with their own table. They are never a day's row and never in a
  day's count. With no `--ondemand` directory there are none.
- `watch.js` and `watch-layout.js` are handwritten. d3 7.9.0 is vendored in
  `site/vendor/d3-7.9.0/` with its ISC licence; nothing is loaded from
  elsewhere. The page carries `data.json` inline, so a `file://` load draws
  it; served, it reads its own `data.json` (a relative URL, the only one it
  reads) every 60 seconds and redraws in place, keeping the view and the day.
  A failed read keeps what is drawn and says when it last updated. With
  scripts off, the tables under the graph hold the same counts. The `/stats/`
  page is unchanged and has no scripts. `tests/test_watch.py` holds the page
  to all of this, with fixtures in `tests/fixtures/watch/` (`make_fixtures.py`
  there writes them).

### data.json

Sorted keys; the same JSON is in the page. Only fields of the scrubbed sets,
`nodes/`, the commitments and findings: no answer bytes or hashes, no card
bytes, no amount, no band.

| key | value |
|---|---|
| `format` | `watch/2` |
| `edge_kinds` | `deal`, `settle`, `ask07`, each with what its edge is |
| `outcomes` | `answered`, `refused`, `absent` |
| `size` | the graph's width and height, in SVG units |
| `nodes` | node id → `{operator, host_build, plugin_version, listed: true}` from `nodes/`, or `{listed: false}` for a node no longer there; with `seed` (its first place, from its node id), `commitment` (the committed file read, or `null`), `history_cards` (each `{stream, derivation, community, witnesses, history, since}`) and `binding` (`null`, `{status: verified, group}`, or `{status, says: "binding not verified", why, cites}`) |
| `groups` | each bound group: `id` (`bound:` and a hash of the controller key id: the key itself, an operator's, is never published), `subjects` (node ids), `verifier`, `checked_at` |
| `views` | `live` (the daily set and its on-demand runs) and `rehearsal` (the reporter rehearsal, a TEST), each `{label, unit, test, days, metrics}` |

Each of a view's `days`, from the first to the last, with a gap day too, and
each on-demand run in time order among them:

| key | value |
|---|---|
| `date` | the day, the rehearsal slot `YYYY-MM-DDTHHMMZ`, or the on-demand run `YYYY-MM-DDTHHMMSSZ` |
| `set` | `daily`, `rehearsal` or `ondemand` |
| `label` | the date, or `on-demand HH:MM:SSZ` |
| `run` | `settled`, `missed`, or `no_file` |
| `file` | the file under `stats/data/`, or `null` |
| `listed` | the file's `listed`, or `null` |
| `reporter_key_id` | the file's, when settled |
| `reporter_runs_on` | the node whose `nodes/` entry holds the reporter's key, or `null` |
| `nodes` | each node in the file: `node_id`, `state` (both questions read together, as the stats page reads them), `questions` (`checkpoints`, `history_card/1` → its state), `asked` (each ask: `question`, `state`, `outcome`, `at` (the settling attempt), `attempts`), `asks` (`answered`, `refused`, `absent`, of 2), `records_at_checkpoint`, `days_listed` (the day files it is in, to this day), `first_day`, `node_line`, `left_on`, `left`; then each node that left and is not in the file: `node_id`, `left_on`, `left: true`, `days_listed` |
| `edges` | the step edges: `kind`, `from`, `to`, `outcome` |
| `pairs` | every pair of shown nodes, `a` < `b`: `line` (`solid` or `dotted`) and `kinds`, the steps whose edges between the two brought something back |
| `measured` | per kind: `ask` → `{measured, source}` or why not; `deal`, `settle`, `ask07` → `{measured: false, step, says: "not measured yet: ..."}`, or `{measured: true, step, finding, result, drawn, answered, not_drawn}` with `why_not_drawn` when an end is not a node of the view, and `withheld`, `withheld_answered` and `why_withheld` when an end does not opt in to public pair edges (live only) |
| `drawn` | what the graph draws: each `{id, members, bound, left, rings}`, a node (`id` its node id) or a bound group's shown subjects (`id` the group's); `rings` one per community, `{community, history, streams, since}` |
| `links` | the lines between what is drawn: `pairs` with each end mapped to its `drawn` id, a group's inner pairs left out, `solid` if any of its pairs is |

A view's `metrics`: one `{key, label, points}` per chart (`nodes`, `records`,
`answered`, `refused`, `absent`, `deals`, `settlements`), each point
`{date, set, label, value}`, with `says` when `value` is `null` and
`not_drawn` for a step's count. `deals` and `settlements` count withheld edges too. They have no on-demand
points: those steps run in the daily loop only.

A step's edge, from its finding's own files: 05, a receiver holding the
sender's half (answered), its refusal reply (refused) or nothing held (absent),
with names mapped to node ids by `population/<population>/population.json`;
06, the exchange's payer to its payee by key id in `nodes/`, answered when the
finding's reference, finality and exchange checks held, else absent; 07, an
`outcome.json` (`artifact`, `refusal`, `recorded_absence`), or the reporter
backend's verification line, from the node the reporter runs on. A rehearsal
finding belongs to the slot its run started in.

## The growth report

`growth.py` builds `_site/growth/` (Pages URL `/loop/growth/`): a report of its
own, separate from the stats page, the status page and the no-regression gate
(loop#151, ruling ops dd525c02). It reads `nodes/` and `nodes/commitments/` from
git history and nothing under `stats/` or `findings/`.

```sh
python3 -B site/growth.py --out _site   # writes _site/growth/index.html and data.json
```

Each Monday-to-Sunday week (UTC, the last one cut at the ref's commit time) is
judged against the tree as committed at the end of that week along the ref's
first-parent history, not today's files. `scripts/check_commitment.py` checks
every listed node's commitment there offline: its signature, its policy digest
(the policy file read from that tree; one that cannot be read means the commitment
does not verify) and each stapled card's bytes, digest and subject. It does not
verify a card's checkpoint signatures or witness receipts (AAC#430), so the page
says a checkpoint "carries a witness receipt", never "witnessed". A week shows the
nodes with a verifying card, of those listed, and the checkpoints cut and records
sealed, as network totals and split by community, with the checkpoints carrying a
receipt of those cut. A checkpoint counts in the week it was cut, from the first
end-of-week tree in which a verifying card carries it, so one committed late is
counted in its own week; each volume cell names the nodes (of those listed) or
cards that contributed it. A failing or missing commitment stays in the denominator
and the node table says why. **Records sealed** is the log entries covered by a
signed checkpoint, padding and admission decisions included: evidence activity,
never deals. A card's first checkpoint is "stated, not proven" unless it carries a
receipt. There is no per-node volume (Sherlock2, Buzz 12950c3a; Steven's
acceptance, ops 92af8529); an operator-chosen column can be added later.
A staple costs one key, so the staple-derived rows are inflatable (loop#170): the
page says so in a caveat line ("a staple costs one key; these rows measure evidence
activity, not nodes or deals"), shows the distinct verified controllers beside the
nodes with a verifying card (a group counts only for a `bindings/` verdict that
`history_cards.bound_groups` accepts; none today, and then the page says so), and
keeps the existence-proof rows (decisions limited or refused on evidence; exchanges
with outside nodes, settled, disputed) in a lab-only section that carries no value
and links the status page. Wording rule: records sealed, never deals.
Needs full git history (`fetch-depth: 0`). `tests/test_growth.py` holds it.
