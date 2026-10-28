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
- `build.py` draws, in order:
  - records sealed per day on the listed nodes: one line, from each node's
    `records_at_checkpoint` minus its last earlier one (`stats_model.sealed_per_day`).
    A day with no day file, a missed run, a node whose count fell or no count to
    compare with is a gap, never zero or negative. Rehearsal nodes are not in
    the daily set and are never added to the line. Records with both halves have
    no source until M2: a legend entry, no line;
  - four tiles, each with what it is out of and its window: nodes that answered
    on the newest day, of nodes listed; exchanges with both halves (a
    placeholder until M2); the median time to the first sealed record over the
    last seven days of runs per population (step 01's findings, with the status
    page's rules); asks refused or absent, of asks, and days missed, of days;
  - "Why this matters" and "How it helps";
  - the loop's steps as a path, once for the newest rehearsal run and once for
    the newest live run, side by side and never added together. Each step's
    state, the held rows and the five numbers are `status/status_page.py`'s,
    called as functions; the status page itself is built into
    `_site/stats/status/`, and each step links its row there;
  - the five numbers, each out of the run's own length, or a placeholder with
    the milestone it waits for;
  - a timeline per node: per question per day, answered, refused, absent,
    missed, not asked or no day file, and the day the node left;
  - the explainer, and each day in detail with each settled answer's
    `answer_sha256`. The day files are copied to `_site/stats/data/daily/`
    and each day links its own.
- `template.html` is the page around them, including the explainer.

The page has no scripts and loads nothing from elsewhere. Every count reads
"N of M ..."; other numbers are quoted record values or times. Nodes are
listed by node id, and the page says the order means nothing. There is no
score, rank or total per node. The page's own words say "checked", "verified"
or "witnessed" only for a `PASS` step. Nothing links under `reports/`: the
reporter's raw files are kept elsewhere and are not part of this site.
`tests/test_site.py` holds the page to all of this, on the fixtures in
`tests/fixtures/stats/`.

The `stats` workflow builds the site on every pull request, push to main and
nightly (the `site` check), runs `.github/leak_lint.py` over the built site
(staged in a scratch git repository, fail-closed without the `LEAK_LINT_TERMS`
secret), and on main deploys it to GitHub Pages with `actions/deploy-pages`.
Pages must first be enabled by a repository admin (Source: GitHub Actions).
