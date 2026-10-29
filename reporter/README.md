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
  21600: three more attempts over six hours; empty or absent, one attempt). **It holds the lab's reporter node (lab-3).** A reporter
  node set up anew starts from all zeros, which `daily_report.py run` refuses
  to run with; fill it on that node with
  `scripts/reporter_key.py --write reporter/reporter.json` and open a pull
  request.
- `systemd/`: the timer that runs `scripts/run_daily.sh` once a day on the
  reporter node, and one that runs `scripts/ask_again.sh` after it. Install
  them as [Going live](#going-live) step 6 says (the lab's reporter node runs
  both). Beside them, `loop-reporter-rehearsal.*`, the
  10-minute rehearsal (see [The rehearsal](#the-rehearsal)), and
  `reporter-env.conf`, a drop-in that gives the daily units the same optional
  settings file (see [The reporter's own node](#the-reporters-own-node)).
- `reporter.env.example`: that settings file, `~/.config/loop-reporter/env`.
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

The run commits in `~/loop-data` with `git commit -s`, so that checkout needs a
git identity of its own: the reporter's, never a person's. Without one, the run
refuses before asking anyone (`no git identity to commit with`), and the next
run marks that day or slot MISSED.

```sh
git clone https://github.com/evidence-network/loop ~/loop
git clone git@github.com:evidence-network/loop-data ~/loop-data   # with the deploy key
git -C ~/loop-data config user.name "loop reporter (<the reporter node's label>)"
git -C ~/loop-data config user.email "reporter-<the reporter node's label>@loop.invalid"
cp reporter/systemd/loop-reporter.* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now loop-reporter.timer
loginctl enable-linger "$USER"
```

The timer runs at 00:30 UTC (and at the next boot if the node was down then).
A run asks nodes that gave no answer again for up to six hours, then commits.
A day it did not run gets a `MISSED` mark from the next run, and a run that cannot
reach its own node commits `MISSED` for that day. The watchdog lives in
loop-data: its `reports` workflow runs this repository's
`scripts/check_reports.py --through <today> --reporter reporter/reporter.json`
over loop-data's `reports/daily` at 09:00 UTC and on every push there, so a day
with neither a report nor a `MISSED` mark turns it red. Here, the `reports`
workflow only tests the reporter code on pull requests.

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

## The reporter's own node

A node can't be asked through its own console. So the reporter asks its own
node (the `node_id` in `reporter.json`, when it is listed in `nodes/`) through a
second console, another lab node's, as step 12 does. Its row is then an answer,
verified like any other, not an absence. Without a second console it is left
out, as before. This holds for the daily run, asking again and the rehearsal.

- `LOOP_SECOND_CONSOLE` (or `--second-console`): that other node's console.
  It must not be the reporter's own; the run refuses it.
- `LOOP_SELF_NODE` (or `--self-node`): the reporter node's label in
  `population/live/population.json`. Optional. When set, it must be the node
  `reporter.json` names, or the run refuses: a stale label never sends some
  other node's questions through the second console.

`RUN.json` lists the node under `asked_through_second_console`, and the
`README.md` of the day or slot says so on its line. Both are set in one file on
the reporter node, which the rehearsal unit reads and the daily units read
through a drop-in, so no unit changes:

```sh
mkdir -p ~/.config/loop-reporter
cp ~/loop/reporter/reporter.env.example ~/.config/loop-reporter/env   # fill in both values
for unit in loop-reporter loop-reporter-ask; do
  mkdir -p ~/.config/systemd/user/$unit.service.d
  cp ~/loop/reporter/systemd/reporter-env.conf ~/.config/systemd/user/$unit.service.d/env.conf
done
systemctl --user daemon-reload
```

## The rehearsal

A test of the whole path before the daily run starts: the daily run, every 10
minutes, into loop-data's `reports/rehearsal/`, apart from `reports/daily/`.
Then loop-data's `publish-scrubbed`, then this repository's
`stats/data/rehearsal/`, then the stats site, which shows it on its own page
under a **TEST** label. Then it is wiped, and the daily run starts clean.

- `run_daily.sh --set rehearsal` (the unit sets `LOOP_REPORT_SET=rehearsal`)
  writes one report per 10-minute UTC slot,
  `reports/rehearsal/<YYYY-MM-DD>T<HHMM>Z/`, in the daily layout. It stages and
  pushes only `reports/rehearsal/`, under the same guards. Its retries are
  bounded to the slot (at +120 s and +240 s, no request longer than 20 s,
  nothing started after 7 minutes), whatever `retry_after_s` says.
- A slot with no run gets a `MISSED` mark from the next run, as a day does.
  The site shows it, and a slot with no file at all, as MISSED.
- loop-data checks each push to `reports/rehearsal/` as it checks the daily
  reports, slot by slot, in its own workflow (`reports-rehearsal`; a rehearsal
  push never runs the daily watchdog), and publishes it to
  `stats/data/rehearsal/` here.
- Each publish is a push to this repository's `main`, so the `stats` workflow
  builds and deploys Pages about once every 10 minutes while the rehearsal
  runs. That is accepted for the rehearsal's length; it stops with the timer.
- Run the rehearsal timer or the daily one, never both.

Install, on the reporter node, after the daily setup above (both checkouts, linger):

```sh
git -C ~/loop pull --ff-only
cp ~/loop/reporter/systemd/loop-reporter-rehearsal.* ~/.config/systemd/user/
systemctl --user daemon-reload
```

Start it (the daily timer stays off):

```sh
systemctl --user disable --now loop-reporter.timer
systemctl --user enable --now loop-reporter-rehearsal.timer
systemctl --user list-timers 'loop-reporter*'
journalctl --user -u loop-reporter-rehearsal.service -f
```

A planted stop skips one whole slot. Starting the timer fires a run at once,
and that run reports the slot it starts in. So restarting inside the slot you
meant to skip reports that slot as clean, and nothing is MISSED. To skip slot
`S`: stop the timer before `S` begins, and start it again only once the next
slot, `S+10min`, has begun. The run that start fires reports `S+10min` and
commits `MISSED` for `S`, and the TEST page shows it:

```sh
systemctl --user stop loop-reporter-rehearsal.timer
# wait until the slot after the one to skip has begun (past two :x0 UTC boundaries), then:
systemctl --user start loop-reporter-rehearsal.timer
```

Stop it (a run under way finishes first; wait for `inactive`):

```sh
systemctl --user disable --now loop-reporter-rehearsal.timer
systemctl --user is-active loop-reporter-rehearsal.service
```

Wipe it, once it is stopped. Each repository has `scripts/wipe_rehearsal.sh`,
which removes its rehearsal path in one commit and refuses if that commit
would touch anything else, `reports/daily/` and `stats/data/daily/` above all.
loop-data first: once its wipe is on `main`, `publish-scrubbed` publishes the
empty rehearsal set, which removes `stats/data/rehearsal/` here and the TEST
page with it. This repository's script is the fallback when that did not
happen.

```sh
# loop-data, from a checkout that can open a pull request:
git fetch origin && git switch -c wipe-rehearsal origin/main
scripts/wipe_rehearsal.sh
git push origin wipe-rehearsal          # open the pull request and merge it
# loop, only if stats/data/rehearsal/ is still on main after publish-scrubbed ran:
git fetch origin && git switch -c wipe-rehearsal origin/main
scripts/wipe_rehearsal.sh
git push origin wipe-rehearsal          # open the pull request and merge it
# the reporter node: drop what a stopped run may have left
git -C ~/loop-data pull --rebase --quiet origin main && rm -rf ~/loop-data/reports/rehearsal
```

Switch to the daily run:

```sh
systemctl --user enable --now loop-reporter.timer
```

To go live with the rehearsal's last clean slot as day one, follow
[Going live](#going-live) instead of the switch above.

## Going live

The rehearsal ends, and the daily run starts, with the last clean rehearsal
slot as day one's data: the same bytes, now a live day under
`reports/daily/<that slot's UTC day>/`. Everything below runs on the reporter
node from `~/loop` (on `main`), with `~/loop-data` beside it, in this order.

1. The confidence check, while the rehearsal still runs. `lab/lab.sh check`
   (on the lab host, with its settings as in `LAB-BRINGUP.md`) records the
   build the nodes run in `$LAB_DIR/build.json`; the reports themselves do not
   record it. The check reads loop-data's slots as the reporter pushed them:

   ```sh
   lab/lab.sh check
   python3 scripts/rehearsal_confidence.py ~/loop-data/reports/rehearsal --build-json "$LAB_DIR/build.json"
   ```

   It prints `CONFIDENT` (exit 0) when the newest 12 slots or more (two hours)
   are clean (every node in `nodes/`, the reporter's own node through the second
   console included, answered both questions verified under its listed key,
   under the current `versions.lock`), a MISSED slot comes before them and the
   slot after it is clean (the planted stop, see [The rehearsal](#the-rehearsal)),
   and `build.json` is the pinned build. Otherwise `NOT YET: <reason>`, naming
   the first slot that is not clean and why: keep the rehearsal running and
   check again. `--json` prints every slot's verdict.

2. Stop the rehearsal timer. A run under way finishes first; wait for
   `inactive`:

   ```sh
   systemctl --user disable --now loop-reporter-rehearsal.timer
   while [ "$(systemctl --user is-active loop-reporter-rehearsal.service)" = activating ]; do sleep 10; done
   systemctl --user is-active loop-reporter-rehearsal.service   # inactive
   ```

   A slot that run finished may not be clean; the next step takes the newest
   one that is.

3. Promote the last clean slot. It copies the slot into
   `reports/daily/<its UTC day>/` (each node's files byte for byte; only
   `RUN.json`, now `"label": "live"` and `"promoted_from": "rehearsal/<slot>"`,
   and `README.md`, now `# Daily report <day>` with no TEST, are rewritten),
   checks the daily set with the daily watchdog
   (`check_reports.py --through <day> --reporter reporter/reporter.json`), and
   commits only `reports/daily/<day>/` in `~/loop-data`. It refuses a slot that
   is not clean, a day that exists already, a daily set that fails the check,
   and any change outside `reports/daily/<day>/`. `--slot <YYYY-MM-DD>T<HHMM>Z`
   names a slot instead.

   ```sh
   python3 scripts/promote_rehearsal_slot.py --last-clean --data ~/loop-data --commit
   ```

4. Push, with the reporter's deploy key, as `run_daily.sh` does:

   ```sh
   git -C ~/loop-data log --stat origin/main..HEAD   # one commit, reports/daily/<day>/ only
   git -C ~/loop-data push origin HEAD:main
   ```

   loop-data's `reports` workflow (the daily watchdog) runs on that push. Promote
   on the slot's own UTC day: on a later day, today has no report yet and the
   watchdog reads red until step 6's run lands it.

5. Wipe the rehearsal: loop-data first, then this repository if needed, then
   the reporter node's copy, through the existing scripts (as in
   [The rehearsal](#the-rehearsal)):

   ```sh
   # loop-data, from a checkout that can open a pull request:
   git fetch origin && git switch -c wipe-rehearsal origin/main
   scripts/wipe_rehearsal.sh
   git push origin wipe-rehearsal          # open the pull request and merge it
   # loop, only if stats/data/rehearsal/ is still on main after publish-scrubbed ran:
   git fetch origin && git switch -c wipe-rehearsal origin/main
   scripts/wipe_rehearsal.sh
   git push origin wipe-rehearsal          # open the pull request and merge it
   # the reporter node:
   git -C ~/loop-data pull --rebase --quiet origin main && rm -rf ~/loop-data/reports/rehearsal
   ```

6. Install the daily units, with the drop-ins that give them the settings
   file (the second console; `reporter/systemd/reporter-env.conf`, copied as
   `env.conf` into each `.service.d`), and enable the daily run. The rehearsal step
   installed only `loop-reporter-rehearsal.*`; without the copy, `enable`
   fails with `Unit loop-reporter.timer not found`:

   ```sh
   cp ~/loop/reporter/systemd/loop-reporter.* ~/loop/reporter/systemd/loop-reporter-ask.* ~/.config/systemd/user/
   for unit in loop-reporter loop-reporter-ask; do
     mkdir -p ~/.config/systemd/user/$unit.service.d
     cp ~/loop/reporter/systemd/reporter-env.conf ~/.config/systemd/user/$unit.service.d/env.conf
   done
   systemctl --user daemon-reload
   systemctl --user enable --now loop-reporter.timer
   systemctl --user enable --now loop-reporter-ask.timer   # optional: asks again at 12:00, 18:00, 23:00 UTC
   systemctl --user list-timers 'loop-reporter*'   # loop-reporter.timer (and -ask if enabled); no rehearsal timer
   ```

   The first run is at the next 00:30 UTC. If the timer ran before
   (`Persistent=true`), enabling it may start a run at once: for day one's own
   day it refuses (`already holds a run`) and pushes nothing; for a later day it
   reports that day and marks any day between as MISSED.

Afterwards, once `publish-scrubbed` has run (06:30 UTC, or by hand with
`set: daily`), the stats page shows day one as a live day, settled, with each
node's answers, and no TEST label: the loop-data wipe published an empty
rehearsal set, which removed `stats/data/rehearsal/`, so the stats build makes
no rehearsal page and the daily page carries no TEST line.
