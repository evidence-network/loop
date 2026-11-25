# Contributing

Changes come in by pull request. Each pull request runs these checks, and all of them must pass
before it merges:

- `nodes`: the commitment list is well formed (`python3 scripts/check_nodes.py nodes`).
- `reports`: the tests, and the reports are well formed
  (`python3 -m unittest discover -s tests`, `python3 scripts/check_reports.py reports/daily`).
- `personal-data`: no email address, phone number or public IP address in `nodes/` or
  `reports/` (`python3 scripts/check_personal_data.py`).
- `leak-lint`: no internal workflow text. Its term list is a repository secret, so a pull
  request from a fork sees only pass or fail; a maintainer can tell you what matched.
- `dco`: every commit is signed off.

A code owner (`.github/CODEOWNERS`) reviews each pull request.

## Check locally before you push

Before every push: `scripts/check.sh` (about 20 min on an Apple-silicon Mac). Before pushing a change to any path
in the `push: paths:` list of `.github/workflows/loop.yml` (the steps, `loop.sh`, `join.sh`, `population/`,
`versions.lock`, `lab/`, `scripts/*.py`, `status/`, `tests/` and `nodes/revocations/`), also
`scripts/check.sh --full` (about 80 min; needs go, cargo, openssl 3, bash 4+, GNU `timeout`, `flock`, Google
Chrome and network for the pinned tools). Pull requests run only the fast set.

The one exception: `scripts/check.sh` skips the `loop` job (every step's pinned tools, stage 1, the cross stage
and steps 10 and 11), which takes about 51 min in CI and about 79 min on a Mac. `--full` runs it; CI runs it
nightly and on each push to main that changes those paths, and a failure there opens a `ci-red` issue the same
day. The leak scans need the `LEAK_LINT_TERMS` secret and run only in CI.

## Sign your commits off (DCO)

Each commit carries a `Signed-off-by:` line with your name and the email address you commit
with:

```sh
git commit -s -m "nodes: add <node_id>"
```

By signing off you certify the [Developer Certificate of Origin](https://developercertificate.org/):
that you wrote the change, or otherwise have the right to submit it under this repository's
license (Apache-2.0, see `LICENSE`). Forgot? `git commit --amend -s`, or for several commits
`git rebase --signoff main`, then force-push your branch.

## No personal data

This repository is public and keeps its history. Give a GitHub handle as a node's contact;
never an email address, a phone number or a home address. Anything removed later stays in the
history.
