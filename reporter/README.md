# reporter/

The daily reporter: a node on the lab mesh with its own key, which once a day
asks every node in `nodes/` the two stranger-safe questions, `checkpoints` and
`history_card/1`, and commits what came back under `reports/daily/<date>/` in
**evidence-network/loop-data**, a private repository: the raw store. It asks
nothing else. See [reports/README.md](../reports/README.md) for what a day's
report holds.

The raw reports hold the bytes each node answered, so they are not published
here. This repository gets only the scrubbed set, `stats/data/daily/<date>.json`
(per node per day: the outcome, its attempts, the log size at the checkpoint and
the SHA-256 of the answer, never the answer), which a GitHub Action in loop-data
writes nightly with `scripts/scrub_reports.py`.

- `reporter.json`: the reporter's mesh node id and its capsules plugin key
  (Ed25519 public key, 64 hex), and `retry_after_s`: when a run asks a node
  that gave no answer again, in seconds after the run starts (now 3600, 10800,
  21600: three more attempts over six hours; empty or absent, one attempt). **Placeholder until the reporter node exists:**
  all zeros, and `daily_report.py run` refuses to run with it. Fill it on the
  reporter node with `scripts/reporter_key.py --write reporter/reporter.json`
  and open a pull request.
- `systemd/`: the timer that runs `scripts/run_daily.sh` once a day on the
  reporter node, and one that runs `scripts/ask_again.sh` after it. Not
  installed anywhere yet.
- `workflows/ask-again.yml`: a manual GitHub dispatch for asking again. Inert
  here; see [Asking again](#asking-again).

## The key

The reporter's key is its capsules plugin's key. The plugin makes it on first
start and keeps the private half in `<data dir>/keys/` (default
`~/.local/share/capsules`) on the reporter node. It never leaves that node and
is never in this repository; `reporter_key.py` reads only the public files
(`self-peer-id`, `keys/node-key.pub.pem`). The reporter's host announces the
key on the mesh, bound to its node id by its node key, and the plugin signs
every request with it, so each node can see who asked.

The reporter's key custody rule: the private key stays in the plugin's data
dir, readable by the reporter's user only, and is never backed up. `daily_report.py run --data-dir` refuses a
`keys/node-key.pem` that another user owns or could read (the plugin writes it
`0600`). If the key is lost, make a new one and open a `reporter.json` pull
request: a new key is a new `key_id`. Every run also checks that the plugin
behind `--console` signs with the `reporter.json` key (its `ledger` route's
`node_pub_key_pem`) and refuses otherwise. Reports from before then stay signed under
the old one; `RUN.json` of each day names the key that ran it.

## Node keys come from the mesh, never a map

The reporter judges each answer under the key the node's host announces on the
mesh (mesh-llm plugin key announcement, `plugin_keys.v1`). The reporter node
sets **no `CAPSULES_PEER_KEYS`**; `run_daily.sh` refuses to run if it is set.
The `key_id` in a node's `nodes/` entry is only compared: each verification line
says `listed_key=match` or `listed_key=differs`.

## The live day-one run

Needs, on the reporter node (not on a laptop or a CI runner):

1. The lab host build and the capsules plugin at the pins in
   [`versions.lock`](../versions.lock) (the host build must announce plugin
   keys; release mesh-llm 0.78.1 does not), installed as in `JOIN-LAB.md`.
2. The node joined to the lab mesh as a full node, not `--client` (a client
   node gets a new id at every start, and keys are bound to node ids), and
   connected to the nodes it asks: a host learns announced keys from its peers.
3. Its key announced: `curl -s http://127.0.0.1:3131/api/plugin-keys` lists it.
4. `reporter.json` filled (`scripts/reporter_key.py --write reporter/reporter.json`).
5. At least two lab nodes in `nodes/` that have each cut a checkpoint. A node
   cuts its first checkpoint 300 s after activity; before that it answers a
   signed `coverage_unsatisfiable`, recorded as `no_checkpoint_yet`.

Then, from a clone of this repository on the reporter node:

```sh
python3 scripts/daily_report.py run --data-dir ~/.local/share/capsules
python3 scripts/check_reports.py reports/daily
python3 scripts/daily_report.py prove --date "$(date -u +%F)"   # exit 0: the day-one proof holds
```

`prove` passes when at least two nodes answered `checkpoints` verified with at
least one checkpoint, and `history_card/1` verified. It prints; it writes
nothing to the repository. Pass `--out <loop-data checkout>/reports/daily` to
both commands to write into the raw store, then commit `reports/daily/<date>/`
there and push it to loop-data's `main` as `run_daily.sh` does (below).

## Running it daily

On the reporter node, one always-on lab node, with two checkouts:

- `~/loop`, a clone of this repository on `main`. The reporter only reads it
  (its scripts, `nodes/`, `reporter.json`): each run fast-forwards it to
  `origin/main` and never commits to it or pushes from it, so clone it with no
  write credential.
- `~/loop-data`, a clone of evidence-network/loop-data on `main` that pushes
  with the reporter's deploy key, the only key with write access there.
  `LOOP_DATA_DIR` names it (required, no default; the systemd units set it).

`run_daily.sh` writes the day into `$LOOP_DATA_DIR/reports/daily`, stages only
`reports/daily` there and pushes it straight to loop-data's `main` (`git push
origin HEAD:main`). It runs only with both checkouts on `main`, pulls
loop-data with `--rebase` first, refuses to push when any outgoing commit
touches anything outside `reports/daily/` (a file added then deleted
included), and holds a day that fails the personal-data check on the node,
outside both checkouts, committing nothing.

```sh
git clone https://github.com/evidence-network/loop ~/loop
git clone git@github.com:evidence-network/loop-data ~/loop-data   # with the deploy key
cp reporter/systemd/loop-reporter.* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now loop-reporter.timer
loginctl enable-linger "$USER"
```

The timer runs at 00:30 UTC (and at the next boot if the node was down then).
A run asks nodes that gave no answer again for up to six hours, then commits.
A day it did not run gets a `MISSED` mark from the next run, and a run that cannot
reach its own node commits `MISSED` for that day. The `reports` workflow's
09:00 UTC watchdog reads this repository's `reports/daily`, which no longer
receives reports: it has to move to loop-data, or read the scrubbed set (see
the pull request that moved the reports).

## Asking again

A node that was offline for the whole run can be asked again once the day's
run has settled (before then, `ask` refuses: the run is still asking):

```sh
scripts/ask_again.sh <node_id>     # one node
scripts/ask_again.sh               # every node with a question unanswered today
```

It asks only the questions the node has not answered today (a signed refusal
is an answer), adds one attempt directory per node, and commits to loop-data
under the same guards as `run_daily.sh` (`LOOP_DATA_DIR` too). Nothing already
written changes. Two hooks call it; neither is enabled:

- `systemd/loop-reporter-ask.timer` (12:00, 18:00 and 23:00 UTC), installed like
  the daily timer;
- `workflows/ask-again.yml`, a `workflow_dispatch` that needs a self-hosted
  runner on the reporter node labelled `loop-reporter`. It lives outside
  `.github/workflows` until that runner exists.
