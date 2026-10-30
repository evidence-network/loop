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
  `not_checked_is_regression` is true (Steven's open call; the default is on, and the base branch's
  setting decides, not the pull request's).
- Rehearsal stays the per-PR gate (`loop.sh --strict`); this gate is about live results.
