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
