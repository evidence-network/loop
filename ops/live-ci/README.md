# Daily live run from GitHub Actions (loop#368)

`.github/workflows/live-daily.yml` runs the daily live loop (loop#308, loop#309) without a person's machine: it starts
`scripts/live_daily.sh` on the lab payer host, waits, and harvests the findings into a data-only pull request with
`scripts/live_harvest.py`. The run depends on one operator's cloud project and lab host; that is the operator's choice,
not part of the loop.

## Trust model

- The workflow starts only from `schedule` and `workflow_dispatch` on `main`, in the `live-lab` environment (deployments
  from `main` only). A fork pull request cannot start it and gets no OIDC token. No self-hosted runner, no stored key.
- Cloud access is Workload Identity Federation: the provider accepts only this repository (by name and id), `main`, the
  `live-lab` environment and the `schedule`/`workflow_dispatch` events, and maps to one service account that may log in
  to the payer host without sudo.
- On the host, that login may run exactly three commands as the lab user (sudoers lists them with their argument):
  `/opt/loop-live-ci/entry.sh start`, `status`, `stage`. `entry.sh` is root-owned, installed by the operator and not
  fetched from this repository at run time. It reads no wallet, seed, key, macaroon, peer-key, join or invite file.
- A change merged to `main` runs as the lab user at the next start, as it did when a person started the run: review of
  `scripts/`, `lab/`, `steps/` and `.github/` is the control, and the repository ruleset should require it. On the host,
  `entry.sh` allows one start per UTC day (a marker the lab user could delete; it guards against accidents and loops in
  the workflow, not against code the lab user runs), and `start` refuses when `live_daily.sh`'s exchange call sites are
  no longer exactly one. The payment ceiling itself is node side, outside this repository.
- The pull request is opened with `GITHUB_TOKEN`, which starts no `pull_request` workflow. The job therefore dispatches
  `nodes`, `reports`, `personal-data`, `leak-lint`, `dco`, `stats` (the `site` check), `harvest-allowlist`,
  `ecosystem-gate` and `versions` on the `live-harvest/<name>` branch; their checks attach to the pull request's head commit. Each
  dispatched run judges the range a pull request from that branch would show (merge base with `main` to the branch head; `ecosystem-gate` compares with main's tip, as a pull request does).
  `--auto-merge` stays off: a human merges.

## Settings

Environment `live-lab` (create it first, with deployment branches limited to `main`; a job naming a missing environment
creates it unprotected) secrets: `LIVE_VM`, `LIVE_PROJECT`, `LIVE_ZONE`, `LIVE_LAB_USER`, `LIVE_WIF_PROVIDER`,
`LIVE_SERVICE_ACCOUNT`. Repository setting: Actions may create pull requests. The host needs `entry.sh` and
`/etc/loop-live-ci/conf` (`LIVE_HOME`, `LIVE_ENV`, `LAB_DIR`) installed by the operator.

## Failures

A run that cannot start, dies, ends without a pack (`live_daily.sh` exit 2 and 4 to 7), fails to stage or harvest, or
passes 3 hours (killed on the host) opens or comments on one `loop-live-failed` issue and fails the workflow; a cancelled
workflow does too. A skip (the lab is busy or being rebuilt, or today's start already taken) ends green with a notice. If
only the harvest failed, dispatch the workflow with `harvest_only` to harvest today's finished run. A day with no findings
on `main` is caught by `live-alerts.yml`'s `loop-live-stale` issue.
