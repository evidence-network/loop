# Daily live run from GitHub Actions (loop#368)

`.github/workflows/live-daily.yml` runs the daily live loop (loop#308, loop#309) without a person's machine: it starts
`scripts/live_daily.sh` on the lab payer host, waits, and harvests the findings into a data-only pull request with
`scripts/live_harvest.py`. The run depends on one operator's cloud project and lab host; that is the operator's choice,
not part of the loop.

## Trust model

- The workflow starts only from `schedule` and `workflow_dispatch` on `main`, in the `live-lab` environment (deployments
  from `main` only). A fork pull request cannot start it and gets no OIDC token. No self-hosted runner, no stored cloud key (the one stored private key is the harvest GitHub App's, below).
- Cloud access is Workload Identity Federation: the provider accepts only this repository (by name and id), `main`, the
  `live-lab` environment and the `schedule`/`workflow_dispatch` events, and maps to one service account that may log in
  to the payer host without sudo.
- On the host, that login may run exactly three commands as the lab user (sudoers lists them with their argument):
  `/usr/local/sbin/loop-live-ci-entry start`, `status`, `stage`. `entry.sh` is root-owned, installed by the operator and not
  fetched from this repository at run time. It reads no wallet, seed, key, macaroon, peer-key, join or invite file.
- A change merged to `main` runs as the lab user at the next start, as it did when a person started the run: review of
  `scripts/`, `lab/`, `steps/` and `.github/` is the control, and the repository ruleset should require it. On the host,
  `entry.sh` allows one start per UTC day (a marker the lab user could delete; it guards against accidents and loops in
  the workflow, not against code the lab user runs), and `start` refuses when `live_daily.sh`'s exchange call sites are
  no longer exactly one. The payment ceiling itself is node side, outside this repository.
- The pull request is opened with a GitHub App installation token (`actions/create-github-app-token`, pinned by commit,
  minted in the harvest step only, from the environment secrets `HARVEST_APP_CLIENT_ID` and `HARVEST_APP_PRIVATE_KEY`; there
  is no fallback to `GITHUB_TOKEN`: without the secrets the harvest fails visibly). A `GITHUB_TOKEN` pull request starts no
  `pull_request` or `pull_request_target` workflow, so the ruleset's required checks (`leak-lint` first) never ran on it
  (loop#395); a pull request opened by an App does. The App holds `contents: write` and `pull requests: write` (plus the
  implicit read-only `metadata`), installed on this repository only, and the token is scoped to it. Three cases if the workflow
  were compromised (anyone who can merge to `main` can change it, so the reviewed-merge rule for `.github/` matters):
  (1) the token alone leaks: it expires in an hour and can push new branches, open and edit pull requests, and create
  releases here; it cannot push `main` directly, dispatch workflows, or change secrets, settings or rulesets, but it can merge its own
  pull request once the six required checks pass (the ruleset needs 0 approvals; `GITHUB_TOKEN` could do the same
  before), so a change to `ops/live-ci/` or `.github/` can reach `main` and run with the key and cloud login at the next
  start; it can also push a branch whose same-repository `pull_request` runs read the repository secrets those workflows use
  (`LEAK_LINT_TERMS`; this was true of `GITHUB_TOKEN` too). What enforces "a human merges" is an owner setting: the main
  ruleset requiring at least one approving review (an App cannot approve its own pull request) and the repository setting
  "Allow GitHub Actions to create and approve pull requests" off, which the App makes unnecessary; (2) the private key leaks: the holder can mint such tokens until the key is revoked in the
  App's settings (revoke and replace it at once); (3) the runner itself is taken over: it has both, plus the `live-lab`
  secrets and the cloud login of the CI account. A pull request opened by the App starts this repository's `pull_request`
  and `pull_request_target` workflows, by design. `GITHUB_TOKEN` shrinks to `contents: read`, `issues: write` (the failure issue),
  `actions: write` (dispatching the checks) and the OIDC token. A fork or non-main run cannot mint it: the secrets live in the
  `live-lab` environment, which accepts deployments from `main` only, and the workflow runs on `schedule` and
  `workflow_dispatch` from `main` only. `harvest-app-check.yml` (manual, main only) proves the token without a live run; it cannot prove the App's own scope, so check once in the
  App's settings that it is installed on this repository only ("Only select repositories") and that contents and pull
  requests are its only write permissions (a leaked key mints for every repository and permission the App has). The job still
  dispatches `nodes`, `reports`, `personal-data`, `leak-lint`, `dco`, `stats`, `harvest-allowlist`, `ecosystem-gate` and
  `versions` on the `live-harvest/<name>` branch with its own token, until the App's pull request runs are shown to start
  them alone. `--auto-merge` stays off: a human merges.

## Settings

Environment `live-lab` (create it first, with deployment branches limited to `main`; a job naming a missing environment
creates it unprotected) secrets: `LIVE_VM`, `LIVE_PROJECT`, `LIVE_ZONE`, `LIVE_LAB_USER`, `LIVE_WIF_PROVIDER`,
`LIVE_SERVICE_ACCOUNT`, `HARVEST_APP_CLIENT_ID`, `HARVEST_APP_PRIVATE_KEY`. The host needs `entry.sh` and
`/etc/loop-live-ci/conf` (`LIVE_HOME`, `LIVE_ENV`, `LAB_DIR`, `LIVE_CI_USER`). Access is through IAP only (port 22 open to
Google's IAP range, never to the runners' addresses); staged raw files are readable by the lab user and the CI login only installed by the operator.

## Failures

A run that cannot start, dies, ends without a pack (`live_daily.sh` exit 2 and 4 to 7), fails to stage or harvest, or
passes 3 hours (killed on the host) opens or comments on one `loop-live-failed` issue and fails the workflow; a cancelled
workflow does too. A skip (the lab is busy or being rebuilt, or today's start already taken) ends green with a notice. If
only the harvest failed, dispatch the workflow with `harvest_only`: it harvests today's finished run, or, when the
findings branch is already pushed, dispatches the checks again. A failure between publishing the `live-<name>` release and
pushing the branch leaves a release that `live_harvest.py` refuses to overwrite; remove it by hand first. A day with no findings
on `main` is caught by `live-alerts.yml`'s `loop-live-stale` issue.
