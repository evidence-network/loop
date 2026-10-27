# site/

The daily report page: one static HTML page built from `reports/daily/` and
nothing else, with an explainer for an operator new to the network.

```sh
python3 -B site/build.py reports/daily --out _site   # writes _site/index.html
```

- `reports_model.py` reads the reports through `scripts/report_layout.py`,
  the one module that knows the layout ([reports/README.md](../reports/README.md)),
  after `scripts/check_reports.py` passes over the whole tree. It is strict: a
  report it cannot read fails the build, and nothing is written. Per question
  the page shows the day so far from the attempts: every attempt, and the
  answer of the first that got one, an ask-again after the run included.
  It also reads the leave marks in `nodes/left/` for "left on <date>".
- `build.py` renders what `reports_model.py` returns, and copies the reports
  byte for byte to `_site/reports/daily/` (and the leave marks to
  `_site/nodes/left/`) so each link opens the raw file.
- `template.html` is the page around the reports, including the explainer.

The page has no scripts. Every count of nodes reads "N of M listed nodes";
other numbers are quoted record values or times. Nodes are listed by node id,
and the page says the order means nothing. There is no score, rank or total
per node. `tests/test_site.py` holds the page to all of this. The `site`
workflow builds the page on every pull request, runs `.github/leak_lint.py`
over the built page (staged in a scratch git repository, fail-closed without
the `LEAK_LINT_TERMS` secret), and uploads it as an artifact; deploying it is a
separate step.
