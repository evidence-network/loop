# capsule-viewer 0.14.0, History view, vendored

`history-view.iife.js` (global `HistoryView`) and `LICENSE` / `LICENSE-BSD-3-Clause` are copied unchanged from the
npm package `@action-state-group/capsule-viewer@0.14.0` (`dist/history-view.iife.js`, `dist/history-view.iife.js.sha256`
and the package's licences).

- sha256 of `history-view.iife.js`: `c7a9bce903e0b99dd5618c893a7d0cf3576ad8d3ff2dd72635209c9ce30346d7`
  (the package's own `.sha256` file, copied beside it)
- `history-view.css` is `HistoryView.historyViewStyles`, written out so the build, which is standard library only,
  can put it inline. `tests/test_watch.py` checks it against the script under node.

The watch build (`site/watch.py`) writes one page per node stream under `watch/history/`, each loading the script from
`../../vendor/capsule-viewer-0.14.0/` and drawing a `history-view/v0` input from the card's checkpoints and the
node's advertised policy (contract: capsule-viewer `docs/design/history-view.md`).

To upgrade: replace the files from the new tarball, regenerate the css, and update this README, the directory name,
`VIEWER` in `site/watch.py` and the pin in the test.
