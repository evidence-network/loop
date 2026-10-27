# site/

The daily report page: one static HTML page built from `reports/daily/` and
nothing else, with an explainer for an operator new to the network.

```sh
python3 -B site/build.py reports/daily --out _site   # writes _site/index.html
```

- `reports_model.py` reads the reports. It is the only code that knows the
  report layout ([reports/README.md](../reports/README.md)), and it is strict:
  a report it cannot read fails the build, and nothing is written.
- `build.py` renders what `reports_model.py` returns, and copies the reports
  byte for byte to `_site/reports/daily/` so each link opens the raw file.
- `template.html` is the page around the reports, including the explainer.

The page has no scripts. Every count of nodes reads "N of M listed nodes";
other numbers are quoted record values or times. Nodes are listed by node id,
and the page says the order means nothing. There is no score, rank or total
per node. `tests/test_site.py` holds the page to all of this. The `site`
workflow builds the page on every pull request and uploads it as an artifact;
deploying it is a separate step.
