# baseline/

`live.json` is the ecosystem gate's baseline (loop#149): for each ecosystem, the last accepted
live result of each step, the `findings/<date>-live` directory it came from, and the pins
(`versions.lock` entries) it ran under.

- `scripts/ecosystem_gate.py update --ecosystem mesh --findings findings/<date>-live` is the only
  way to change it. It refuses malformed findings, rehearsal findings and a regression.
- `verify` (CI, every PR and push) requires every pin the baseline records to be in `versions.lock`
  at the same ref; a pin added since is not a bump. So a pull request that changes or removes a pin
  must carry a baseline taken from a new live run under the new pins, and no step that was PASS in
  the base branch's baseline may be FAIL or missing in it. PASS -> NOT_CHECKED is a regression while
  `not_checked_is_regression` is true (Steven ruled it counts as going red, buzz://message?channel=054513b2-0a66-4c1e-885e-2af75c56233c&id=d07765e74e23b2b946a35d96316ce12951118fa1825d1d111d824d737dee0da2;
  the default is on, and the base branch's setting decides, not the pull request's).
- Rehearsal stays the per-PR gate (`loop.sh --strict`); this gate is about live results.

## Live harvest (loop#309)

`scripts/live_harvest.py` turns one `live_daily.sh` run into a pull request from `live-harvest/<date>-live-<N>`:
`findings/<name>/` holds each step's `finding.json`, `run.json`, `index.md` and `proof-archive.json`; the proof bytes
are the release asset `live-<name>` / `<name>.tgz`, pinned by the sha256 in `proof-archive.json`. `steps/finding.py
check` accepts a missing proof file there (a present one must still match its digest). To check every byte, download the
asset, compare its sha256 to `proof-archive.json`, and unpack it from the repository root (`tar xzf <name>.tgz`) over
`findings/<name>/`, then run `python3 steps/finding.py check findings/<name>`. The `harvest-allowlist` job keeps the pull
request data only (added `findings/<date>-live-<N>/` files and `baseline/live.json`). A human merges it; `--auto-merge`
stays off until Steven approves it, and then `harvest-allowlist` goes into the required checks.
