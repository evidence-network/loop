# reporter/

The daily reporter: a node on the lab mesh with its own key, which once a day
asks every node in `nodes/` the two stranger-safe questions, `checkpoints` and
`history_card/1`, and commits what came back under `reports/daily/<date>/`.
It asks nothing else. See [reports/README.md](../reports/README.md) for what a
day's report holds.

- `reporter.json`: the reporter's mesh node id and its capsules plugin key
  (Ed25519 public key, 64 hex). **Placeholder until the reporter node exists:**
  all zeros, and `daily_report.py run` refuses to run with it. Fill it on the
  reporter node with `scripts/reporter_key.py --write reporter/reporter.json`
  and open a pull request.
- `systemd/`: the timer that runs `scripts/run_daily.sh` once a day on the
  reporter node. Not installed anywhere yet.

## The key

The reporter's key is its capsules plugin's key. The plugin makes it on first
start and keeps the private half in `<data dir>/keys/` (default
`~/.local/share/capsules`) on the reporter node. It never leaves that node and
is never in this repository; `reporter_key.py` reads only the public files
(`self-peer-id`, `keys/node-key.pub.pem`). The reporter's host announces the
key on the mesh, bound to its node id by its node key, and the plugin signs
every request with it, so each node can see who asked.

A new key (a lost data dir, a rotation) is a new `key_id`: update
`reporter.json` by pull request. Reports from before then stay signed under
the old one; `RUN.json` of each day names the key that ran it.

## Node keys come from the mesh, never a map

The reporter judges each answer under the key the node's host announces on the
mesh (mesh-llm plugin key announcement, `plugin_keys.v1`). The reporter node
sets **no `CAPSULES_PEER_KEYS`**; `run_daily.sh` refuses to run if it is set.
The `key_id` in a node's `nodes/` entry is only compared: each verification line
says `listed_key=match` or `listed_key=differs`.

## The live day-one run

Needs, on the reporter node (not on a laptop or a CI runner):

1. The lab host build, **mesh-llm main b5ed6bd8 + PR #2620** (it announces
   plugin keys; release 0.78.1 does not), and **capsules 0.1.5**, installed as
   in `JOIN-LAB.md`.
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
nothing to the repository. Commit `reports/daily/<date>/` by pull request.

## Running it daily

On the reporter node, from a clone at `~/loop` that can push to this
repository:

```sh
cp reporter/systemd/loop-reporter.* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now loop-reporter.timer
loginctl enable-linger "$USER"
```

The timer runs at 06:00 UTC (and at the next boot if the node was down then).
A day it did not run gets a `MISSED` mark from the next run, a run that cannot
reach its own node commits `MISSED` for that day, and the `reports` workflow's
09:00 UTC watchdog fails when today has neither a report nor a mark.
